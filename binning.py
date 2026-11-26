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

import bisect
import math
from abc import ABC, abstractmethod
from datetime import date, datetime, timedelta
from functools import lru_cache
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

    #: True to anchor each x-axis label at its bin's start (the tick
    #: the value belongs to) instead of centering it under the bin --
    #: SizeAxis, whose bins are labelled by their lower boundary.
    label_at_bin_start: bool = False

    #: True when axis space (to_axis_space) isn't a plain linear copy
    #: of value space -- the widget then zooms in axis space, so a wheel
    #: step zooms the same visual amount wherever the view sits.
    nonlinear: bool = False

    def reset_level(self):
        """Back to the coarsest zoom level (only SizeAxis's nested log
        mode has levels; default no-op)."""

    def relevel(self, view_start: float, view_end: float, pivot: Optional[float] = None):
        """Adjust the zoom level for a new view after a zoom and return
        the (possibly adjusted) view -- see SizeAxis.relevel. Default:
        no levels, view unchanged."""
        return view_start, view_end

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

    def format_hover_value(self, value: float, step: Any) -> str:
        """A bin boundary value as shown in the hover/Chart readouts.
        Default: the tick label (SizeAxis's nested log mode shows the
        full exact value there instead of its "+offset" tick labels)."""
        return self.format_tick(value, step)

    def format_hover_range(self, start: float, end: float, step: Any) -> str:
        """The hovered bin as shown in the hover readout. Default: just
        its start value (as before); SizeAxis shows "start - end"."""
        return self.format_hover_value(start, step)

    def log_subgrid_fractions(self, start: float, end: float, step: Any) -> list:
        """Positions (0..1 across the bin) of the logarithmic sub-grid
        lines inside bin [start, end) -- the "Subdivide logarithmic axis
        grid" option. Empty for a non-logarithmic axis (the default)."""
        return []

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

    def axis_delta(self, value: float, ref: float) -> float:
        """Axis-space distance from `ref` to `value` (negative if `value`
        is below `ref`), i.e. to_axis_space(value) - to_axis_space(ref)
        -- but an axis whose absolute positions lose float precision
        (SizeAxis's nested log mode) computes it directly, exactly."""
        return self.to_axis_space(value) - self.to_axis_space(ref)

    def from_axis_delta(self, ref: float, delta: float) -> float:
        """The value at axis-space distance `delta` from `ref` (inverse
        of axis_delta for a fixed `ref`)."""
        return self.from_axis_space(self.to_axis_space(ref) + delta)

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
_SIZE_UNITS_BINARY = [
    (1_024 ** 5, "PiB"), (1_024 ** 4, "TiB"), (1_024 ** 3, "GiB"),
    (1_024 ** 2, "MiB"), (1_024, "KiB"), (1, "B"),
]

#: binary (KiB/MiB, 1024) instead of decimal (kB/MB, 1000) size units in
#: this module's text -- the host's "Use binary sizes" option, set via
#: set_binary_sizes. Only the units change: SizeAxis's bins themselves
#: stay on decimal (power-of-10) boundaries.
BINARY_SIZES = False


def set_binary_sizes(enabled: bool):
    global BINARY_SIZES
    BINARY_SIZES = bool(enabled)


def _size_units():
    return _SIZE_UNITS_BINARY if BINARY_SIZES else _SIZE_UNITS


def format_size_si(num_bytes: float) -> str:
    """Short size text: decimal (SI, power-of-1000) TB/GB/MB/kB/B, or
    binary TiB/GiB/MiB/KiB/B while BINARY_SIZES is on."""
    n = abs(num_bytes)
    for factor, unit in _size_units():
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


def format_size_exact(num_bytes: float, sig_digits: Optional[int] = None) -> str:
    """Like format_size_si, but never rounds: every significant digit of
    the (whole-byte) value is kept, trailing zeros dropped -- e.g.
    1_000_000_001 -> "1.000000001 GB", 1_010_000_000 -> "1.01 GB",
    10_000_000 -> "10 MB". Used by SizeAxis's nested log bins, whose
    boundaries (1 GB + 1 B, 1 GB + 10 B, ...) would all read "1.0 GB"
    with format_size_si's single decimal. `sig_digits` caps the
    significant digits instead (rounded)."""
    from decimal import Decimal, localcontext
    n = int(round(num_bytes))
    if n < 0:
        return "-" + format_size_exact(-n, sig_digits)
    for factor, unit in _size_units():
        if n >= factor:
            with localcontext() as ctx:
                # Decimal units divide exactly; binary ones give long
                # (but terminating) fractions -- 15 significant digits
                # still tell neighbouring bytes apart up to ~100 TB.
                ctx.prec = sig_digits or (15 if BINARY_SIZES else 40)
                value = Decimal(n) / Decimal(factor)
            text = format(value.normalize(), "f")
            return f"{text} {unit}"
    return "0 B"


# ------------------------------------------------------------ log bases
#: logarithm bases offered for logarithmic axes (Settings tab "Log
#: base"), by key. Powers are rounded to whole numbers (see
#: log_base_powers), since bytes and file counts are whole numbers.
LOG_BASES = {"2": 2.0, "e": math.e, "10": 10.0}
DEFAULT_LOG_BASE = "10"

#: highest power generated -- well past any real file size / count
_LOG_POWER_LIMIT = 1e19

#: Subdivide logarithmic axis grid: sub-grid lines at these multiples of
#: a cell's start, per base. Base 10: whole multiples 2..9 (log paper,
#: one level deep). Base 2 has no whole multiple inside [x, 2x) and
#: base e only 2x, so those go two levels deeper (quarters / halves).
LOG_SUBGRID_MULTIPLIERS = {
    "10": (2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0),
    "2": (1.25, 1.5, 1.75),
    "e": (1.5, 2.0, 2.5),
}


def normalize_log_base(base) -> str:
    base = str(base)
    return base if base in LOG_BASES else DEFAULT_LOG_BASE


@lru_cache(maxsize=None)
def log_base_powers(base: str) -> tuple:
    """1, b, b^2, ... rounded to whole numbers (strictly increasing, up
    to past _LOG_POWER_LIMIT): base 10 -> 1, 10, 100, ...; base 2 ->
    1, 2, 4, 8, ...; base e -> 1, 3, 7, 20, 55, 148, ..."""
    b = LOG_BASES[normalize_log_base(base)]
    out = []
    k = 0
    while True:
        p = float(round(b ** k))
        if not out or p > out[-1]:
            out.append(p)
        if p > _LOG_POWER_LIMIT:
            break
        k += 1
    return tuple(out)


def _log_children(a: float, b: float, base: str) -> list[float]:
    """Edges of the nested-log sub-bins of bin [a, b): a, a+1, a+B1,
    a+B2, ... (every whole power of the base still short of the bin's
    width), then b -- base 10: a, a+1, a+10, a+100, ... I.e. the bin's
    start ("the most significant digits") is held fixed and the
    logarithmic progression runs over the offset from it, with 1 byte
    as the smallest step. A bin 1 byte wide (or narrower) has no
    children -- it's a leaf, holding exactly one byte value."""
    width = b - a
    edges = [a]
    for p in log_base_powers(base):
        if p >= width:
            break
        edges.append(a + p)
    edges.append(b)
    return edges


#: deepest nesting level ever walked (a safety cap -- real byte counts
#: run out of levels, i.e. reach 1-byte leaf bins, long before this)
_LOG_MAX_LEVELS = 1000


@lru_cache(maxsize=None)
def _level0_edges(base: str) -> tuple:
    """Level-0 bin edges: 0, then every whole power of the base from b
    up -- base 10: 0, 10, 100, ... (the first bin swallows everything
    below b, incl. 0 where log is undefined)."""
    return (0.0,) + log_base_powers(base)[1:]


def _decade_of(value: float, base: str) -> int:
    """Level-0 bin index of `value` (bin d = [edges[d], edges[d+1]))."""
    edges = _level0_edges(base)
    d = bisect.bisect_right(edges, value) - 1
    return max(0, min(len(edges) - 2, d))


def _decade_bin(d: int, base: str) -> tuple[float, float]:
    edges = _level0_edges(base)
    d = max(0, min(len(edges) - 2, d))
    return edges[d], edges[d + 1]


def _decade_count(base: str) -> int:
    return len(_level0_edges(base)) - 1


@lru_cache(maxsize=8192)
def _log_path(value: float, base: str):
    """Nested-log path of `value` (>= 0), level 0 first, down to its
    1-byte leaf: bins[k] = (start, end) of its level-k bin."""
    a, b = _decade_bin(_decade_of(value, base), base)
    bins = [(a, b)]
    for _ in range(_LOG_MAX_LEVELS):
        if b - a <= 1.0:
            break
        edges = _log_children(a, b, base)
        j = bisect.bisect_right(edges, value) - 1
        j = max(0, min(len(edges) - 2, j))
        a, b = edges[j], edges[j + 1]
        bins.append((a, b))
    return tuple(bins)


# How many bins there are on a nesting level, counted exactly (Python
# ints). A bin's sub-structure depends only on its width (its children
# are start + 1, start + b, ... whatever the start), so counts are
# memoized per width (and base).
@lru_cache(maxsize=None)
def _child_widths(width: float, base: str) -> tuple:
    edges = _log_children(0.0, width, base)
    return tuple(edges[i + 1] - edges[i] for i in range(len(edges) - 1))


@lru_cache(maxsize=None)
def _count_bins(width: float, levels: int, base: str) -> int:
    """Number of bins `levels` levels below a bin `width` bytes wide (a
    1-byte leaf counts as one bin on every deeper level)."""
    if levels <= 0 or width <= 1.0:
        return 1
    return sum(_count_bins(cw, levels - 1, base) for cw in _child_widths(width, base))


def _decade_width(d: int, base: str) -> float:
    a, b = _decade_bin(d, base)
    return b - a


@lru_cache(maxsize=None)
def _decade_prefix(d: int, level: int, base: str) -> int:
    """Number of level-`level` bins below level-0 bin `d`."""
    if d <= 0:
        return 0
    return _decade_prefix(d - 1, level, base) + _count_bins(_decade_width(d - 1, base), level, base)


@lru_cache(maxsize=65536)
def _level_index(value: float, level: int, base: str):
    """(index, start, end) of the level-`level` bin holding `value`
    (>= 0), index counted exactly from the first bin [0, ...)."""
    d = _decade_of(value, base)
    idx = _decade_prefix(d, level, base)
    a, b = _decade_bin(d, base)
    for k in range(level):
        if b - a <= 1.0:
            break
        edges = _log_children(a, b, base)
        j = bisect.bisect_right(edges, value) - 1
        j = max(0, min(len(edges) - 2, j))
        below = level - k - 1
        for i in range(j):
            idx += _count_bins(edges[i + 1] - edges[i], below, base)
        a, b = edges[j], edges[j + 1]
    return idx, a, b


def _level_bin_by_index(index: int, level: int, base: str) -> tuple[float, float]:
    """Inverse of _level_index: the level-`level` bin with that index
    (clamped to the last bin)."""
    d = 0
    last = _decade_count(base) - 1
    while d < last:
        c = _count_bins(_decade_width(d, base), level, base)
        if index < c:
            break
        index -= c
        d += 1
    a, b = _decade_bin(d, base)
    for k in range(level):
        if b - a <= 1.0:
            break
        edges = _log_children(a, b, base)
        below = level - k - 1
        j = len(edges) - 2
        for i in range(len(edges) - 1):
            c = _count_bins(edges[i + 1] - edges[i], below, base)
            if index < c:
                j = i
                break
            index -= c
        a, b = edges[j], edges[j + 1]
    return a, b


class SizeAxis(BinAxis):
    """Decimal order-of-magnitude bins: powers of 10 bytes (1, 10, 100, ...
    i.e. B / 10 B / 100 B / kB / 10 kB / ... / MB / GB / TB), formatted with
    SI (decimal) size prefixes rather than binary ones.

    Two independent bin-spacing modes, toggled by `log_mode`/
    `set_log_mode` (off by default, matching the original behaviour):
    - Off (linear): the original behaviour above - bin *widths* are
      powers of 10, evenly spaced.
    - On (logarithmic, nested): level 0 is one bin per power of the
      log base (`log_base`, see LOG_BASES; base 10 below) - [0, 10),
      [10, 100), [100, 1000), ... (the first bin swallows everything
      below 10, incl. 0 where log is undefined). Base 2 steps by
      doubling, base e by powers of e rounded to whole bytes.
      Zooming in on a bin splits it again, logarithmically over the
      offset from its start (see _log_children): [1 GB, 10 GB) becomes
      1 GB + [0, 1 B), [1 B, 10 B), [10 B, 100 B), ..., [100 MB, 1 GB),
      then [2 GB, 10 GB). Each of those splits the same way when zoomed
      in on, and so on down to 1-byte leaf bins.
      Every bin of the current zoom level gets the same on-screen width
      (one axis unit each, see the nested log helpers), wherever it is,
      so panning never changes bin widths. The level changes only on
      zoom (relevel): one deeper once fewer than 2 bins are in view.

    The log step token is ("log", level, parent_start, parent_end):
    level 0 = decade bins (parent None); parent is the bin under the
    view's center one level up, used only for the "Bin:" readout.
    """

    _LOG_TOKEN = "log"

    #: zoom in until the view shows fewer than this many bins of the
    #: current level and it splits into the next level (see relevel)
    _LOG_SPLIT_SPAN = 2.0

    min_span = 1.0  # 1 byte
    equal_span_pad = 1024.0
    label_at_bin_start = True
    min_value = 0.0  # file sizes can't go negative - clamp view/bins at 0

    def __init__(self, max_hint: float = 1_000_000_000_000.0):
        # generated lazily/broadly; choose_step regenerates if needed
        self._max_hint = max_hint
        self._steps = _generate_size_steps(max_hint)
        self.log_mode = False
        #: logarithm base key (see LOG_BASES) for log mode's bins
        self.log_base = DEFAULT_LOG_BASE
        #: current nested-log zoom level (0 = one bin per power of the base)
        self.level = 0

    def set_log_mode(self, enabled: bool):
        self.log_mode = bool(enabled)
        self.level = 0

    def set_log_base(self, base):
        """Logarithm base of log mode's bins: "2", "e" or "10" (see
        LOG_BASES). Back to the coarsest zoom level."""
        self.log_base = normalize_log_base(base)
        self.level = 0

    @property
    def nonlinear(self) -> bool:
        return self.log_mode

    def _steps_for(self, needed_max: float) -> list[float]:
        if needed_max > self._max_hint:
            self._max_hint = needed_max
            self._steps = _generate_size_steps(needed_max)
        return self._steps

    # -- nested log helpers ------------------------------------------------
    # On-screen mapping: at the current zoom level L (self.level), every
    # level-L bin is exactly 1.0 axis unit wide, wherever it is -- so
    # panning/scrolling never changes bin widths, and a bin's children
    # don't depend on how many siblings it has. Positions are the bin's
    # exact (Python int) index on that level plus a linear fraction
    # inside the bin. Nesting can run 100+ levels deep, so indexes can
    # get astronomically large; the widget therefore only ever asks for
    # *distances* (axis_delta / from_axis_delta), whose integer part is
    # exact and whose fractional part keeps full float precision.
    # The level only changes on zoom (see relevel), never on pan.
    @classmethod
    def _is_log(cls, step: Any) -> bool:
        return isinstance(step, tuple) and len(step) > 0 and step[0] == cls._LOG_TOKEN

    def _log_bin_at(self, value: float, level: int) -> tuple[float, float]:
        """The bin of nesting `level` holding `value` (a leaf stops the
        descent early -- it's its own bin at every deeper level)."""
        path = _log_path(max(0.0, float(value)), self.log_base)
        return path[min(level, len(path) - 1)]

    def _delta_at(self, value: float, ref: float, level: int) -> float:
        iv, av, bv = _level_index(max(0.0, float(value)), level, self.log_base)
        ir, ar, br = _level_index(max(0.0, float(ref)), level, self.log_base)
        fv = min(1.0, max(0.0, (max(0.0, value) - av) / (bv - av)))
        fr = min(1.0, max(0.0, (max(0.0, ref) - ar) / (br - ar)))
        return float(iv - ir) + (fv - fr)

    def _from_delta_at(self, ref: float, delta: float, level: int) -> float:
        ref = max(0.0, float(ref))
        ir, a, b = _level_index(ref, level, self.log_base)
        t = (ref - a) / (b - a) + delta
        k = math.floor(t)
        frac = t - k
        target = ir + int(k)
        if target < 0:
            return 0.0
        a, b = _level_bin_by_index(target, level, self.log_base)
        return a + min(1.0, max(0.0, frac)) * (b - a)

    def axis_delta(self, value: float, ref: float) -> float:
        if not self.log_mode:
            return value - ref
        return self._delta_at(value, ref, self.level)

    def from_axis_delta(self, ref: float, delta: float) -> float:
        if not self.log_mode:
            return ref + delta
        if delta == 0:
            return max(0.0, float(ref))
        return self._from_delta_at(ref, delta, self.level)

    def reset_level(self):
        self.level = 0

    def _can_descend(self, value: float) -> bool:
        return len(_log_path(max(0.0, float(value)), self.log_base)) - 1 > self.level

    def relevel(self, view_start: float, view_end: float, pivot: Optional[float] = None):
        """Pick the zoom level for the view [view_start, view_end] after
        a zoom (never called on pan, so panning keeps both level and bin
        width): one level deeper while the view shows fewer than 2 bins
        of the current level, one level up while it shows 2 or more
        bins of the level above. Returns the (possibly adjusted) view:
        changing level changes the mapping, so with a `pivot` the view
        is re-fitted to keep the pivot value at the same on-screen
        fraction (wheel zoom under the cursor); without one the raw
        range is kept as is (zoom to a bin/selection)."""
        if not self.log_mode:
            return view_start, view_end
        span0 = self.axis_delta(view_end, view_start)
        frac = None
        if pivot is not None and span0 > 0:
            frac = min(1.0, max(0.0, self.axis_delta(pivot, view_start) / span0))
        start_level = self.level
        for _ in range(_LOG_MAX_LEVELS):
            span = self.axis_delta(view_end, view_start)
            center = self.from_axis_delta(view_start, span / 2)
            if span < self._LOG_SPLIT_SPAN and self._can_descend(center):
                self.level += 1
                continue
            if self.level > 0 and self._delta_at(view_end, view_start, self.level - 1) >= self._LOG_SPLIT_SPAN:
                self.level -= 1
                continue
            break
        if self.level != start_level and frac is not None:
            span = self.axis_delta(view_end, view_start)

            def fit(s):
                v0 = self.from_axis_delta(pivot, -frac * s)
                v1 = self.from_axis_delta(pivot, (1.0 - frac) * s)
                if v0 < 0.0:
                    v0 = 0.0
                    v1 = self.from_axis_delta(0.0, s)
                return v0, v1

            def too_wide(v0, v1):
                # re-fitted view showing >= 2 bins of the level above
                # would step straight back up on the next zoom (flip-flop)
                return (self.level > 0 and self.level > start_level
                        and self._delta_at(v1, v0, self.level - 1) >= self._LOG_SPLIT_SPAN)

            cand = fit(span)
            if too_wide(*cand):
                lo, hi = 0.0, span  # largest span (around the pivot) that fits
                for _ in range(48):
                    mid = (lo + hi) / 2
                    if too_wide(*fit(mid)):
                        hi = mid
                    else:
                        lo = mid
                cand = fit(lo) if lo > 0 else (view_start, view_end)
            view_start, view_end = cand
        return view_start, view_end

    # -- BinAxis interface ---------------------------------------------------
    def choose_step(self, view_start: float, view_end: float, target_bins: int) -> Any:
        target_bins = max(1, target_bins)
        if self.log_mode:
            # Not driven by target_bins. The level itself is set by relevel() on zoom; this only
            # reports it (plus the parent bin under the view's center,
            # for the "Bin:" readout).
            if self.level == 0:
                return (self._LOG_TOKEN, 0, None, None)
            span = self.axis_delta(view_end, view_start)
            center = self.from_axis_delta(view_start, span / 2)
            pa, pb = self._log_bin_at(center, self.level - 1)
            return (self._LOG_TOKEN, self.level, pa, pb)
        view_span = view_end - view_start
        ideal = max(view_span, self.min_span) / target_bins
        for step in self._steps_for(max(ideal, view_span)):
            if step >= ideal:
                return float(step)
        return float(self._steps[-1])

    def floor(self, value: float, step: Any) -> float:
        if self._is_log(step):
            return self._log_bin_at(value, step[1])[0]
        return math.floor(value / step) * step

    def next(self, boundary: float, step: Any) -> float:
        if self._is_log(step):
            return self._log_bin_at(boundary, step[1])[1]
        return boundary + step

    def format_tick(self, value: float, step: Any) -> str:
        # Log mode tick labels -- bin starts and "+offset"s alike -- are
        # rounded to 4 significant digits ("1.074 GB", "+1.049 MB") to
        # stay readable in base 2 / base e / binary units; the hover and
        # Chart readouts (format_hover_value) stay exact.
        if self._is_log(step) and step[1] > 0:
            # Below level 0: a tick at the start of its parent bin gets
            # the full value, the rest just their offset from that start
            # ("+10 MB") -- otherwise every label would read 1.0x GB.
            pa, _pb = self._log_bin_at(value, step[1] - 1)
            if abs(value - pa) < 0.5:
                return format_size_exact(value, sig_digits=4)
            return "+" + format_size_exact(value - pa, sig_digits=4)
        if self._is_log(step):
            return format_size_exact(value, sig_digits=4)
        return format_size_si(value)

    def format_hover_value(self, value: float, step: Any) -> str:
        if self._is_log(step):
            return format_size_exact(value)
        return self.format_tick(value, step)

    def format_hover_range(self, start: float, end: float, step: Any) -> str:
        return f"{self.format_hover_value(start, step)} - {self.format_hover_value(end, step)}"

    def log_subgrid_fractions(self, start: float, end: float, step: Any) -> list:
        """Log-paper pattern: lines at LOG_SUBGRID_MULTIPLIERS (base 10:
        2x, 3x, ... 9x; base 2: 1.25x/1.5x/1.75x; base e: 1.5x/2x/2.5x) the
        bin's lower bound, measured from its parent bin's start (so
        [1 GB, 10 GB) gets 2..9 GB, and the nested bin
        1 GB + [100 MB, 1 GB) gets 1 GB + 200..900 MB), each placed
        log10(k) / log10(upper/lower) of the way across. A bin starting
        right at its parent's start ([0, 10), or a parent's first
        bins) counts from 1 byte; a bin 1 byte wide has none."""
        if not self._is_log(step) or end - start <= 1.0:
            return []
        level = step[1]
        parent = 0.0 if level == 0 else self._log_bin_at(start, level - 1)[0]
        lo, hi = start - parent, end - parent
        if lo < 1.0:
            lo = 1.0
        if hi <= lo:
            return []
        span = math.log(hi / lo)
        out = []
        for m in LOG_SUBGRID_MULTIPLIERS[self.log_base]:
            if m * lo >= hi - 1e-9:
                break
            out.append(math.log(m) / span)
        return out

    def format_bin(self, start: float, end: float) -> str:
        if self.log_mode:
            return f"{format_size_exact(start)} - {format_size_exact(end)}"
        return f"{format_size_si(start)} - {format_size_si(end)}"

    def format_step(self, step: Any) -> str:
        if self._is_log(step):
            if step[1] == 0:
                return {"10": "1 bin/decade (log10)", "2": "1 bin/doubling (log2)",
                        "e": "1 bin/power of e (ln)"}[self.log_base]
            return f"log within {format_size_exact(step[2])} - {format_size_exact(step[3])}"
        return format_size_si(step)

    def format_raw_width(self, width: float) -> str:
        return format_size_si(width)

    def to_axis_space(self, value: float) -> float:
        if self.log_mode:
            # Index of the value's bin on the current level (+ fraction).
            # Coarse (absolute float) -- the widget uses axis_delta for
            # anything that has to stay exact at deep zoom.
            return self.axis_delta(value, 0.0)
        return value

    def from_axis_space(self, pos: float) -> float:
        if self.log_mode:
            if pos <= 0.0:
                return pos
            return self.from_axis_delta(0.0, pos)
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

