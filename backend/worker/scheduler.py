# Worker Scheduler - APScheduler tick loop for running strategies

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session
from zoneinfo import ZoneInfo
import asyncio
import logging
import os
import signal
import sys
from datetime import datetime

from app.db import get_engine, get_session_local
from app.models import StrategyProfile, BotConfig, TradeLog, EquitySnapshot, OrderStatus
from app.bot.engine import run_strategy
from app.bot.risk import RiskManager, compute_daily_loss_pct
from app.alpaca_client import AlpacaClient
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Cron expressions are interpreted in this timezone. APScheduler otherwise
# defaults to Etc/UTC, which would run a "9-16" schedule 05:00-12:00 ET.
MARKET_TZ = ZoneInfo(settings.bot_timezone)


def build_scheduler() -> BackgroundScheduler:
    """Scheduler pinned to the market timezone."""
    return BackgroundScheduler(timezone=MARKET_TZ)


# Alpaca order statuses that mean "terminal, and it went through".
_FILLED = {"filled"}
# ...that mean "terminal, and it did not".
_NOT_FILLED = {"canceled", "expired", "replaced", "done_for_day"}
_REJECTED = {"rejected"}


def local_order_status(alpaca_status) -> OrderStatus:
    """Translate an Alpaca order status into this app's OrderStatus enum.

    Alpaca has 18 order statuses and only three of them (filled, canceled,
    rejected) overlap with ours. The rest - accepted, new, partially_filled,
    pending_new, ... - describe an order that is still in flight, which is
    exactly what our `submitted` value means. Note that Alpaca never sends
    `submitted`; it is our own lifecycle label.

    Everything unknown maps to `submitted` rather than raising: an unrecognised
    broker status should not be able to take down a trading cycle. The stored
    value has to be a real OrderStatus member because TradeLog.status is read
    back through the ORM (see app/models.py).

    Pass `order.status` straight in; both a bare string and an Alpaca enum
    member are accepted. The activity log deliberately does not preserve the
    broker's finer distinction between, say, `accepted` and `partially_filled` -
    GET /dashboard/orders reports the live status, and `alpaca_order_id` on the
    row is what links the two.
    """
    value = getattr(alpaca_status, "value", alpaca_status)

    if value in _FILLED:
        return OrderStatus.filled
    if value in _REJECTED:
        return OrderStatus.rejected
    if value in _NOT_FILLED:
        return OrderStatus.canceled
    return OrderStatus.submitted


class BotWorker:
    """Main worker class that runs the trading bot on schedule"""
    
    def __init__(self):
        self.scheduler = build_scheduler()
        self.db: Session = get_session_local()()
        self.running = False
        self._setup_signal_handlers()
    
    def _setup_signal_handlers(self):
        """Handle graceful shutdown"""
        signal.signal(signal.SIGTERM, self._shutdown)
        signal.signal(signal.SIGINT, self._shutdown)
    
    def _shutdown(self, signum, frame):
        logger.info(f"Received signal {signum}, shutting down...")
        self.stop()
        sys.exit(0)
    
    def start(self):
        """Start the scheduler with the configured cron job"""
        if self.running:
            logger.warning("Worker already running")
            return
        
        # Get bot config
        bot_config = self.db.query(BotConfig).first()
        if not bot_config:
            logger.error("No bot config found, cannot start worker")
            return
        
        if not bot_config.is_running:
            logger.info("Bot is not running (is_running=False), worker will not execute trades")
            # Still start scheduler to check for config changes
        else:
            # Schedule the trading job
            cron_expr = bot_config.schedule_cron or settings.bot_schedule_cron
            try:
                trigger = CronTrigger.from_crontab(cron_expr, timezone=MARKET_TZ)
                self.scheduler.add_job(
                    self._run_trading_cycle,
                    trigger=trigger,
                    id="trading_cycle",
                    replace_existing=True,
                    max_instances=1,
                    coalesce=True
                )
                logger.info(f"Scheduled trading cycle with cron: {cron_expr}")
            except Exception as e:
                logger.error(f"Invalid cron expression '{cron_expr}': {e}")
                return
        
        # Add a periodic config check job (every 30 seconds)
        self.scheduler.add_job(
            self._check_config_changes,
            "interval",
            seconds=30,
            id="config_check",
            replace_existing=True
        )
        
        self.scheduler.start()
        self.running = True
        logger.info("Bot worker started")
    
    def stop(self):
        """Stop the scheduler"""
        if not self.running:
            return
        
        self.scheduler.shutdown(wait=True)
        self.running = False
        logger.info("Bot worker stopped")
    
    def _check_config_changes(self):
        """Check for bot config changes and update schedule if needed"""
        try:
            # Refresh from database
            self.db.expire_all()
            bot_config = self.db.query(BotConfig).first()
            
            if not bot_config:
                return
            
            # Check if schedule changed
            current_job = self.scheduler.get_job("trading_cycle")
            cron_expr = bot_config.schedule_cron or settings.bot_schedule_cron
            
            if bot_config.is_running:
                if not current_job:
                    # Need to add job
                    try:
                        trigger = CronTrigger.from_crontab(cron_expr, timezone=MARKET_TZ)
                        self.scheduler.add_job(
                            self._run_trading_cycle,
                            trigger=trigger,
                            id="trading_cycle",
                            replace_existing=True,
                            max_instances=1,
                            coalesce=True
                        )
                        logger.info(f"Added trading cycle job with cron: {cron_expr}")
                    except Exception as e:
                        logger.error(f"Failed to add trading cycle job: {e}")
                else:
                    # Check if cron expression changed
                    # We can't easily compare triggers, so just reschedule
                    try:
                        trigger = CronTrigger.from_crontab(cron_expr, timezone=MARKET_TZ)
                        self.scheduler.reschedule_job("trading_cycle", trigger=trigger)
                    except Exception as e:
                        logger.error(f"Failed to reschedule trading cycle: {e}")
            else:
                # Bot stopped, remove trading job if exists
                if current_job:
                    self.scheduler.remove_job("trading_cycle")
                    logger.info("Removed trading cycle job (bot stopped)")
                    
        except Exception as e:
            logger.error(f"Error checking config changes: {e}")
    
    def _run_trading_cycle(self):
        """Execute one trading cycle: run active strategy, apply risk checks, place orders

        APScheduler's BackgroundScheduler runs jobs in a plain worker thread and
        does not await coroutines, so this stays synchronous and drives the real
        work through a single event loop in _run_trading_cycle_async. Calling
        the async AlpacaClient methods without awaiting returns a coroutine
        object, which fails on first attribute access.
        """
        logger.info("Starting trading cycle")
        try:
            asyncio.run(self._run_trading_cycle_async())
        except Exception as e:
            logger.error(f"Error in trading cycle: {e}", exc_info=True)

    async def _run_trading_cycle_async(self):
        try:
            # Get active profile and bot config
            bot_config = self.db.query(BotConfig).first()
            if not bot_config or not bot_config.is_running:
                logger.info("Bot not running, skipping cycle")
                return
            
            if not bot_config.active_profile_id:
                logger.warning("No active profile configured, skipping cycle")
                return
            
            profile = self.db.query(StrategyProfile).filter(
                StrategyProfile.id == bot_config.active_profile_id,
                StrategyProfile.enabled == True
            ).first()
            
            if not profile:
                logger.warning("Active profile not found or disabled, skipping cycle")
                return
            
            # Get Alpaca client with credentials
            alpaca = self._get_alpaca_client()
            if not alpaca:
                logger.error("Failed to create Alpaca client (no credentials)")
                return
            
            # Check market clock
            clock = await alpaca.get_clock()
            if not clock.is_open:
                logger.info("Market is closed, skipping cycle")
                return
            
            # Get account equity, plus the broker's own day P&L basis
            account = await alpaca.get_account()
            equity = float(account.equity)
            if equity <= 0:
                logger.warning("Equity is zero or negative, skipping cycle")
                return
            
            # Equity vs the previous close is the only trustworthy daily loss
            # figure: TradeLog.pnl is never written, so the local sum is
            # always zero and the loss limit could never trigger.
            last_equity = float(account.last_equity) if account.last_equity else None
            daily_loss_pct = compute_daily_loss_pct(equity=equity, last_equity=last_equity)
            if daily_loss_pct > 0:
                logger.warning(
                    f"Account is down {daily_loss_pct:.2%} from the previous close "
                    f"(equity {equity} vs last_equity {last_equity})"
                )
            
            # Get current positions from Alpaca
            positions = await alpaca.get_positions()
            current_position_symbols = {p.symbol for p in positions}
            current_positions_count = len(positions)
            
            # Run strategy to get signals
            signals = await run_strategy(profile, alpaca)
            if not signals:
                logger.info("No signals generated")
                await self._record_equity_snapshot(equity)
                return
            
            logger.info(f"Generated {len(signals)} signals")
            
            # Initialize risk manager
            risk_manager = RiskManager(self.db)
            
            # Process each signal
            for signal in signals:
                try:
                    await self._process_signal(
                        signal=signal,
                        profile=profile,
                        alpaca=alpaca,
                        equity=equity,
                        current_positions_count=current_positions_count,
                        current_position_symbols=current_position_symbols,
                        risk_manager=risk_manager,
                        daily_loss_pct=daily_loss_pct
                    )
                except Exception as e:
                    logger.error(f"Error processing signal for {signal.symbol}: {e}")
                    continue
            
            # Record equity snapshot
            await self._record_equity_snapshot(equity)
            
            logger.info("Trading cycle completed")
            
        except Exception as e:
            logger.error(f"Error in trading cycle: {e}", exc_info=True)
    
    def _get_alpaca_client(self) -> AlpacaClient | None:
        """Create Alpaca client with stored credentials"""
        from app.models import ApiCredentials
        from app.security import decrypt
        
        creds = self.db.query(ApiCredentials).first()
        if not creds:
            return None
        
        try:
            key_id = decrypt(creds.key_id_encrypted)
            secret_key = decrypt(creds.secret_key_encrypted)
            return AlpacaClient(api_key=key_id, secret_key=secret_key)
        except Exception as e:
            logger.error(f"Failed to decrypt credentials: {e}")
            return None
    
    async def _process_signal(
        self,
        signal,
        profile: StrategyProfile,
        alpaca: AlpacaClient,
        equity: float,
        current_positions_count: int,
        current_position_symbols: set,
        risk_manager: RiskManager,
        daily_loss_pct: float = 0.0
    ):
        """Process a single trading signal with risk checks"""
        symbol = signal.symbol
        side = signal.side
        qty = signal.qty
        
        # Get current price for risk checks
        current_price = signal.estimated_price
        if current_price <= 0:
            # Try to get latest price from Alpaca
            try:
                quote = await alpaca.get_latest_quote(symbol)
                current_price = quote.ask_price if side == "buy" else quote.bid_price
            except Exception:
                logger.warning(f"Could not get current price for {signal.symbol}")
                return
        
        # Risk validation
        result = risk_manager.validate_order(
            profile=profile,
            equity=equity,
            qty=qty,
            current_price=current_price,
            current_positions_count=current_positions_count,
            side=side,
            daily_loss_pct=daily_loss_pct
        )
        
        if not result.allowed:
            logger.warning(f"Risk check failed for {symbol} {side}: {result.reason}")
            return
        
        # Check if we already have a position in this symbol
        has_position = symbol in current_position_symbols
        
        # For sell signals, only proceed if we have a position to close
        if side == "sell" and not has_position:
            logger.info(f"No position in {symbol} to sell, skipping")
            return
        
        # For buy signals, check if we already have a position (avoid doubling)
        if side == "buy" and has_position:
            logger.info(f"Already have position in {symbol}, skipping buy")
            return
        
        # Place order
        try:
            order = await alpaca.submit_order(
                symbol=symbol,
                qty=qty,
                side=side,
                order_type=signal.order_type,
                limit_price=signal.limit_price
            )
            
            # Log the trade.
            #
            # Both fields below need converting, and getting either wrong
            # corrupts a trade that has already been placed:
            #
            #   status         order.status is one of Alpaca's 18 values, none
            #                  of which except filled/canceled/rejected exist in
            #                  our OrderStatus enum. Writing it raw used to
            #                  commit (SQLite has no CHECK here) and then poison
            #                  the row, so the Activity tab could not load it.
            #   alpaca_order_id  order.id is a uuid.UUID, which sqlite3 refuses
            #                  to bind at all. That raised ProgrammingError on
            #                  commit *after* the order was live at the broker,
            #                  so the except branch below logged "Order failed"
            #                  for an order that had really gone through, and
            #                  the in-cycle position bookkeeping was skipped.
            trade_log = TradeLog(
                profile_id=profile.id,
                symbol=symbol,
                side=side,
                qty=qty,
                order_type=signal.order_type,
                limit_price=signal.limit_price,
                status=local_order_status(order.status).value,
                alpaca_order_id=str(order.id),
                message=f"Order placed: {side} {qty} {symbol} @ {signal.order_type}"
            )
            self.db.add(trade_log)
            self.db.commit()
            
            logger.info(f"Order placed: {side} {qty} {symbol} (order_id: {order.id})")
            
            # Update position count for subsequent signals
            if side == "buy":
                current_positions_count += 1
                current_position_symbols.add(symbol)
            elif side == "sell":
                current_positions_count = max(0, current_positions_count - 1)
                current_position_symbols.discard(symbol)
                
        except Exception as e:
            logger.error(f"Failed to place order for {symbol}: {e}")
            # Log failed trade
            trade_log = TradeLog(
                profile_id=profile.id,
                symbol=symbol,
                side=side,
                qty=qty,
                order_type=signal.order_type,
                limit_price=signal.limit_price,
                status=OrderStatus.error.value,
                message=f"Order failed: {str(e)}"
            )
            self.db.add(trade_log)
            self.db.commit()
    
    async def _record_equity_snapshot(self, equity: float):
        """Record equity snapshot for equity curve"""
        try:
            alpaca = self._get_alpaca_client()
            if alpaca:
                account = await alpaca.get_account()
                snapshot = EquitySnapshot(
                    equity=equity,
                    cash=float(account.cash),
                    buying_power=float(account.buying_power),
                    day_pl=float(getattr(account, 'daytrade_count', 0) or 0),
                    total_pl=0.0  # Would need to calculate from positions
                )
                self.db.add(snapshot)
                self.db.commit()
        except Exception as e:
            logger.error(f"Failed to record equity snapshot: {e}")


def main():
    """Entry point for worker container"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    
    worker = BotWorker()
    worker.start()
    
    # Keep the main thread alive
    try:
        import time
        while worker.running:
            time.sleep(1)
    except KeyboardInterrupt:
        worker.stop()


if __name__ == "__main__":
    main()