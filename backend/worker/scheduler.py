# Worker Scheduler - APScheduler tick loop for running strategies

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session
import logging
import os
import signal
import sys
from datetime import datetime

from app.db import get_engine, get_session_local
from app.models import StrategyProfile, BotConfig, TradeLog, EquitySnapshot
from app.bot.engine import run_strategy
from app.bot.risk import RiskManager
from app.alpaca_client import AlpacaClient
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

class BotWorker:
    """Main worker class that runs the trading bot on schedule"""
    
    def __init__(self):
        self.scheduler = BackgroundScheduler()
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
                trigger = CronTrigger.from_crontab(cron_expr)
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
                        trigger = CronTrigger.from_crontab(cron_expr)
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
                        trigger = CronTrigger.from_crontab(cron_expr)
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
        """Execute one trading cycle: run active strategy, apply risk checks, place orders"""
        logger.info("Starting trading cycle")
        
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
            
            # Check market hours if configured
            if bot_config.market_hours_only:
                # We'll let Alpaca client handle market hours check
                pass
            
            # Get Alpaca client with credentials
            alpaca = self._get_alpaca_client()
            if not alpaca:
                logger.error("Failed to create Alpaca client (no credentials)")
                return
            
            # Check market clock
            clock = alpaca.get_clock()
            if not clock.is_open:
                logger.info("Market is closed, skipping cycle")
                return
            
            # Get account equity
            equity = alpaca.get_equity()
            if equity <= 0:
                logger.warning("Equity is zero or negative, skipping cycle")
                return
            
            # Get current positions from Alpaca
            positions = alpaca.get_positions()
            current_position_symbols = {p.symbol for p in positions}
            current_positions_count = len(positions)
            
            # Run strategy to get signals
            signals = run_strategy(profile, alpaca)
            if not signals:
                logger.info("No signals generated")
                self._record_equity_snapshot(equity)
                return
            
            logger.info(f"Generated {len(signals)} signals")
            
            # Initialize risk manager
            risk_manager = RiskManager(self.db)
            
            # Process each signal
            for signal in signals:
                try:
                    self._process_signal(
                        signal=signal,
                        profile=profile,
                        alpaca=alpaca,
                        equity=equity,
                        current_positions_count=current_positions_count,
                        current_position_symbols=current_position_symbols,
                        risk_manager=risk_manager
                    )
                except Exception as e:
                    logger.error(f"Error processing signal for {signal.symbol}: {e}")
                    continue
            
            # Record equity snapshot
            self._record_equity_snapshot(equity)
            
            logger.info("Trading cycle completed")
            
        except Exception as e:
            logger.error(f"Error in trading cycle: {e}", exc_info=True)
    
    def _get_alpaca_client(self) -> AlpacaClient | None:
        """Create Alpaca client with stored credentials"""
        from app.models import ApiCredentials
        from app.security import decrypt_value
        
        creds = self.db.query(ApiCredentials).first()
        if not creds:
            return None
        
        try:
            key_id = decrypt_value(creds.key_id_encrypted)
            secret_key = decrypt_value(creds.secret_key_encrypted)
            return AlpacaClient(key_id=key_id, secret_key=secret_key)
        except Exception as e:
            logger.error(f"Failed to decrypt credentials: {e}")
            return None
    
    def _process_signal(
        self,
        signal,
        profile: StrategyProfile,
        alpaca: AlpacaClient,
        equity: float,
        current_positions_count: int,
        current_position_symbols: set,
        risk_manager: RiskManager
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
                quote = alpaca.get_latest_quote(symbol)
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
            side=side
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
            order = alpaca.submit_order(
                symbol=symbol,
                qty=qty,
                side=side,
                order_type=signal.order_type,
                limit_price=signal.limit_price
            )
            
            # Log the trade
            trade_log = TradeLog(
                profile_id=profile.id,
                symbol=symbol,
                side=side,
                qty=qty,
                order_type=signal.order_type,
                limit_price=signal.limit_price,
                status=order.status,
                alpaca_order_id=order.id,
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
                status="error",
                message=f"Order failed: {str(e)}"
            )
            self.db.add(trade_log)
            self.db.commit()
    
    def _record_equity_snapshot(self, equity: float):
        """Record equity snapshot for equity curve"""
        try:
            alpaca = self._get_alpaca_client()
            if alpaca:
                account = alpaca.get_account()
                snapshot = EquitySnapshot(
                    equity=equity,
                    cash=account.cash,
                    buying_power=account.buying_power,
                    day_pl=float(account.daytrade_count) if hasattr(account, 'daytrade_count') else 0.0,
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