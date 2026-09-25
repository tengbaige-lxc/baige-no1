"""Read-only calendar and post-release interpreter for scheduled US macro events.

The radar deliberately stops before execution.  Calendar data identifies when a
release matters; an economic classification explains the surprise; the first
minute market reaction decides whether the thesis is actionable.  Strategy
code may consume the result later, but this module never imports an exchange
client and cannot place orders.
"""

from __future__ import annotations

import asyncio
import html
import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx


NEW_YORK = ZoneInfo("America/New_York")
BLS_CALENDAR_URL = "https://www.bls.gov/schedule/news_release/bls.ics"
FOMC_CALENDAR_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
DEFAULT_EVENT_CONTEXT_PATH = Path("/root/baige-no3/backend/data/macro_event_context.json")
HTTP_HEADERS = {
    "User-Agent": "baige-no3-macro-event-radar/1.0 contact=admin@example.invalid",
    "Accept": "text/calendar,text/html;q=0.9,*/*;q=0.1",
}

BLS_EVENT_TYPES = {
    "Employment Situation": "nfp",
    "Consumer Price Index": "cpi",
    "Producer Price Index": "ppi",
    "Job Openings and Labor Turnover Survey": "jolts",
}

MONTH_NUMBERS = {
    "January": 1,
    "February": 2,
    "March": 3,
    "April": 4,
    "May": 5,
    "June": 6,
    "July": 7,
    "August": 8,
    "September": 9,
    "October": 10,
    "November": 11,
    "December": 12,
}


@dataclass(frozen=True)
class ScheduledMacroEvent:
    event_id: str
    event_type: str
    title: str
    scheduled_at: datetime
    source: str
    importance: str = "high"
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["scheduled_at"] = self.scheduled_at.isoformat()
        return data


@dataclass(frozen=True)
class CalendarSnapshot:
    events: tuple[ScheduledMacroEvent, ...]
    source_errors: tuple[str, ...] = ()
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "fetched_at": self.fetched_at.isoformat(),
            "events": [event.to_dict() for event in self.events],
            "source_errors": list(self.source_errors),
        }


@dataclass(frozen=True)
class NfpRelease:
    """All payroll fields are in their public release units.

    Payroll surprise and revision are measured in thousands of jobs.  Wage and
    unemployment values are percentage points, e.g. 0.3 and 4.2.
    """

    payroll_actual_k: float | None = None
    payroll_consensus_k: float | None = None
    unemployment_actual_pct: float | None = None
    unemployment_consensus_pct: float | None = None
    avg_hourly_earnings_actual_pct: float | None = None
    avg_hourly_earnings_consensus_pct: float | None = None
    payroll_revision_k: float | None = None


@dataclass(frozen=True)
class EventAssessment:
    event_type: str
    regime: str
    bias: str
    confidence: float
    reason: str
    blockers: tuple[str, ...]
    requires_market_confirmation: bool = True

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["blockers"] = list(self.blockers)
        return data


@dataclass(frozen=True)
class MarketReaction:
    """Observed reaction after a release, not a forecast.

    index and leader returns are percent moves from immediately pre-release
    reference prices.  Yield movement is in basis points.
    """

    elapsed_seconds: int
    index_return_pct: float
    leader_return_pct: float
    ten_year_yield_change_bps: float | None
    dxy_return_pct: float | None
    volume_ratio: float


@dataclass(frozen=True)
class ReactionDecision:
    status: str
    direction: str | None
    reason: str
    blockers: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["blockers"] = list(self.blockers)
        return data


def build_confirmed_event_context(
    event_id: str,
    assessment: EventAssessment,
    decision: ReactionDecision,
    *,
    observed_at: datetime | None = None,
    active_minutes: int = 30,
) -> dict[str, Any]:
    """Create the short-lived context consumed by the TradFi score overlay.

    Only a confirmed candidate can become a context.  The context is not an
    order and expires rapidly so a scheduled-event thesis cannot affect later
    normal scans.
    """
    if decision.status != "CONFIRMED_CANDIDATE" or decision.direction not in {"LONG", "SHORT"}:
        raise ValueError("event context requires a confirmed market reaction")
    expected_direction = {
        "TECH_LONG_CANDIDATE": "LONG",
        "TECH_SHORT_CANDIDATE": "SHORT",
    }.get(assessment.bias)
    if decision.direction != expected_direction:
        raise ValueError("event assessment and market reaction directions disagree")
    observed_at = observed_at or datetime.now(timezone.utc)
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    active_minutes = max(5, min(120, int(active_minutes)))
    expires_at = observed_at.astimezone(timezone.utc) + timedelta(minutes=active_minutes)
    return {
        "schema_version": 1,
        "status": "CONFIRMED_CANDIDATE",
        "event_id": str(event_id),
        "event_type": assessment.event_type,
        "direction": decision.direction,
        "regime": assessment.regime,
        "observed_at": observed_at.astimezone(timezone.utc).isoformat(),
        "expires_at": expires_at.isoformat(),
        "assessment": assessment.to_dict(),
        "market_confirmation": decision.to_dict(),
    }


def write_event_context(context: dict[str, Any], path: Path | str = DEFAULT_EVENT_CONTEXT_PATH) -> None:
    """Atomically publish an already-validated, short-lived event context."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(context, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.replace(temporary, target)


def load_active_event_context(
    path: Path | str = DEFAULT_EVENT_CONTEXT_PATH,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Fail closed on missing, malformed, unconfirmed, or expired contexts."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("schema_version") != 1:
            return None
        if raw.get("status") != "CONFIRMED_CANDIDATE" or raw.get("direction") not in {"LONG", "SHORT"}:
            return None
        expires_at = datetime.fromisoformat(str(raw["expires_at"]).replace("Z", "+00:00"))
        if expires_at.tzinfo is None:
            return None
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if now.astimezone(timezone.utc) >= expires_at.astimezone(timezone.utc):
        return None
    return raw


def _unfold_ics_lines(content: str) -> list[str]:
    unfolded: list[str] = []
    for line in content.replace("\r\n", "\n").split("\n"):
        if line.startswith((" ", "\t")) and unfolded:
            unfolded[-1] += line[1:]
        else:
            unfolded.append(line)
    return unfolded


def _parse_ics_datetime(field: str, value: str) -> datetime | None:
    try:
        normalized = value.rstrip("Z")
        parsed = datetime.strptime(normalized, "%Y%m%dT%H%M%S")
    except ValueError:
        return None
    if value.endswith("Z"):
        return parsed.replace(tzinfo=timezone.utc)
    if "TZID=US-Eastern" in field or "TZID=America/New_York" in field:
        return parsed.replace(tzinfo=NEW_YORK)
    return parsed.replace(tzinfo=timezone.utc)


def parse_bls_calendar(content: str) -> list[ScheduledMacroEvent]:
    """Extract only high-impact BLS releases from the official ICS feed."""
    events: list[ScheduledMacroEvent] = []
    fields: dict[str, tuple[str, str]] = {}
    inside_event = False
    for line in _unfold_ics_lines(content):
        if line == "BEGIN:VEVENT":
            inside_event = True
            fields = {}
            continue
        if line == "END:VEVENT":
            if inside_event:
                summary = fields.get("SUMMARY", ("", ""))[1]
                event_type = BLS_EVENT_TYPES.get(summary)
                dt_field, dt_value = fields.get("DTSTART", ("", ""))
                scheduled_at = _parse_ics_datetime(dt_field, dt_value)
                uid = fields.get("UID", ("", ""))[1]
                if event_type and scheduled_at and uid:
                    events.append(
                        ScheduledMacroEvent(
                            event_id=f"bls:{uid}",
                            event_type=event_type,
                            title=summary,
                            scheduled_at=scheduled_at,
                            source="BLS calendar",
                        )
                    )
            inside_event = False
            fields = {}
            continue
        if not inside_event or ":" not in line:
            continue
        field, value = line.split(":", 1)
        key = field.split(";", 1)[0]
        fields[key] = (field, value.replace("\\,", ",").replace("\\n", " "))
    return events


def parse_fomc_calendar(content: str) -> list[ScheduledMacroEvent]:
    """Parse official FOMC meeting pages into the decision-release day.

    Regular statements are normally released at 14:00 New York time on the
    final meeting day.  The result is a calendar reminder, not a claim that
    every future special meeting shares that release time.
    """
    events: list[ScheduledMacroEvent] = []
    panel_pattern = re.compile(
        r'<h4><a id="[^\"]+">(?P<year>20\d{2}) FOMC Meetings</a></h4>(?P<body>.*?)(?=<div class="panel panel-default"|\Z)',
        re.DOTALL,
    )
    meeting_pattern = re.compile(
        r'<div class="[^\"]*fomc-meeting__month[^\"]*"><strong>(?P<month>[A-Za-z]+)</strong></div>.*?'
        r'<div class="[^\"]*fomc-meeting__date[^\"]*">(?P<dates>[^<]+)</div>',
        re.DOTALL,
    )
    for panel in panel_pattern.finditer(content):
        year = int(panel.group("year"))
        for meeting in meeting_pattern.finditer(panel.group("body")):
            month = MONTH_NUMBERS.get(html.unescape(meeting.group("month")).strip())
            day_numbers = re.findall(r"\d+", html.unescape(meeting.group("dates")))
            if not month or not day_numbers:
                continue
            decision_day = int(day_numbers[-1])
            try:
                scheduled_at = datetime(year, month, decision_day, 14, 0, tzinfo=NEW_YORK)
            except ValueError:
                continue
            date_label = "-".join(day_numbers)
            events.append(
                ScheduledMacroEvent(
                    event_id=f"fomc:{year}-{month:02d}-{decision_day:02d}",
                    event_type="fomc",
                    title=f"FOMC decision ({date_label} {meeting.group('month').strip()})",
                    scheduled_at=scheduled_at,
                    source="Federal Reserve calendar",
                    notes="Regular decision-time reminder; verify exceptional timing before use.",
                )
            )
    return events


class MacroEventRadar:
    """Fetch calendars and interpret scheduled event releases without execution."""

    async def fetch_calendar(self, days: int = 21, now: datetime | None = None) -> CalendarSnapshot:
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        end_at = now + timedelta(days=max(1, days))
        source_errors: list[str] = []
        async with httpx.AsyncClient(headers=HTTP_HEADERS, timeout=15.0, follow_redirects=True) as client:
            bls_task = client.get(BLS_CALENDAR_URL)
            fomc_task = client.get(FOMC_CALENDAR_URL)
            bls_result, fomc_result = await asyncio.gather(bls_task, fomc_task, return_exceptions=True)

        events: list[ScheduledMacroEvent] = []
        if isinstance(bls_result, Exception):
            source_errors.append(f"BLS fetch failed: {type(bls_result).__name__}")
        else:
            try:
                bls_result.raise_for_status()
                events.extend(parse_bls_calendar(bls_result.text))
            except Exception as exc:
                source_errors.append(f"BLS parse failed: {type(exc).__name__}")
        if isinstance(fomc_result, Exception):
            source_errors.append(f"FOMC fetch failed: {type(fomc_result).__name__}")
        else:
            try:
                fomc_result.raise_for_status()
                events.extend(parse_fomc_calendar(fomc_result.text))
            except Exception as exc:
                source_errors.append(f"FOMC parse failed: {type(exc).__name__}")

        upcoming = [
            event for event in events
            if now <= event.scheduled_at.astimezone(timezone.utc) <= end_at
        ]
        upcoming.sort(key=lambda event: event.scheduled_at)
        return CalendarSnapshot(tuple(upcoming), tuple(source_errors), datetime.now(timezone.utc))

    @staticmethod
    def assess_nfp(release: NfpRelease) -> EventAssessment:
        required = {
            "payroll actual": release.payroll_actual_k,
            "payroll consensus": release.payroll_consensus_k,
            "unemployment actual": release.unemployment_actual_pct,
            "unemployment consensus": release.unemployment_consensus_pct,
            "wage actual": release.avg_hourly_earnings_actual_pct,
            "wage consensus": release.avg_hourly_earnings_consensus_pct,
        }
        missing = tuple(name for name, value in required.items() if value is None)
        if missing:
            return EventAssessment(
                event_type="nfp",
                regime="incomplete",
                bias="WAIT",
                confidence=0.0,
                reason="NFP classification needs payroll, unemployment, and wage surprises.",
                blockers=missing,
            )

        payroll_surprise = float(release.payroll_actual_k) - float(release.payroll_consensus_k)
        unemployment_surprise = float(release.unemployment_actual_pct) - float(release.unemployment_consensus_pct)
        wage_surprise = float(release.avg_hourly_earnings_actual_pct) - float(release.avg_hourly_earnings_consensus_pct)
        revision = float(release.payroll_revision_k or 0.0)

        recession_flags = [
            payroll_surprise <= -175.0,
            unemployment_surprise >= 0.20,
            revision <= -100.0,
        ]
        if any(recession_flags):
            return EventAssessment(
                event_type="nfp",
                regime="recession_risk",
                bias="WAIT",
                confidence=0.75,
                reason=(
                    f"Payroll surprise {payroll_surprise:+.0f}k, unemployment {unemployment_surprise:+.1f}pp, "
                    f"revision {revision:+.0f}k: a growth scare can override rate-cut optimism."
                ),
                blockers=("Do not infer a technology long from weak payroll alone.",),
            )

        inflation_flags = [payroll_surprise >= 100.0, wage_surprise >= 0.20]
        if any(inflation_flags):
            return EventAssessment(
                event_type="nfp",
                regime="inflation_sticky",
                bias="TECH_SHORT_CANDIDATE",
                confidence=0.65,
                reason=(
                    f"Payroll surprise {payroll_surprise:+.0f}k and wage surprise {wage_surprise:+.1f}pp "
                    "keep policy expectations restrictive."
                ),
                blockers=("Market reaction still decides; do not short from the release alone.",),
            )

        soft_landing = (
            -175.0 < payroll_surprise <= -25.0
            and unemployment_surprise <= 0.10
            and wage_surprise <= 0.0
            and revision > -100.0
        )
        if soft_landing:
            return EventAssessment(
                event_type="nfp",
                regime="soft_landing_cooling",
                bias="TECH_LONG_CANDIDATE",
                confidence=0.70,
                reason=(
                    f"Moderately weak payroll ({payroll_surprise:+.0f}k) with contained unemployment "
                    f"({unemployment_surprise:+.1f}pp) and non-hot wages ({wage_surprise:+.1f}pp)."
                ),
                blockers=("Require a 60-second equity, yield, and dollar confirmation.",),
            )

        return EventAssessment(
            event_type="nfp",
            regime="mixed",
            bias="WAIT",
            confidence=0.35,
            reason=(
                f"Mixed payroll inputs: payroll {payroll_surprise:+.0f}k, unemployment {unemployment_surprise:+.1f}pp, "
                f"wages {wage_surprise:+.1f}pp, revisions {revision:+.0f}k."
            ),
            blockers=("No directional macro thesis before the market establishes one.",),
        )

    @staticmethod
    def confirm_market_reaction(
        assessment: EventAssessment,
        reaction: MarketReaction,
        min_elapsed_seconds: int = 60,
        max_elapsed_seconds: int = 300,
        min_equity_move_pct: float = 0.20,
        min_volume_ratio: float = 1.50,
    ) -> ReactionDecision:
        if assessment.bias not in {"TECH_LONG_CANDIDATE", "TECH_SHORT_CANDIDATE"}:
            return ReactionDecision(
                status="WAIT",
                direction=None,
                reason="The release classification has no directional technology thesis.",
                blockers=assessment.blockers,
            )
        if not min_elapsed_seconds <= reaction.elapsed_seconds <= max_elapsed_seconds:
            return ReactionDecision(
                status="WAIT",
                direction=None,
                reason="Reaction must be measured after the initial 60-second noise and before it goes stale.",
                blockers=(f"elapsed_seconds={reaction.elapsed_seconds}",),
            )
        direction = "LONG" if assessment.bias == "TECH_LONG_CANDIDATE" else "SHORT"
        sign = 1 if direction == "LONG" else -1
        blockers: list[str] = []
        if sign * reaction.index_return_pct < min_equity_move_pct:
            blockers.append("index move lacks breakout strength")
        if sign * reaction.leader_return_pct < min_equity_move_pct:
            blockers.append("technology leader does not confirm")
        if reaction.volume_ratio < min_volume_ratio:
            blockers.append("volume is not abnormal")
        if reaction.ten_year_yield_change_bps is not None and sign * reaction.ten_year_yield_change_bps > 0:
            blockers.append("ten-year yield conflicts with equity thesis")
        if reaction.dxy_return_pct is not None and sign * reaction.dxy_return_pct > 0:
            blockers.append("dollar move conflicts with equity thesis")
        macro_confirmation = (
            (reaction.ten_year_yield_change_bps is not None and sign * reaction.ten_year_yield_change_bps < 0)
            or (reaction.dxy_return_pct is not None and sign * reaction.dxy_return_pct < 0)
        )
        if not macro_confirmation:
            blockers.append("yield or dollar does not confirm the macro thesis")
        if blockers:
            return ReactionDecision(
                status="WAIT",
                direction=None,
                reason="Scheduled event thesis is not yet confirmed by the market.",
                blockers=tuple(blockers),
            )
        return ReactionDecision(
            status="CONFIRMED_CANDIDATE",
            direction=direction,
            reason=(
                f"{direction} candidate: macro classification and 60-second equity, volume, "
                "yield, and dollar reaction agree. This is not an order instruction."
            ),
            blockers=(),
        )


async def _main(args: Any) -> None:
    if args.write_nfp_context:
        release = NfpRelease(
            args.payroll_actual_k,
            args.payroll_consensus_k,
            args.unemployment_actual_pct,
            args.unemployment_consensus_pct,
            args.avg_hourly_earnings_actual_pct,
            args.avg_hourly_earnings_consensus_pct,
            args.payroll_revision_k,
        )
        assessment = MacroEventRadar.assess_nfp(release)
        reaction = MarketReaction(
            args.elapsed_seconds,
            args.index_return_pct,
            args.leader_return_pct,
            args.ten_year_yield_change_bps,
            args.dxy_return_pct,
            args.volume_ratio,
        )
        decision = MacroEventRadar.confirm_market_reaction(assessment, reaction)
        output: dict[str, Any] = {
            "assessment": assessment.to_dict(),
            "market_reaction": decision.to_dict(),
        }
        if decision.status == "CONFIRMED_CANDIDATE":
            context = build_confirmed_event_context(
                args.event_id,
                assessment,
                decision,
                active_minutes=args.active_minutes,
            )
            write_event_context(context, args.context_path)
            output["context_written"] = str(args.context_path)
            output["context"] = context
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return
    snapshot = await MacroEventRadar().fetch_calendar(days=args.days)
    print(json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Read-only scheduled US macro-event radar")
    parser.add_argument("--days", type=int, default=21)
    parser.add_argument("--write-nfp-context", action="store_true")
    parser.add_argument("--context-path", type=Path, default=DEFAULT_EVENT_CONTEXT_PATH)
    parser.add_argument("--event-id", default="nfp")
    parser.add_argument("--active-minutes", type=int, default=30)
    parser.add_argument("--payroll-actual-k", type=float)
    parser.add_argument("--payroll-consensus-k", type=float)
    parser.add_argument("--unemployment-actual-pct", type=float)
    parser.add_argument("--unemployment-consensus-pct", type=float)
    parser.add_argument("--avg-hourly-earnings-actual-pct", type=float)
    parser.add_argument("--avg-hourly-earnings-consensus-pct", type=float)
    parser.add_argument("--payroll-revision-k", type=float, default=0.0)
    parser.add_argument("--elapsed-seconds", type=int, default=60)
    parser.add_argument("--index-return-pct", type=float, default=0.0)
    parser.add_argument("--leader-return-pct", type=float, default=0.0)
    parser.add_argument("--ten-year-yield-change-bps", type=float)
    parser.add_argument("--dxy-return-pct", type=float)
    parser.add_argument("--volume-ratio", type=float, default=0.0)
    args = parser.parse_args()
    asyncio.run(_main(args))
