"""Pluggable "nice" binning strategies for TimelineWidget.

The widget used to divide whatever window was visible into `num_bins`
equal-width slices, which meant bin widths changed every time you panned or
zoomed. A `BinAxis` instead snaps bins to a fixed, human-meaningful grid -
calendar units for time (decade -> year -> month -> week -> day -> hour ->
minute -> second) or decimal orders of magnitude for size (TB -> GB -> MB ->
kB -> B) - so panning only changes which bins are visible, never their
width or alignment.

A `BinAxis` knows nothing about files or `FileRecord` - it only deals in
plain floats (a unix timestamp, or a byte count). That keeps it reusable
for any other "plot a distribution of numeric values over a range" widget.
"""
from __future__ import annotations

import math
from abc import ABC, abstractmethod
from datetime import date, datetime, timedelta
from typing import Any, Optional


class BinAxis(ABC):
    """Strategy for turning a numeric value range into "nice" bin edges."""

    #: smallest sensible view span (in this axis's units) - stops zoom
    #: from collapsing to nothing.
    min_span: float = 1.0

    #: padding used when every record has the exact same value (so a lone
    #: bar has some visible width instead of a zero-width view).
    equal_span_pad: float = 1.0

    #: lower bound the view/bins may never cross (None = unbounded). Used by
    #: SizeAxis so byte counts can't pan/zoom below 0.
    min_value: Optional[float] = None

    #: True for an axis whose bins are fixed by its own nature (one per
    #: category), so the widget always uses its step logic and never the
    #: free "arbitrary equal-width bins" mode, whatever "Nice bins" says.
    always_nice: bool = False

    #: (start, end) the data is always shown over, or None to use the
    #: range the records' values actually cover.
    fixed_range: Optional[tuple] = None

    def view_padding(self, span: float) -> float:
        """Empty room added either side of the data when the view is
        reset (reset_view)."""
        return span * 0.03 if span > 0 else self.equal_span_pad

    @abstractmethod
    def choose_step(self, view_start: float, view_end: float, target_bins: int) -> Any:
        """Pick a "nice" step (opaque token) for the given visible range,
        aiming for roughly `target_bins` bins across it. Takes the full
        range rather than just its span so a mode that needs more than
        the span alone (e.g. SizeAxis's logarithmic mode, which needs
        the start/end *ratio*, not their difference) can use it -- an
        axis that only needs the span, like every mode before that one,
        just computes `view_end - view_start` itself."""

    @abstractmethod
    def floor(self, value: float, step: Any) -> float:
        """Snap `value` down to the nearest bin boundary for `step`."""

    @abstractmethod
    def next(self, boundary: float, step: Any) -> float:
        """The next bin boundary after `boundary`, for `step`."""

    @abstractmethod
    def format_tick(self, value: float, step: Any) -> str:
        """Short axis-label text for a bin boundary."""

    @abstractmethod
    def format_bin(self, start: float, end: float) -> str:
        """Human-readable description of one bin's range (for hover/click
        readouts), e.g. '2024-03 (Mar 2024)' or '10 MB - 20 MB'."""

    @abstractmethod
    def format_step(self, step: Any) -> str:
        """Human-readable bin width, e.g. '1 month' or '10 MB', shown in
        the status bar."""

    def format_raw_width(self, width: float) -> str:
        """Human-readable width for an arbitrary (non-"nice") bin size,
        used when nice-bin snapping is turned off and bins are just
        `view_span / bin_count` equal slices. Default is a plain number;
        subclasses give a more meaningful unit-aware rendering."""
        return f"{width:g}"

    # -- optional "strip larger units from the labels" support ---------
    # Only TimeAxis has anything to strip; every other axis keeps these
    # defaults (labels unchanged, no extra context), so a host can call
    # them unconditionally regardless of which axis is active.
    def format_tick_stripped(
        self, value: float, step: Any,
        use_names: bool = False, abbreviate: bool = False, use_week_number: bool = False,
    ) -> str:
        """Like format_tick, but with the parts of the label that are
        larger than the bin size left out (see strip_context for where
        they go instead). use_names/abbreviate/use_week_number are the
        Settings tab's "Use calendar unit names"/"Abbreviate names"/
        "Use week nr." options -- meaningless (and ignored) here; only
        TimeAxis has names or week numbers to give. Default: nothing to
        strip."""
        return self.format_tick(value, step)

    def strip_context(
        self, first: float, last: float, step: Any,
        use_names: bool = False, abbreviate: bool = False,
    ) -> Optional[str]:
        """The parts format_tick_stripped leaves out, for a view whose
        first/last labelled bins start at `first`/`last` -- or None if
        nothing was stripped for `step` (so there's nothing to show)."""
        return None

    def to_axis_space(self, value: float) -> float:
        """Map a raw value into the coordinate space the widget positions
        bars in - i.e. `value` such that equal *differences* in this
        space should get equal pixel width. Default is identity (a plain
        linear axis, true for every mode except SizeAxis's logarithmic
        one), so the widget's `_value_to_x` can stay axis-agnostic
        instead of special-casing log mode itself."""
        return value

    def from_axis_space(self, pos: float) -> float:
        """Inverse of `to_axis_space` - recovers a raw value from a
        position in axis space (e.g. turning a mouse x back into a byte
        count/timestamp for hit-testing, panning, etc)."""
        return pos

    def build_edges(self, view_start: float, view_end: float, step: Any) -> list[float]:
        """Generic edge builder shared by both axes: floor to a boundary
        at/before view_start, then walk `next()` until past view_end."""
        edges = [self.floor(view_start, step)]
        # safety cap so a pathological step/range combo can't loop forever
        for _ in range(100_000):
            if edges[-1] >= view_end:
                break
            edges.append(self.next(edges[-1], step))
        return edges

# --------------------------------------------------------------------- time
_SECOND = 1
_MINUTE = 60
_HOUR = 3600
_DAY = 86400
_WEEK = 7 * _DAY
_MONTH_AVG = 30.44 * _DAY
_YEAR_AVG = 365.25 * _DAY

# Ordered smallest -> largest. Each entry: (unit, multiple, approx_seconds)
_TIME_STEPS: list[tuple[str, int, float]] = (
    [("second", n, n * _SECOND) for n in (1, 2, 5, 10, 15, 30)]
    + [("minute", n, n * _MINUTE) for n in (1, 2, 5, 10, 15, 30)]
    + [("hour", n, n * _HOUR) for n in (1, 2, 3, 6, 12)]
    + [("day", 1, _DAY)]
    + [("week", 1, _WEEK)]
    + [("month", n, n * _MONTH_AVG) for n in (1, 2, 3, 6)]
    + [("year", n, n * _YEAR_AVG) for n in (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000)]
)

_TIME_UNIT_NAME = {
    "second": "second", "minute": "minute", "hour": "hour",
    "day": "day", "week": "week", "month": "month", "year": "year",
}


class TimeAxis(BinAxis):
    """Calendar-aware bins: seconds/minutes/hours align to the epoch;
    days/weeks/months/years align to actual calendar boundaries (so a
    "1 month" bin is really that whole month, not a fixed 30*86400s)."""

    min_span = 1.0  # 1 second
    equal_span_pad = _HOUR

    # -- step selection ---------------------------------------------------
    def choose_step(self, view_start: float, view_end: float, target_bins: int) -> tuple[str, int]:
        target_bins = max(1, target_bins)
        view_span = view_end - view_start
        ideal = max(view_span, self.min_span) / target_bins
        for unit, n, approx in _TIME_STEPS:
            if approx >= ideal:
                return (unit, n)
        # span wider than 1000 years (!) - fall back to the coarsest step
        unit, n, _ = _TIME_STEPS[-1]
        return (unit, n)

    # -- boundary math ------------------------------------------------------
    def floor(self, value: float, step: tuple[str, int]) -> float:
        unit, n = step
        if unit in ("second", "minute", "hour"):
            secs = {"second": 1, "minute": _MINUTE, "hour": _HOUR}[unit] * n
            return math.floor(value / secs) * secs
        dt = datetime.fromtimestamp(value)
        if unit == "day":
            d = dt.date()
            floored_ord = (d.toordinal() // n) * n
            fd = date.fromordinal(max(floored_ord, 1))
            return datetime(fd.year, fd.month, fd.day).timestamp()
        if unit == "week":
            d = dt.date()
            monday = d - timedelta(days=d.weekday())
            ref = date(1970, 1, 5)  # a Monday
            weeks_since = (monday - ref).days // 7
            floored_weeks = (weeks_since // n) * n
            fm = ref + timedelta(weeks=floored_weeks)
            return datetime(fm.year, fm.month, fm.day).timestamp()
        if unit == "month":
            total_months = dt.year * 12 + (dt.month - 1)
            floored_total = (total_months // n) * n
            y, m = divmod(floored_total, 12)
            return datetime(y, m + 1, 1).timestamp()
        if unit == "year":
            floored_year = (dt.year // n) * n
            return datetime(floored_year, 1, 1).timestamp()
        raise ValueError(f"unknown time unit {unit!r}")

    def next(self, boundary: float, step: tuple[str, int]) -> float:
        unit, n = step
        if unit in ("second", "minute", "hour"):
            secs = {"second": 1, "minute": _MINUTE, "hour": _HOUR}[unit] * n
            return boundary + secs
        dt = datetime.fromtimestamp(boundary)
        if unit == "day":
            return (dt + timedelta(days=n)).timestamp()
        if unit == "week":
            return (dt + timedelta(weeks=n)).timestamp()
        if unit == "month":
            total_months = dt.year * 12 + (dt.month - 1) + n
            y, m = divmod(total_months, 12)
            return datetime(y, m + 1, 1).timestamp()
        if unit == "year":
            return datetime(dt.year + n, 1, 1).timestamp()
        raise ValueError(f"unknown time unit {unit!r}")

    # -- formatting -----------------------------------------------------
    def format_tick(self, value: float, step: Any) -> str:
        unit, n = step
        dt = datetime.fromtimestamp(value)
        if unit in ("second",):
            return dt.strftime("%H:%M:%S")
        if unit == "minute":
            return dt.strftime("%H:%M")
        if unit == "hour":
            return dt.strftime("%Y-%m-%d %H:%M")
        if unit == "day":
            return dt.strftime("%Y-%m-%d")
        if unit == "week":
            return dt.strftime("%Y-%m-%d")
        if unit == "month":
            return dt.strftime("%Y-%m")
        if unit == "year":
            if n >= 10:
                decade_start = (dt.year // n) * n
                return f"{decade_start}s" if n >= 10 else str(dt.year)
            return str(dt.year)
        return dt.isoformat()

    #: How many leading date fields (year, month, day) format_tick_
    #: stripped drops for each step unit -- the ones larger than the
    #: bin. A week is dated by its Monday, so its month has to stay in
    #: the label (only the year goes); the time of day is one atomic
    #: piece, so hour/minute/second bins drop the whole date but keep
    #: "HH:MM" / "HH:MM:SS" as they are.
    _STRIPPED_DATE_FIELDS = {
        "year": 0, "month": 1, "week": 1, "day": 2,
        "hour": 3, "minute": 3, "second": 3,
    }

    def format_tick_stripped(
        self, value: float, step: Any,
        use_names: bool = False, abbreviate: bool = False, use_week_number: bool = False,
    ) -> str:
        unit, n = step
        dt = datetime.fromtimestamp(value)
        month_name = dt.strftime("%b" if abbreviate else "%B")
        if unit == "month":
            return month_name if use_names else dt.strftime("%m")
        if unit == "week":
            if use_week_number:
                # The week the bin *starts* on (its Monday, per floor())
                # -- same "number/name of whatever unit it starts on"
                # treatment every other stripped unit gets regardless of
                # its own multiple (n). With Abbreviate names on too,
                # the "W" is dropped -- a bare number reads fine once
                # the setting itself already says "week nr.".
                week = dt.isocalendar()[1]
                return f"{week}" if abbreviate else f"W{week}"
            return f"{month_name} {dt.day}" if use_names else dt.strftime("%m-%d")
        if unit == "day":
            return dt.strftime("%a" if abbreviate else "%A") if use_names else dt.strftime("%d")
        if unit == "hour":
            return dt.strftime("%H:%M")
        # year (nothing larger to strip), minute, second: the ordinary
        # label already is the year-only / time-only form.
        return self.format_tick(value, step)

    def strip_context(
        self, first: float, last: float, step: Any,
        use_names: bool = False, abbreviate: bool = False,
    ) -> Optional[str]:
        """The stripped-out date fields for the view, either as plain
        numbers ("2024-03-22 - 23", "2024 - 2025") or, with use_names,
        as full prose dates joined by "to" ("2024, August 13 to 2024,
        September 20") -- see _named_strip_context. The year is always
        written in full (no 2-digit shortening -- writing "24"/"25" for
        neighbouring years reads as more confusing, not less)."""
        count = self._STRIPPED_DATE_FIELDS.get(step[0], 0)
        if count <= 0:
            return None
        d0, d1 = datetime.fromtimestamp(first), datetime.fromtimestamp(last)
        if use_names and count >= 2:
            return self._named_strip_context(d0, d1, count, abbreviate)

        f0 = [d0.year, d0.month, d0.day][:count]
        f1 = [d1.year, d1.month, d1.day][:count]

        def fmt(fields, start=0):
            parts = []
            for k, v in enumerate(fields):
                parts.append(f"{v:04d}" if start + k == 0 else f"{v:02d}")
            return "-".join(parts)

        text = fmt(f0)
        if f1 != f0:
            common = 0
            while common < count and f0[common] == f1[common]:
                common += 1
            text += " - " + fmt(f1[common:], start=common)
        return text

    @staticmethod
    def _named_strip_context(d0: datetime, d1: datetime, count: int, abbreviate: bool) -> str:
        """"{year}, {Month} [day]" for each end (day only included once
        the bin itself is a day or finer, i.e. count == 3), always in
        full -- unlike the numeric form above, names aren't stripped
        down to just what differs, since "2024, August 13 to September
        20" reads as if the second date might be a different year."""
        def named(dt: datetime) -> str:
            month = dt.strftime("%b" if abbreviate else "%B")
            return f"{dt.year}, {month} {dt.day}" if count == 3 else f"{dt.year}, {month}"
        first, last = named(d0), named(d1)
        return first if first == last else f"{first} to {last}"

    def format_bin(self, start: float, end: float) -> str:
        d0 = datetime.fromtimestamp(start)
        d1 = datetime.fromtimestamp(end)
        span = end - start
        if span >= _YEAR_AVG * 9:
            return f"{d0.year}s"
        if span >= _YEAR_AVG * 1.5:
            return f"{d0.year}-{d1.year - 1}" if d1.year > d0.year + 1 else str(d0.year)
        if span >= _MONTH_AVG * 1.5:
            return d0.strftime("%Y-%m")
        if span >= _DAY * 1.5:
            return f"{d0.strftime('%Y-%m-%d')} - {d1.strftime('%Y-%m-%d')}"
        if span >= _HOUR * 1.5:
            return f"{d0.strftime('%Y-%m-%d %H:%M')}"
        return d0.strftime("%Y-%m-%d %H:%M:%S")

    def format_step(self, step: tuple[str, int]) -> str:
        unit, n = step
        if unit == "year" and n >= 10:
            decades = n // 10
            if n % 10 == 0 and n < 100:
                return f"{decades} decade" + ("s" if decades != 1 else "")
            if n % 100 == 0:
                centuries = n // 100
                return f"{centuries} centur" + ("ies" if centuries != 1 else "y")
        return f"{n} {unit}" + ("s" if n != 1 else "")

    def format_raw_width(self, width: float) -> str:
        secs = abs(width)
        if secs >= _YEAR_AVG:
            return f"{secs / _YEAR_AVG:.1f} years"
        if secs >= _MONTH_AVG:
            return f"{secs / _MONTH_AVG:.1f} months"
        if secs >= _WEEK:
            return f"{secs / _WEEK:.1f} weeks"
        if secs >= _DAY:
            return f"{secs / _DAY:.1f} days"
        if secs >= _HOUR:
            return f"{secs / _HOUR:.1f} hours"
        if secs >= _MINUTE:
            return f"{secs / _MINUTE:.1f} minutes"
        return f"{secs:.0f} seconds"


# --------------------------------------------------------------------- size
_SIZE_UNITS = [
    (1_000 ** 5, "PB"), (1_000 ** 4, "TB"), (1_000 ** 3, "GB"),
    (1_000 ** 2, "MB"), (1_000, "kB"), (1, "B"),
]


def format_size_si(num_bytes: float) -> str:
    """Decimal (SI, power-of-1000) size formatting - TB/GB/MB/kB/B, not
    binary KiB/MiB, per the user's request for 'decade order' sizes."""
    n = abs(num_bytes)
    for factor, unit in _SIZE_UNITS:
        if n >= factor:
            value = num_bytes / factor
            return f"{value:.0f} {unit}" if value >= 100 else f"{value:.1f} {unit}"
    return f"{num_bytes:.0f} B"


def _generate_size_steps(max_value: float) -> list[float]:
    """Pure decade (power-of-10) steps: 1, 10, 100, 1000, ... bytes - so
    bin widths land on B / 10B / 100B / kB / 10kB / 100kB / MB / ... rather
    than a 1-2-5 "nice number" sequence."""
    steps = []
    k = 0
    while True:
        steps.append(float(10 ** k))
        if 10 ** k > max(max_value, 1) * 10:
            break
        k += 1
        if k > 18:  # sanity cap (exabyte+ files are not a real scenario)
            break
    return steps


class SizeAxis(BinAxis):
    """Decimal order-of-magnitude bins: powers of 10 bytes (1, 10, 100, ...
    i.e. B / 10 B / 100 B / kB / 10 kB / ... / MB / GB / TB), formatted with
    SI (decimal) size prefixes rather than binary ones.

    Two independent bin-spacing modes, toggled by `log_mode`/
    `set_log_mode` (off by default, matching the original behaviour):
    - Off (linear): the original behaviour above - bin *widths* are
      powers of 10, evenly spaced.
    - On (logarithmic): exactly one bin per order of magnitude - [0,
      10), [10, 100), [100, 1000), ... (the first bin is the one
      exception: a "real" log bin would be [1, 10), but that leaves
      nowhere sensible for values below 1, including 0 itself where
      log is undefined, so the lowest bin just swallows everything
      below 10 instead). Each bin is exactly 10x the width of the one
      before it, but that ratio - not the bin's absolute width - is
      what's constant, so every magnitude gets the same single bin
      regardless of zoom/pan. Useful when the size *distribution*
      itself is the point, at the cost of bin width no longer being a
      single fixed number - see format_step.
    """

    #: sentinel step token for logarithmic mode.
    _LOG_STEP = "log-decade"

    min_span = 1.0  # 1 byte
    equal_span_pad = 1024.0
    min_value = 0.0  # file sizes can't go negative - clamp view/bins at 0

    def __init__(self, max_hint: float = 1_000_000_000_000.0):
        # generated lazily/broadly; choose_step regenerates if needed
        self._max_hint = max_hint
        self._steps = _generate_size_steps(max_hint)
        self.log_mode = False

    def set_log_mode(self, enabled: bool):
        self.log_mode = bool(enabled)

    def _steps_for(self, needed_max: float) -> list[float]:
        if needed_max > self._max_hint:
            self._max_hint = needed_max
            self._steps = _generate_size_steps(needed_max)
        return self._steps

    def choose_step(self, view_start: float, view_end: float, target_bins: int) -> Any:
        target_bins = max(1, target_bins)
        if self.log_mode:
            # Always one bin per decade - nothing to choose based on
            # view/target_bins, unlike every other axis/mode.
            return self._LOG_STEP
        view_span = view_end - view_start
        ideal = max(view_span, self.min_span) / target_bins
        for step in self._steps_for(max(ideal, view_span)):
            if step >= ideal:
                return float(step)
        return float(self._steps[-1])

    def floor(self, value: float, step: Any) -> float:
        if step == self._LOG_STEP:
            # Everything below 10 (incl. 0, and negatives from padding)
            # collapses into the one [0, 10) bin - see class docstring.
            if value < 10.0:
                return 0.0
            decade = math.floor(math.log10(value) + 1e-9)
            return 10.0 ** decade
        return math.floor(value / step) * step

    def next(self, boundary: float, step: Any) -> float:
        if step == self._LOG_STEP:
            return 10.0 if boundary <= 0.0 else boundary * 10.0
        return boundary + step

    def format_tick(self, value: float, step: Any) -> str:
        return format_size_si(value)

    def format_bin(self, start: float, end: float) -> str:
        return f"{format_size_si(start)} - {format_size_si(end)}"

    def format_step(self, step: Any) -> str:
        if step == self._LOG_STEP:
            return "1 bin/decade (log)"
        return format_size_si(step)

    def format_raw_width(self, width: float) -> str:
        return format_size_si(width)

    def to_axis_space(self, value: float) -> float:
        if self.log_mode:
            # log10(10**k) == k, so consecutive decade boundaries
            # (1, 10, 100, ...) are always exactly 1.0 apart here no
            # matter which decade they're in - that's what makes every
            # decade bin the same pixel width once the widget maps this
            # space linearly to x. Clamped to >=1 so the [0, 10) bin
            # (which floor()/next() treat as starting at 0, not 1 - see
            # class docstring) still has a finite position: it's placed
            # as if it started at 1, same as a real log axis would.
            return math.log10(max(value, 1.0))
        return value

    def from_axis_space(self, pos: float) -> float:
        if self.log_mode:
            return 10.0 ** pos
        return pos


# ----------------------------------------------------------------- category
class CategoryAxis(BinAxis):
    """One bin per named category: category i is the value range
    [i, i + 1), and a record belongs to it by having a value in there
    (the host uses i + 0.5). Used by the Statistics View's "File Filter
    types" metric, where the categories are the File Filter list's entries,
    in list order. The step is always 1 -- a bin is a whole category."""

    min_span = 1.0
    equal_span_pad = 0.5
    min_value = 0.0
    always_nice = True

    def __init__(self, labels):
        self.labels = [str(x) for x in labels]
        self.fixed_range = (0.0, float(len(self.labels)))

    def view_padding(self, span: float) -> float:
        return 0.0

    def choose_step(self, view_start: float, view_end: float, target_bins: int) -> Any:
        return 1

    def floor(self, value: float, step: Any) -> float:
        return float(math.floor(value))

    def next(self, boundary: float, step: Any) -> float:
        return boundary + 1.0

    def _label_at(self, value: float) -> str:
        i = int(math.floor(value + 1e-9))
        return self.labels[i] if 0 <= i < len(self.labels) else ""

    def format_tick(self, value: float, step: Any) -> str:
        return self._label_at(value)

    def format_bin(self, start: float, end: float) -> str:
        return self._label_at(start)

    def format_step(self, step: Any) -> str:
        return "1 entry"

    def format_raw_width(self, width: float) -> str:
        return "1 entry"

