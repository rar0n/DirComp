"""
Cancellation/progress-bar helper shared by the Select group's search
buttons, the central move/paste bar, and the item-recount that runs when "Recursive Folder Contents" is toggled.
"""

from __future__ import annotations

import time

from PySide6.QtWidgets import QApplication, QProgressBar


class _CancellableProgress:
    """Cooperative-cancellation token paired with a QProgressBar: tracks
    cancellation, keeps the UI responsive (by periodically pumping the Qt
    event loop) during a long operation so its Cancel button actually gets
    a chance to be clicked and noticed, and drives the bar itself. If
    `total` is given up front, the bar fills determinately as tick() is
    called; otherwise it runs in "busy" mode, since with no known total
    there's nothing to show a percentage of."""

    # Pump the event loop at least this often (seconds) during a scan,
    # rather than every N *items* -- a fixed item count meant a run of
    # unusually slow items (e.g. a directory entry whose size needs a
    # real recursive filesystem walk -- see listing_ops._build_entries)
    # could go tens of items, and however long each of those actually
    # took, without a single repaint or a chance for Cancel to be
    # noticed: the busy bar would just sit there looking frozen, then
    # suddenly "catch up" and animate once enough items had gone by.
    # Time-based pumping keeps the interval between repaints roughly
    # constant regardless of how expensive any given item turns out to
    # be, so the bar reads as continuously alive instead of stalling
    # then lurching. A single pathologically slow item is still atomic
    # and can't be interrupted mid-call, same caveat as always.
    _PUMP_INTERVAL = 0.05

    # How often (seconds) the live count/duration label (see `on_tick`)
    # actually refreshes its text. Much coarser than _PUMP_INTERVAL on
    # purpose -- the bar itself already animates smoothly every tick,
    # but redrawing a text label doesn't need to happen anywhere near
    # that often to still read as "live"; once a second is plenty, and
    # avoids paying a label-repaint cost on every single item.
    _LABEL_INTERVAL = 1.0

    def __init__(self, bar: QProgressBar, total: int | None = None, on_tick=None):
        self.cancelled = False
        self._count = 0
        self._bar = bar
        self._last_pump = time.monotonic()
        # None (rather than time.monotonic()) so the very first tick()
        # always shows a value immediately regardless of _LABEL_INTERVAL
        # -- an operation quick enough to finish inside that first
        # second (increasingly common now that most of what this app
        # does is snapshot-backed rather than touching disk) used to
        # never trigger even one label update at all.
        self._last_label = None
        self._on_tick = on_tick
        self._set_range(total)

    def _set_range(self, total: int | None):
        """Apply `total` to the bar, and flip its "busy" dynamic property
        (see ui_builder._build_transfer_group's stylesheet) so a
        busy-mode bar -- total=None, Qt's own native range(0,0)
        indeterminate animation -- reliably paints in a visually
        distinct color/pattern from an ordinary determinate fill,
        instead of just looking like a normal bar that happens to be
        stuck at 0%. unpolish/polish is what makes a QSS dynamic-
        property selector actually take effect immediately rather than
        waiting for some unrelated style event."""
        busy = not total
        self._bar.setProperty("busy", busy)
        style = self._bar.style()
        style.unpolish(self._bar)
        style.polish(self._bar)
        if total:
            self._bar.setRange(0, total)
        else:
            self._bar.setRange(0, 0)
        self._bar.setValue(0)

    @property
    def count(self) -> int:
        """Real number of tick()s so far -- distinct from the bar's own
        (possibly clamped, possibly estimated-total) displayed value.
        Used both for an accurate final "N items" result label (see
        TransferMixin._finish_transfer_progress) and by flush_to_max
        below, regardless of what `total` this cycle was actually
        armed with."""
        return self._count

    def flush_to_max(self):
        """Cosmetically snap the bar to its own maximum right before the
        caller collapses it (see TransferMixin._finish_transfer_progress,
        which calls this unconditionally as part of ending any cycle) --
        for a determinate cycle whose `total` was an estimate (e.g.
        statistics_ops._on_timeline_selection's "assume the other pane
        has at most as many matches" guess for the Synchronize-Views
        mirror walk) and so may have real ticks land short of it. A
        no-op in busy mode (maximum() == 0, nothing to snap to) or once
        cancelled (a cancelled operation shouldn't visually read as
        finished) -- and harmless for a cycle whose ticks already
        reached `total` exactly, since setValue(maximum) is then a
        no-op repaint-wise. The processEvents() call is what actually
        makes the snap-to-full visible for a moment before
        _finish_transfer_progress's own bar.setRange(0, 1) collapses
        it right back down -- without it Qt is free to coalesce the
        two into a single repaint that never shows the full bar at
        all."""
        if self.cancelled:
            return
        maximum = self._bar.maximum()
        if maximum > 0:
            self._bar.setValue(maximum)
            QApplication.processEvents()

    def tick(self) -> bool:
        """Call once per item visited/processed. Returns False once
        cancelled, at which point the caller should stop and unwind."""
        self._count += 1
        if self._bar.maximum() > 0:
            self._bar.setValue(min(self._count, self._bar.maximum()))
        now = time.monotonic()
        if now - self._last_pump >= self._PUMP_INTERVAL:
            self._last_pump = now
            QApplication.processEvents()
        if self._on_tick is not None and (self._last_label is None or now - self._last_label >= self._LABEL_INTERVAL):
            self._last_label = now
            self._on_tick(self._count)
        return not self.cancelled

    def advance(self, units: int) -> bool:
        """Like tick(), but moves the bar (and the real count) forward by
        `units` at once -- for work measured in something other than
        items, such as bytes hashed (see HashOpsMixin). Never calls the
        live item-count label callback, since `units` aren't items.
        Returns False once cancelled, same contract as tick()."""
        self._count += units
        if self._bar.maximum() > 0:
            self._bar.setValue(min(self._count, self._bar.maximum()))
        now = time.monotonic()
        if now - self._last_pump >= self._PUMP_INTERVAL:
            self._last_pump = now
            QApplication.processEvents()
        return not self.cancelled

    def pump(self) -> bool:
        """Keep the UI responsive and let Cancel be noticed, without
        advancing the bar or the live count/duration label -- for
        background traversal (e.g. walking through an ancestor
        directory on the way to a real match, in
        selection_ops._select_paths_in_tree's walk()) that shouldn't
        count against a caller-sized, already-known total the way
        tick() does. A caller that already knows its real total up
        front (see statistics_ops._on_timeline_selection) needs the bar
        to reach 100% only on genuine matches, not on every node merely
        passed through on the way to one -- ticking both would make the
        bar finish long before the walk actually does. Same
        cancellation/pumping behavior as tick(), just without the
        counting half of it. Returns False once cancelled, same
        contract as tick()."""
        now = time.monotonic()
        if now - self._last_pump >= self._PUMP_INTERVAL:
            self._last_pump = now
            QApplication.processEvents()
        return not self.cancelled

    def start_phase(self, total: int | None):
        """Move this same token into a new counting phase -- e.g. from an
        "Enumerating..." busy-mode file count into a determinate
        "Copying..."/"Moving..." total once the real amount of work is
        known, or from one pane's snapshot refresh into the next pane's
        (see update_listings) -- so each phase's ticks represent its own
        progress, not a continuation of the previous phase's (unrelated)
        count; without that, opening a second folder while a first one
        was already loaded made the live label jump straight to
        (already-loaded pane's total) before the new pane's own count
        had even started climbing, reading as more items than the
        folder actually being opened has. Cancellation state carries
        over unchanged -- a cancel during enumeration still stops the
        transfer that would otherwise follow. _last_label is reset too
        (see __init__) so the new phase's first tick shows a value
        immediately, the same as a brand new progress cycle would,
        rather than waiting out whatever was left of the previous
        phase's interval."""
        self._count = 0
        self._last_label = None
        self._set_range(total)
