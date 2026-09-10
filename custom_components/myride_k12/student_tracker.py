"""
Pick each student's active run/bus for today and normalize the /api/student payload.

Ported from legrego/myride-ha-bridge's student-tracker.js (MIT), which figured out
that MyRide's stopTime values are the *district's* local wall-clock time regardless
of what timezone the server or client runs in - so "now" has to be evaluated in that
same timezone or the wrong run (e.g. a substitute's) gets picked.

The Node bridge needed a manual TZ env var for this because a bare Node process has
no notion of "the user's local timezone". Home Assistant already has one configured
(Settings > General), so this version uses `homeassistant.util.dt.now()` instead of
asking the user to set a redundant timezone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from homeassistant.util import dt as dt_util


@dataclass
class RunInfo:
    """One run (e.g. AM or PM) for a student, normalized."""

    run_id: Any
    bus_number: str | None
    active_vehicle: str | None
    is_substitute: bool
    window_start: int | None  # minutes since midnight
    window_end: int | None


@dataclass
class StudentSnapshot:
    """Everything we track for one student - static roster data plus live position."""

    unique_id: str
    first_name: str | None
    last_name: str | None
    current_run: RunInfo | None
    todays_runs: list[RunInfo] = field(default_factory=list)

    # Live fields, filled in by the coordinator as NewLocation events arrive.
    latitude: float | None = None
    longitude: float | None = None
    heading: float | None = None
    speed: float | None = None
    log_time: str | None = None
    log_time_changed_at: float | None = (
        None  # monotonic seconds; None until a message arrives
    )

    @property
    def bus(self) -> str | None:
        """The vehicle we should be listening for on the hub today."""
        return self.current_run.active_vehicle if self.current_run else None

    @property
    def is_substitute(self) -> bool:
        """Whether today's active vehicle differs from the student's regular bus."""
        return bool(self.current_run and self.current_run.is_substitute)


_MIN_TIME_PARTS = 2  # "HH:MM..." split on ":" needs at least hour and minute


def stop_time_to_minutes(stop_time: str | None) -> int | None:
    """
    Parse a stopTime string like '1900-01-01T09:02:22.99' into minutes-since-midnight.

    The date portion is always a placeholder; only the time part matters.
    """
    if not stop_time or "T" not in stop_time:
        return None
    time_part = stop_time.split("T", 1)[1]
    pieces = time_part.split(":")
    if len(pieces) < _MIN_TIME_PARTS:
        return None
    try:
        hours, minutes = int(pieces[0]), int(pieces[1])
    except ValueError:
        return None
    return hours * 60 + minutes


def _run_window(run: dict[str, Any]) -> tuple[int | None, int | None]:
    stops = run.get("stopsInfo") or []
    start = stop_time_to_minutes(stops[0]["stopTime"]) if stops else None
    end = stop_time_to_minutes(stops[-1]["stopTime"]) if len(stops) > 1 else start
    return start, end


def pick_current_run(
    run_info: list[dict[str, Any]], now_minutes: int
) -> dict[str, Any] | None:
    """
    Pick which run in run_info is "current" based on the time of day.

    Strategy, in order:
      1. The run whose stop-time window contains now.
      2. The next upcoming run (earliest start after now).
      3. The most recently past run (latest end before now).
      4. The first run, if nothing else matched.
    """
    if not run_info:
        return None
    if len(run_info) == 1:
        return run_info[0]

    windows = [(run, *_run_window(run)) for run in run_info]

    for run, start, end in windows:
        if start is not None and end is not None and start <= now_minutes <= end:
            return run

    upcoming = sorted(
        (w for w in windows if w[1] is not None and w[1] > now_minutes),
        key=lambda w: w[1],
    )
    if upcoming:
        return upcoming[0][0]

    past = sorted(
        (w for w in windows if w[2] is not None and w[2] <= now_minutes),
        key=lambda w: w[2],
        reverse=True,
    )
    if past:
        return past[0][0]

    return run_info[0]


def normalize_student(
    student: dict[str, Any], now_minutes: int | None = None
) -> StudentSnapshot:
    """Normalize a raw /api/student entry, picking today's active run/bus."""
    if now_minutes is None:
        now = dt_util.now()
        now_minutes = now.hour * 60 + now.minute

    raw_runs = student.get("runInfo") or []
    todays_runs = []
    for run in raw_runs:
        start, end = _run_window(run)
        todays_runs.append(
            RunInfo(
                run_id=run.get("runId"),
                bus_number=run.get("busNumber"),
                active_vehicle=run.get("activeVehicle"),
                is_substitute=run.get("activeVehicle") != run.get("busNumber"),
                window_start=start,
                window_end=end,
            )
        )

    current_raw = pick_current_run(raw_runs, now_minutes)
    current_run = None
    if current_raw is not None:
        current_run = next(
            (r for r in todays_runs if r.run_id == current_raw.get("runId")), None
        ) or (todays_runs[0] if todays_runs else None)
    elif todays_runs:
        current_run = todays_runs[0]

    unique_id = student.get("uniqueId")
    return StudentSnapshot(
        unique_id=str(unique_id) if unique_id is not None else "",
        first_name=student.get("firstName"),
        last_name=student.get("lastName"),
        current_run=current_run,
        todays_runs=todays_runs,
    )
