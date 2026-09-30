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
from datetime import datetime, timedelta

from app.db import get_engine, get_session_local, init_db
from app.models import StrategyProfile, BotConfig, TradeLog, EquitySnapshot, OrderStatus
from app.bot.engine import run_strategy
from app.bot.market_hours import (
    MARKET_TZ,
    market_session_state,
    skip_reason,
    trading_allowed,
)
from app.bot.risk import RiskManager, allocation_pct, compute_daily_loss_pct, size_qty
from app.alpaca_client import AlpacaClient
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# How far back the fill reconciler looks for unresolved orders. Alpaca keeps a
# bounded order history, so anything older than this is a guaranteed 404 on
# every pass; searching it anyway would mean the sweep grows without limit and
# its log fills with errors that are really just "expired", burying the ones
# that matter. A DAY order that has not filled within a trading day is gone.
RECONCILE_MAX_AGE_HOURS = 48


def build_scheduler() -> BackgroundScheduler:
    """Scheduler pinned to the market timezone.

    `MARKET_TZ` is defined once, in app/bot/market_hours.py, and imported here
    rather than rebuilt from settings. The schedule's zone and the zone the
    trading-hours gate judges the session in have to be the same zone, and two
    separate `ZoneInfo(settings.bot_timezone)` calls are two chances to
    disagree - which is how a cron and its market-hours gate end up four hours
    apart without either looking wrong.
    """
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


def _fill_from_order(order) -> tuple[float | None, float | None]:
    """(filled_price, filled_qty) from a broker order, or (None, None).

    The three SDK types meet here: `filled_avg_price` and `filled_qty` are
    `Decimal`, and passing one straight into a `Float` column commits fine but
    is the same class of boundary bug as the `uuid.UUID` order id (gotcha 5b).
    Coerced explicitly rather than relying on the driver.

    `None` is returned for an order with no fill *yet*, and that is a
    meaningful value rather than a missing one: the reconciler finds work by
    searching for NULLs, so writing 0.0 for an unfilled order would report a
    fill that never happened and mark the row as already reconciled.

    A partially filled order does return its partial. `partially_filled` is a
    real state with real execution behind it, and it is the state most easily
    misread as "nothing happened" - the order is still open, so the
    reconciler keeps it, but the partial is data already paid for.
    """
    price = getattr(order, "filled_avg_price", None)
    qty = getattr(order, "filled_qty", None)
    if price is None or qty is None:
        return None, None
    if float(qty) <= 0:
        return None, None
    return float(price), float(qty)


def binding_position_pct(profile: StrategyProfile) -> float:
    """The per-position cap that actually binds, per strategy-profile.

        min(risk_max_position_pct, position_size_pct)

    The same arithmetic as `StrategyProfile.max_deployable_pct`, factored out
    because a cycle log line wants to name the binding input without
    re-implementing it. It used to print `risk_max_position_pct` and let the
    reader assume it was the cap, which is exactly the class of wrong number
    that made issue #2's idle cash invisible in the first place: the ceiling
    the caps impose was nowhere to be seen.
    """
    position_size = float((profile.parameters or {}).get("position_size_pct", 0.0) or 0.0)
    cap = max(float(profile.risk_max_position_pct or 0.0), 0.0)
    return min(cap, position_size)


class BotWorker:
    """Main worker class that runs the trading bot on schedule"""
    
    def __init__(self):
        # The worker owns its schema upgrade. It has its own engine and its own
        # connection pool, and the backend's `init_db` - which is where the
        # upgrade lived until this - runs in a different container against a
        # different process. Relying on it means depending on a startup order
        # nobody guarantees: `depends_on: service_started` waits for the
        # container to spawn, not for the hook, and `docker compose restart
        # worker` starts the worker with nothing else running. The failure is
        # silent until the first query, then it is a crash loop on "no such
        # column: bot_config.<something added this release>".
        #
        # `init_db` is idempotent and is now called by both processes, so this
        # is two no-ops in the steady state, not two migrations.
        added = init_db()
        if added:
            logger.info(f"Worker added missing columns on startup: {', '.join(added)}")

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

        # Fill reconciliation (issue #5). Registered unconditionally, not under
        # the `is_running` branch above: an order placed on the last cycle
        # before a stop still fills, and the row still has to learn what it
        # cost. A reconciler gated on "the bot is currently running" would miss
        # exactly the orders most likely to still be in flight.
        self.scheduler.add_job(
            self._reconcile_fills_job,
            "interval",
            minutes=1,
            id="reconcile_fills",
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
            
            # Which window of the trading day is this?
            #
            # `clock.is_open` alone is not enough and never was. Alpaca reports
            # it true from 04:00 to 20:00 ET, so gating on it places orders in
            # pre-market and after-hours, and a position opened at 19:30 sits
            # overnight with no liquidity behind it. bot_config
            # .market_hours_only existed to prevent exactly that and was read
            # by nothing at all (gotcha 17); it is read here now.
            #
            # The broker's own timestamp is the reference instant, so this does
            # not depend on this container's clock agreeing with Alpaca's.
            broker_clock = await alpaca.get_clock()
            session_state = market_session_state(broker_clock)
            market_hours_only = bool(bot_config.market_hours_only)

            if not trading_allowed(session_state, market_hours_only):
                # Says *which* window, on purpose. "Market is closed" for
                # every non-regular session makes a correctly-idle bot
                # indistinguishable from a broken one - the ambiguity that made
                # issue #3's own evidence a timezone misread rather than the
                # after-hours trading it was reported as.
                logger.info(f"{skip_reason(session_state, market_hours_only)}, skipping cycle")
                return

            if session_state != "regular":
                logger.info(f"Trading in {session_state} (market_hours_only is off)")

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

            # The share of equity the strategies may size against (issue #6,
            # "money to invest"). Every risk limit is a fraction of equity, so
            # they are all evaluated against this same investable base - that is
            # what makes a 20% cash reserve actually reserve cash instead of
            # leaving the *limits* unchanged while shrinking the positions.
            investable_equity = RiskManager.investable_equity(
                equity, bot_config.capital_allocation_pct
            )
            logger.info(
                f"Equity {equity:,.2f}, allocating "
                f"{allocation_pct(bot_config.capital_allocation_pct):.0%} = "
                f"{investable_equity:,.2f} investable; profile can deploy at most "
                f"{profile.max_deployable_pct:.0%} of equity "
                f"({profile.risk_max_concurrent_positions} positions x "
                f"{binding_position_pct(profile):.0%} each)"
            )

            # Get current positions from Alpaca
            #
            # Symbol -> quantity, not a bare set of symbols. The quantity is not
            # a nicety: a sell's size *is* the holding (issue #9), and this line
            # used to throw `p.qty` away one character after fetching it, so
            # `_process_signal` had no way to size an exit and every strategy
            # fell back to sizing it like a buy - 22 consecutive broker
            # rejections against a 1-share position in the live log.
            #
            # It is a dict rather than a parallel set + dict deliberately. Two
            # structures tracking one fact drift the first time a sell is
            # partial, and "is this symbol held" and "how much of it" have to
            # answer consistently for the in-cycle bookkeeping to be right.
            positions = await alpaca.get_positions()
            current_positions: dict[str, float] = {
                p.symbol: float(p.qty) for p in positions
            }
            current_positions_count = len(positions)
            deployed_value = sum(abs(float(p.market_value)) for p in positions)

            # Run strategy to get signals. The investable base is passed in
            # rather than left to the strategy fetching its own equity, so every
            # strategy in the registry sizes the same way and the allocation
            # setting cannot be honoured by one strategy and ignored by another.
            signals = await run_strategy(profile, alpaca, investable_equity=investable_equity)
            if not signals:
                logger.info("No signals generated")
            else:
                logger.info(f"Generated {len(signals)} signals")

            # Initialize risk manager
            risk_manager = RiskManager(self.db)

            # Process each signal
            placed_value = 0.0
            for signal in signals:
                try:
                    placed_value += await self._process_signal(
                        signal=signal,
                        profile=profile,
                        alpaca=alpaca,
                        investable_equity=investable_equity,
                        current_positions_count=current_positions_count,
                        current_positions=current_positions,
                        risk_manager=risk_manager,
                        daily_loss_pct=daily_loss_pct
                    )
                except Exception as e:
                    logger.error(f"Error processing signal for {signal.symbol}: {e}")
                    continue

            # Idle cash, deployed by an explicit opt-in. Runs after the signal
            # loop rather than inside it, so it never competes with a real
            # signal for the concurrency budget and a sweep that fails cannot
            # take a signal down with it.
            #
            # Reached on the no-signal path too, deliberately. A sweep exists
            # for the quiet week - the one where the strategy found nothing and
            # the cash would otherwise sit there indefinitely. Returning early
            # when `signals` is empty would make it fire precisely when it is
            # least wanted, which is the whole reason for it.
            await self._maybe_sweep_cash(
                profile=profile,
                alpaca=alpaca,
                risk_manager=risk_manager,
                investable_equity=investable_equity,
                # This cycle's orders have spent cash the sweep was about to
                # treat as spare. The count and quantities were already being
                # updated in place by _process_signal; the *value* was not,
                # and it is the value the sweep subtracts - see the returned
                # notional from _process_signal.
                deployed_value=deployed_value + placed_value,
                current_positions_count=current_positions_count,
                current_positions=current_positions,
                daily_loss_pct=daily_loss_pct,
            )

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
        investable_equity: float,
        current_positions_count: int,
        current_positions: dict,
        risk_manager: RiskManager,
        daily_loss_pct: float = 0.0
    ):
        """Process a single trading signal with risk checks.

        Notification: this is the BUY-ONLY gate it looks like. `validate_order`
        runs every check in the buy branch (see risk.py) - the position cap
        among them - so `investable_equity` is deliberately *not* optional
        here. The cap is the buyer of "one base for sizing and limits": the
        amount the order is measured against is precisely the 80% the operator
        asked to be investable.

        There is deliberately no `equity` parameter. The account total plays no
        part in what may be ordered, and an unused `equity` sitting beside
        `investable_equity` was precisely how this call site regressed once -
        someone read the signature, concluded "the cap is measured against the
        account", and handed `validate_order` the wrong base. The bug is not
        the mistake; it is the signature that invites it.

        Returns the notional value placed (`qty * price`), or 0.0 when nothing
        was placed - refused by a risk check, already holding the symbol, or
        the broker rejected the order. The cycle sums this to know what the
        cash sweep can still spend; without it the sweep would size itself
        against the book as it was *before* this cycle's orders, and could
        announce an order it then refused.

        `current_positions` is symbol -> quantity and is mutated in place, so it
        is the cycle's live book rather than a snapshot taken before it. The
        quantity is load-bearing: it is what a sell is sized against (see the
        clamp below), and a sell signal's own `qty` is not trusted for that.
        """
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
                # 0.0, not a bare `return`. The cycle does
                # `placed_value += await self._process_signal(...)`, so a None
                # here raised TypeError inside the signal loop, which the loop's
                # `except Exception` swallowed into a "Error processing signal"
                # log - dropping the signal and skipping the cash sweep for the
                # cycle, over a quote lookup that failed. The docstring promises
                # 0.0 for "nothing was placed" and that has to include "could not
                # even price it".
                return 0.0

        # A sell's size is the position, not a share of equity.
        #
        # Every strategy sizes a sell exactly as it sizes a buy - `qty =
        # size_qty(budget, price, allow_fractional)`, where budget is
        # position_size_pct x equity. All three carry a comment saying
        # "flatten position" and none of them can honour it: `generate_signals`
        # is handed symbol strings and never learns a holding size, so that
        # line computes how many shares a *fresh* allocation would buy. The two
        # numbers are unrelated by construction, and when the fresh allocation
        # is the larger one the broker refuses the order outright - 22
        # consecutive `40310000 insufficient qty available` rejections in the
        # live log, one every half hour, none of which could ever succeed while
        # the holding stayed put (issue #9).
        #
        # Worse, it rots. The budget grows with the account and the holding does
        # not, so a position bought when equity was lower becomes permanently
        # un-exitable as the account earns. Three of seven live positions were
        # already stuck that way, one by 0.02 shares. That defeats the risk
        # design from outside the risk layer: `validate_order` always permits
        # sells specifically so a breach can never trap a position.
        #
        # The clamp is a ceiling, not a substitution. A signal smaller than the
        # holding closes that much and leaves the rest standing.
        #
        # This runs *before* `validate_order` so the risk layer approves the
        # quantity that will actually be sent. Handing it a number the broker
        # was always going to refuse is a check reasoning about a trade that
        # does not exist.
        if side == "sell":
            held = current_positions.get(symbol, 0.0)
            if held <= 0:
                logger.info(f"No position in {symbol} to sell, skipping")
                return 0.0

            qty = min(qty, held)

            # `size_qty` already floors when the profile is whole-share, so
            # this is normally a no-op - the signal's quantity is whole by then.
            # It earns its place when a position was opened under fractional
            # sizing and is being closed after the flag went off, which is
            # exactly the state COST is in on the live account (1.29 held, 1.31
            # requested).
            #
            # Floored, never rounded up. A sub-share holding becomes 0 shares,
            # which is not an order, and rounding up instead would be a short
            # sale this code is not entitled to open. Both are handled below.
            if not profile.allow_fractional_shares:
                qty = float(int(qty))

            if qty <= 0:
                logger.info(
                    f"Position in {symbol} is {held} shares, which the "
                    f"whole-share profile cannot sell; skipping"
                )
                return 0.0

        # Risk validation. The base passed is the *investable* one, so the
        # position cap is measured against allocation x equity, agreeing with
        # what the strategies sized against. Sizes and limits on two different
        # bases made a true cap into a notional one (see test_risk_equity_base).
        result = risk_manager.validate_order(
            profile=profile,
            equity=investable_equity,
            qty=qty,
            current_price=current_price,
            current_positions_count=current_positions_count,
            side=side,
            daily_loss_pct=daily_loss_pct
        )

        if not result.allowed:
            logger.warning(f"Risk check failed for {symbol} {side}: {result.reason}")
            return 0.0

        # For buy signals, check if we already have a position (avoid doubling).
        # The sell half of this check moved above, into the clamp: it needs the
        # quantity, and a `symbol in <set>` answer cannot supply it.
        if side == "buy" and symbol in current_positions:
            logger.info(f"Already have position in {symbol}, skipping buy")
            return 0.0
        
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
            # All three fields below need converting, and getting any of them
            # wrong corrupts a trade that has already been placed:
            #
            #   status           order.status is one of Alpaca's 18 values, none
            #                    of which except filled/canceled/rejected exist
            #                    in our OrderStatus enum. Writing it raw used to
            #                    commit (SQLite has no CHECK here) and then poison
            #                    the row, so the Activity tab could not load it.
            #   alpaca_order_id  order.id is a uuid.UUID, which sqlite3 refuses
            #                    to bind at all. That raised ProgrammingError on
            #                    commit *after* the order was live at the broker,
            #                    so the except branch below logged "Order failed"
            #                    for an order that had really gone through, and
            #                    the in-cycle position bookkeeping was skipped.
            #   filled_*         Decimal, same coercion rule. Left out, these
            #                    stayed NULL on every row ever written (issue
            #                    #5) and nothing in the app noticed, because the
            #                    columns had always been nullable.
            #
            # The fill is captured here when the broker has already filled -
            # Alpaca frequently returns a filled market order straight from
            # submit - and reconciled on a timer when it has not. Submit-time
            # capture alone would fix the common case and silently leave the
            # rest, which is the failure mode this comment exists to prevent.
            filled_price, filled_qty = _fill_from_order(order)
            trade_log = TradeLog(
                profile_id=profile.id,
                symbol=symbol,
                side=side,
                qty=qty,
                order_type=signal.order_type,
                limit_price=signal.limit_price,
                status=local_order_status(order.status).value,
                alpaca_order_id=str(order.id),
                filled_price=filled_price,
                filled_qty=filled_qty,
                message=f"Order placed: {side} {qty} {symbol} @ {signal.order_type}"
            )
            self.db.add(trade_log)
            self.db.commit()
            
            logger.info(f"Order placed: {side} {qty} {symbol} (order_id: {order.id})")
            
            # Update the position book for subsequent signals in this cycle.
            #
            # A sell used to be an unconditional `discard`, which is only true
            # of a *full* close. Nothing in the old code could tell the
            # difference, because nothing knew the size: a partial sell made
            # the bot forget a position it still held, and a later signal in
            # the same cycle was then free to open a second one in that
            # symbol. The book is read by the very next iteration, so a wrong
            # answer here is a wrong answer for the rest of the cycle.
            #
            # Rounded before comparing rather than tested with an epsilon: a
            # residue of 1e-16 left over from `held - qty` would keep the symbol
            # in the book for a position that is gone, and `symbol in
            # current_positions` would refuse the next buy in that symbol.
            if side == "buy":
                current_positions_count += 1
                current_positions[symbol] = qty
            elif side == "sell":
                remaining = round(current_positions.get(symbol, 0.0) - qty, 6)
                if remaining > 0:
                    current_positions[symbol] = remaining
                else:
                    current_positions_count = max(0, current_positions_count - 1)
                    current_positions.pop(symbol, None)

            # The notional this cycle added to the book, so the cash sweep
            # measures "spare" against the book as it now is, not as it was
            # at the top of the cycle.
            return qty * current_price
                
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
            return 0.0
    
    async def _maybe_sweep_cash(
        self,
        profile: StrategyProfile,
        alpaca: AlpacaClient,
        risk_manager: RiskManager,
        investable_equity: float,
        deployed_value: float,
        current_positions_count: int,
        current_positions: dict,
        daily_loss_pct: float,
    ) -> None:
        """Put a bounded slice of idle cash into a broad instrument.

        Issue #2's other half. A strategy trades on a signal, so a week in
        which it finds nothing leaves the account's cash uninvested - and the
        longer the quiet stretch, the more of the account is parked. The sweep
        is a second, deliberately dumb strategy for exactly that window: buy
        SPY (or whatever the operator picked) with a slice of the spare cash.

        The cycle calls this *after* the signal loop, whether or not a signal
        was found - the quiet week is the whole point, so gating it on "no
        signal" would make it fire precisely when the strategy was busy. A
        cycle that did place signals tightens the sweep automatically: the
        caller passes `deployed_value` including this cycle's placements
        (the sum of the notional `_process_signal` reports), so the sweep
        measures spare against the book as it is, not as it was.

        It is routed through `_process_signal` rather than calling
        `submit_order` directly, so the sweep is subject to the same risk
        checks, the same "already hold this" guard and the same log write as a
        strategy signal. A second order path is a second set of rules, and the
        rules are the whole reason this is safe. That is also why it returns
        nothing: what it placed (or did not, and why) is recorded by the order
        path itself, and the log line here reports the *result*, not the
        intention.

        Four conditions, all of which have to hold:

        * **Opt-in.** `cash_sweep.enabled` in the profile's parameters, off by
          default. SPY is a position like any other and the operator should
          have to ask for it.
        * **A real pct.** A block with a symbol and no percentage is an
          unfinished setting, and reading it as "sweep everything spare" would
          open with whatever accumulated over a quiet month.
        * **Cash actually spare.** Measured as the gap between what is deployed
          and what may be deployed - `investable_equity - deployed_value` - so
          a full book sweeps nothing. Note it is the *investable* base, not
          full equity: with a 20% cash reserve configured, that reserve is not
          available to sweep and this is what enforces it.
        * **Inside the per-position cap.** `min(sweep_pct, risk_max_position_pct)`
          so a generous sweep pct cannot quietly open a larger position than
          the profile's own limit allows.
        """
        if not profile.cash_sweep_enabled:
            return

        symbol = profile.cash_sweep_symbol
        sweep_pct = profile.cash_sweep_pct
        if not symbol or sweep_pct <= 0:
            return

        spare = investable_equity - deployed_value
        if spare <= 0:
            logger.debug("Cash sweep skipped: nothing spare to deploy")
            return

        # The per-position cap wins, exactly as it does in max_deployable_pct.
        budget = min(investable_equity * sweep_pct, spare,
                     investable_equity * float(profile.risk_max_position_pct or 0.0))
        if budget <= 0:
            return

        try:
            quote = await alpaca.get_latest_quote(symbol)
            price = float(quote.ask_price)
        except Exception as e:
            logger.warning(f"Could not get a quote for cash sweep symbol {symbol}: {e}")
            return

        # Whole shares unless the profile opted into fractional sizing - the
        # same rounding as every other position, so a sweep can never be the
        # one order that breaches a cap by a fraction of a share.
        qty = size_qty(budget, price, allow_fractional=bool(profile.allow_fractional_shares))
        if qty <= 0:
            logger.debug(
                f"Cash sweep skipped: {budget:,.2f} will not buy a share of {symbol} "
                f"at {price}"
            )
            return

        from app.bot.strategies.base import Signal

        placed = await self._process_signal(
            signal=Signal(symbol=symbol, side="buy", qty=qty,
                          order_type="market", estimated_price=price),
            profile=profile,
            alpaca=alpaca,
            investable_equity=investable_equity,
            current_positions_count=current_positions_count,
            current_positions=current_positions,
            risk_manager=risk_manager,
            daily_loss_pct=daily_loss_pct,
        )

        # Logged from what actually happened, not from what was intended.
        # The old ordering logged the sweep *before* _process_signal ran its
        # checks, so a sweep that the concurrency cap or the per-position cap
        # then refused was still announced as bought - the mirror image of
        # gotcha 10, where a trade that happened was logged as a failure.
        # Either way the log reports a trade that did not occur.
        if placed and placed > 0:
            logger.info(
                f"Cash sweep: bought {qty} {symbol} (~{placed:,.2f}) from "
                f"{budget:,.2f} of {spare:,.2f} spare ({sweep_pct:.0%} of investable)"
            )
        else:
            logger.info(
                f"Cash sweep skipped for {symbol}: {qty} shares at ~{qty * price:,.2f} "
                f"(spare {spare:,.2f}) - nothing was placed. The reason was logged "
                f"just above by the order path."
            )

    def _reconcile_fills_job(self):
        """Scheduled entry point for fill reconciliation.

        Sync, like `_run_trading_cycle`: APScheduler runs jobs in a plain worker
        thread and does not await coroutines, so an `async def` here would
        create a coroutine and discard it (gotcha 9). One event loop per tick.
        """
        try:
            asyncio.run(self._reconcile_fills())
        except Exception as e:
            # A bookkeeping read must never be able to kill the worker. This
            # runs on its own timer, so an escaping exception would take out
            # the trading cycle and the config poller with it.
            logger.error(f"Fill reconciliation failed: {e}", exc_info=True)

    async def _reconcile_fills(self, alpaca=None):
        """Fill in `filled_price`/`filled_qty` on orders that have since filled.

        Issue #5. The row is written the moment `submit_order` returns, and at
        that moment a market order is usually `accepted` with no fill data - so
        the fill lands at the broker a moment later and nothing ever went back
        to look. Every one of the 52 rows in the live table is NULL on both
        fields, and all 52 are `submitted`, which is what made this look like
        the bot had never traded at all rather than a bot that cannot see its
        own executions.

        Three things bound the query, and all three are load-bearing:

        * **Recent only.** Alpaca's order history is a bounded window, so an
          unbounded "every row with a NULL fill" search re-checks rows whose
          orders expired from the broker weeks ago. Every one of those is a
          permanent 404, on every tick, forever.
        * **Not cancelled, rejected or already-filled rows.** A row in a
          terminal non-filled state will never fill; reconciling it forever is
          pure waste, and `filled_price` staying NULL is the correct record of
          "this never executed".
        * **One row at a time, and one failure is contained.** The broker is
          asked per order rather than for a batch, so a single 404 cannot take
          the rest of the sweep down with it.

        One commit for the whole sweep, not one per row: the same SQLite write
        lock that serialises the worker against the backend (gotcha 18) is not
        something to take once per order.
        """
        SessionLocal = get_session_local()
        db = SessionLocal()
        try:
            cutoff = datetime.utcnow() - timedelta(hours=RECONCILE_MAX_AGE_HOURS)
            rows = db.query(TradeLog).filter(
                TradeLog.alpaca_order_id.isnot(None),
                TradeLog.filled_qty.is_(None),
                TradeLog.status == OrderStatus.submitted.value,
                TradeLog.timestamp >= cutoff,
            ).order_by(TradeLog.id).all()

            if not rows:
                return

            alpaca = alpaca or self._get_alpaca_client()
            if not alpaca:
                return

            updated = 0
            for row in rows:
                try:
                    order = await alpaca.get_order(row.alpaca_order_id)
                except Exception as e:
                    # Expected, not exceptional: the order expired from the
                    # broker's history. Logged at debug so a genuinely broken
                    # key is still findable without filling the log with noise
                    # that is actually normal operation.
                    logger.debug(
                        f"Could not re-read order {row.alpaca_order_id} "
                        f"({row.symbol}): {e}"
                    )
                    continue

                price, qty = _fill_from_order(order)
                if qty is None:
                    continue  # still open; keep it on the list

                row.filled_price = price
                row.filled_qty = qty
                row.status = local_order_status(order.status).value
                updated += 1

            if updated:
                db.commit()
                logger.info(f"Reconciled fills on {updated} order(s)")
        except Exception as e:
            db.rollback()
            logger.error(f"Fill reconciliation error: {e}", exc_info=True)
        finally:
            db.close()

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