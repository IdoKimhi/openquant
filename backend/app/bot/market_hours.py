# Which part of the trading day are we in?
#
# Alpaca's `Clock.is_open` answers a coarser question than this app needs. It
# is true across the whole tradable window - pre-market from 04:00 ET,
# regular 09:30-16:00, after-hours to 20:00 - so a worker that gates on
# `is_open` alone places orders at 19:30 ET, and the position it opens then
# sits overnight with no liquidity behind it. That is what
# `bot_config.market_hours_only` exists to prevent, and it used to be read by
# nothing at all (gotcha 17).
#
# Two rules for this module:
#
#   * The broker decides. Holidays and early closes are not derivable from a
#     wall clock, so `is_open` is authoritative and the local time only
#     subdivides the window the broker says is open. Getting this backwards -
#     trusting the weekday - trades on Christmas.
#   * The broker's clock is the reference instant, not our host's. Alpaca's
#     `clock.timestamp` is offset-aware, which makes this function
#     deterministic in a test and immune to drift between this container and
#     the broker.
#
# The regular session boundaries are fixed calendar times in the market
# timezone, not in UTC. `*/5 9-16` in this app is 09:00-16:59 Eastern, which
# is why a 16:00 close produces a `closed` skip rather than an after-hours
# trade.

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from app.config import get_settings

# The zone cron expressions are written in, and the zone market times are
# quoted in. One definition, imported by the worker, so the schedule's zone
# and the trading-hours gate can never drift apart.
MARKET_TZ = ZoneInfo(get_settings().bot_timezone)

REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)

SESSION_REGULAR = "regular"
SESSION_PRE_MARKET = "pre_market"
SESSION_AFTER_HOURS = "after_hours"
SESSION_CLOSED = "closed"

SESSION_STATES = (SESSION_REGULAR, SESSION_PRE_MARKET, SESSION_AFTER_HOURS, SESSION_CLOSED)


def _aware(moment: datetime) -> datetime:
    """A datetime that can be converted between zones.

    Falls back to treating a naive value as UTC, which is what every column
    default in this app produces (`default=datetime.utcnow`).
    """
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment


def market_session_state(clock, now: datetime | None = None, tz=None) -> str:
    """Which session the broker is currently in.

    Returns one of SESSION_STATES. `now` and `tz` exist for tests and for the
    one caller that has a more precise instant than the clock; in normal
    operation both come from the clock itself.
    """
    tz = tz or MARKET_TZ
    moment = now if now is not None else getattr(clock, "timestamp", None)
    local = _aware(moment or datetime.now(timezone.utc)).astimezone(tz)

    # Authoritative, and deliberately first: this single check covers weekends,
    # the eleven US market holidays, and an early-close day once the early
    # close has passed (a 13:00 close is "closed" at 13:30 even though it is
    # 13:30 < 16:00 and still a weekday).
    if not getattr(clock, "is_open", False):
        return SESSION_CLOSED
    if local.weekday() >= 5:
        return SESSION_CLOSED

    wall = local.time()
    if wall < REGULAR_OPEN:
        return SESSION_PRE_MARKET
    if wall >= REGULAR_CLOSE:
        return SESSION_AFTER_HOURS
    return SESSION_REGULAR


def trading_allowed(session_state: str, market_hours_only: bool) -> bool:
    """Whether a cycle may place orders in this session.

    `market_hours_only` is the flag from bot_config, and the reason this is a
    function rather than an `if` in the worker is that the two answers are
    different questions: a closed market is never tradeable, while extended
    hours is a choice about whether to hold positions overnight.
    """
    if session_state == SESSION_CLOSED:
        return False
    if market_hours_only:
        return session_state == SESSION_REGULAR
    return True


def skip_reason(session_state: str, market_hours_only: bool) -> str:
    """Why a cycle was skipped, for the log line.

    Exists so the explanation is the same string the tests assert on. A worker
    that logs "Market is closed" for every non-regular session is how a
    schedule that never trades during extended hours looks identical to one
    that is broken, and that ambiguity has already cost this project one
    misdiagnosed bug (issue #3: after-hours trades that were a timezone
    misread, with a log line that could not tell the difference).
    """
    if session_state == SESSION_CLOSED:
        return "Market is closed (weekend, holiday or outside broker hours)"
    if market_hours_only and session_state == SESSION_PRE_MARKET:
        return "Pre-market only and market_hours_only is on; skipping cycle"
    if market_hours_only and session_state == SESSION_AFTER_HOURS:
        return "After-hours only and market_hours_only is on; skipping cycle"
    return ""
