# Tests for the worker's schedule timezone handling.
#
# APScheduler defaults to Etc/UTC, so a cron of "*/5 9-16 * * MON-FRI" - which
# the UI labels "during market hours (9:30-16:00 ET)" - actually fires 5am to
# noon Eastern. That silently skipped the whole afternoon. The scheduler must
# be pinned to a market timezone.

import pytest
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from worker.scheduler import build_scheduler, MARKET_TZ

client = TestClient(app)


def admin_headers():
    resp = client.post("/auth/login", json={"password": "testpass"})
    return {"Authorization": f"Bearer {resp.json()['token']}"}


class TestScheduleTimezone:
    def test_scheduler_uses_market_timezone_not_utc(self):
        scheduler = build_scheduler()
        assert str(scheduler.timezone) == "America/New_York"

    def test_market_hours_cron_fires_in_eastern(self):
        """A 9-16 cron must resolve to 9am-4pm Eastern, not 9am-4pm UTC."""
        trigger = CronTrigger(
            hour="9-16",
            day_of_week="mon-fri",
            timezone=ZoneInfo("America/New_York")
        )

        # 15:00 UTC on a weekday is 11:00 EDT -> inside the window
        inside = datetime(2026, 9, 28, 15, 0, tzinfo=ZoneInfo("UTC"))
        # 20:00 UTC on a weekday is 16:00 EDT -> the afternoon close, inside
        afternoon = datetime(2026, 9, 28, 20, 0, tzinfo=ZoneInfo("UTC"))
        # 05:00 UTC on a weekday is 01:00 EDT -> the middle of the night
        middle_of_night = datetime(2026, 9, 28, 5, 0, tzinfo=ZoneInfo("UTC"))

        assert trigger.get_next_fire_time(None, inside) is not None
        assert trigger.get_next_fire_time(None, afternoon) is not None
        assert trigger.get_next_fire_time(None, middle_of_night) is not None

        # The decisive check: the afternoon must still be covered. Under UTC
        # scheduling, 16:00 ET (20:00 UTC) was outside the 9-16 window.
        fires = []
        t = datetime(2026, 9, 28, 12, 0, tzinfo=ZoneInfo("UTC"))
        for _ in range(200):
            t = trigger.get_next_fire_time(t, t)
            if t is None:
                break
            fires.append(t)
            if len(fires) > 400:
                break

        et_times = {f.astimezone(ZoneInfo("America/New_York")).hour for f in fires}
        # Must cover the full 09:00-16:00 Eastern range
        assert et_times == set(range(9, 17)), (
            f"expected hours 9-16 ET, got {sorted(et_times)}"
        )

    def test_default_cron_covers_entire_regular_session(self):
        """The shipped default must cover 09:00-16:00 ET, not 05:00-12:00 ET."""
        trigger = CronTrigger.from_crontab("*/5 9-16 * * MON-FRI", timezone=MARKET_TZ)

        fires = []
        t = datetime(2026, 9, 28, 0, 0, tzinfo=ZoneInfo("UTC"))
        for _ in range(500):
            t = trigger.get_next_fire_time(t, t)
            if t is None:
                break
            fires.append(t)

        et = ZoneInfo("America/New_York")
        first_day = fires[0].astimezone(et).date()
        day = [f.astimezone(et) for f in fires if f.astimezone(et).date() == first_day]

        first, last = day[0], day[-1]
        # Under a UTC default this schedule lands at 05:00-12:00 ET, so
        # these two assertions are exactly what catches the bug.
        assert (first.hour, first.minute) == (9, 0), (
            f"session starts at {first.hour}:{first.minute:02d} ET, expected 09:00"
        )
        assert (last.hour, last.minute) >= (16, 0), (
            f"session ends at {last.hour}:{last.minute:02d} ET, expected to reach "
            f"the 16:00 close - the afternoon is not covered"
        )
        # Every fire in the session must sit inside regular trading hours
        assert all(9 <= f.hour <= 16 for f in day)


class TestApiReportsTheTimezoneTheWorkerUses:
    """The Schedule page said "Timezone: UTC" and labelled the hour row
    "0-23 (UTC)".

    That was true of APScheduler's default and false of this app, which pins
    MARKET_TZ - so a cron written against the UI's stated timezone fired
    05:00-12:00 ET, which is the bug this file is about one layer up. It was
    invisible in the UI because the schedule *looked* configured correctly.

    Hardcoding "ET" in the frontend would not be a fix either: BOT_TIMEZONE is
    configurable, so the only value that cannot be wrong is the one the worker
    actually reads. Hence it is served.
    """

    def test_bot_config_reports_the_scheduler_timezone(self):
        resp = client.get("/bot/config", headers=admin_headers())
        assert resp.status_code == 200
        assert resp.json()["cron_timezone"] == str(MARKET_TZ)

    def test_the_reported_value_is_the_one_the_trigger_is_built_with(self):
        """Not just "a plausible-looking zone" - the same string the worker
        hands to CronTrigger. If these drift, the UI is confidently wrong."""
        reported = client.get("/bot/config", headers=admin_headers()).json()["cron_timezone"]
        assert reported == get_settings().bot_timezone
        assert ZoneInfo(reported) == MARKET_TZ

    def test_the_reported_value_is_a_resolvable_iana_zone(self):
        """A typo in BOT_TIMEZONE must not render as a plausible string in the
        UI while the worker blows up on every cycle."""
        reported = client.get("/bot/config", headers=admin_headers()).json()["cron_timezone"]
        assert "/" in reported
        assert ZoneInfo(reported) is not None

    def test_the_patch_response_reports_it_too(self):
        """SchedulePage reloads after saving. If only GET carried the field,
        the timezone would vanish from the screen the moment anyone edited
        the schedule."""
        resp = client.patch(
            "/bot/config", json={"market_hours_only": True}, headers=admin_headers()
        )
        assert resp.status_code == 200
        assert resp.json()["cron_timezone"] == str(MARKET_TZ)
