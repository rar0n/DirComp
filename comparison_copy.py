"""
ComparisonCopyMixin: a second copy of the Settings tab's "Comparison Options"
group, shown on the Main tab in Duplicate Finder mode (below the right pane,
where the button groups sit in Compare Folders mode; no title), together with
that mode's Quick Options column beside it -- see
UiBuilderMixin._build_cluster_row.

The Settings tab's widgets (self.comparison_type_combo, ...) stay the real,
live controls: everything that reads, saves or reacts to a comparison setting
still goes through them. Each widget here only mirrors one of them, both ways
(a change in the copy is handed to the original, whose own handlers do the
rest), and follows the original's enabled state, so greying a control on the
Settings tab greys its copy too.
"""

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QGroupBox, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from constants import _CLUSTER_GROUP_PAD
from custom_widgets import SliderSpinBox, SpinSlider, spin_slider_inline


class _EnabledMirror(QObject):
    """Keeps `target`'s enabled state equal to `source`'s."""

    def __init__(self, source, target):
        super().__init__(target)
        self._source = source
        self._target = target
        source.installEventFilter(self)
        target.setEnabled(source.isEnabled())

    def eventFilter(self, obj, event):
        if obj is self._source and event.type() == QEvent.EnabledChange:
            try:
                self._target.setEnabled(self._source.isEnabled())
            except RuntimeError:
                pass  # the copy is gone (UI rebuilt)
        return False


class ComparisonCopyMixin:

    # ---------------------------------------------------- Mirroring helpers

    def _mirror_combo(self, primary: QComboBox) -> QComboBox:
        copy = QComboBox()
        for i in range(primary.count()):
            copy.addItem(primary.itemText(i), primary.itemData(i))
        copy.setCurrentIndex(primary.currentIndex())
        copy.setToolTip(primary.toolTip())

        def to_primary(index, primary=primary):
            if primary.currentIndex() != index:
                primary.setCurrentIndex(index)

        def to_copy(index, copy=copy):
            try:
                if copy.currentIndex() != index:
                    copy.blockSignals(True)
                    copy.setCurrentIndex(index)
                    copy.blockSignals(False)
            except RuntimeError:
                pass  # the copy is gone (UI rebuilt)

        copy.activated.connect(to_primary)
        primary.currentIndexChanged.connect(to_copy)
        _EnabledMirror(primary, copy)
        return copy

    def _mirror_checkbox(self, primary: QCheckBox) -> QCheckBox:
        copy = QCheckBox(primary.text())
        copy.setToolTip(primary.toolTip())
        copy.setStyleSheet(primary.styleSheet())
        self._link_checkboxes(primary, copy)
        _EnabledMirror(primary, copy)
        return copy

    def _mirror_label(self, primary: QLabel) -> QLabel:
        copy = QLabel(primary.text())
        _EnabledMirror(primary, copy)
        return copy

    def _mirror_spin(self, primary: SliderSpinBox, slider_width=None):
        """(spin box, slider) following `primary`."""
        copy = SliderSpinBox()
        copy.setRange(primary.minimum(), primary.maximum())
        copy.setSingleStep(primary.singleStep())
        copy.setSuffix(primary.suffix())
        copy.setValue(primary.value())
        copy.setToolTip(primary.toolTip())

        def to_primary(value, primary=primary):
            if primary.value() != value:
                primary.setValue(value)

        def to_copy(value, copy=copy):
            try:
                if copy.value() != value:
                    copy.blockSignals(True)
                    copy.setValue(value)
                    copy.blockSignals(False)
            except RuntimeError:
                pass  # the copy is gone (UI rebuilt)

        copy.valueChanged.connect(to_primary)
        primary.valueChanged.connect(to_copy)
        _EnabledMirror(primary, copy)
        return copy, SpinSlider(copy, width=slider_width, live=False)

    # ------------------------------------------------------------- The group

    def _build_comparison_options_copy(self) -> QGroupBox:
        """The compact copy: the same controls as the Settings tab's
        Comparison Options group, in rows narrow enough to share the cell
        with the Quick Options column."""
        def row(indent=20):
            layout = QHBoxLayout()
            layout.setContentsMargins(0, 0, 0, 0)
            if indent:
                layout.addSpacing(indent)
            return layout

        # No title (saves a line of height).
        group = QGroupBox("")
        outer = QVBoxLayout(group)
        margins = outer.contentsMargins()
        outer.setContentsMargins(_CLUSTER_GROUP_PAD, margins.top(), _CLUSTER_GROUP_PAD, margins.bottom())

        type_row = row(0)
        type_row.addWidget(QLabel("Comparison type:"))
        type_row.addWidget(self._mirror_combo(self.comparison_type_combo))
        type_row.addStretch(1)
        outer.addLayout(type_row)

        algorithm_row = row()
        algorithm_row.addWidget(self._mirror_label(self.hash_algorithm_label))
        algorithm_row.addWidget(self._mirror_combo(self.hash_algorithm_combo))
        algorithm_row.addStretch(1)
        outer.addLayout(algorithm_row)

        same_size_row = row()
        same_size_row.addWidget(self._mirror_checkbox(self.hash_same_size_cb))
        same_size_row.addStretch(1)
        outer.addLayout(same_size_row)

        labels = []
        for label_source, spin_source in ((self.fingerprint_start_label, self.fingerprint_start_spin),
                                          (self.fingerprint_end_label, self.fingerprint_end_spin)):
            chunk_row = row()
            label = self._mirror_label(label_source)
            labels.append(label)
            spin, slider = self._mirror_spin(spin_source, slider_width=90)
            chunk_row.addWidget(spin_slider_inline(label, spin, slider, spacing=6), 0, Qt.AlignVCenter)
            chunk_row.addStretch(1)
            outer.addLayout(chunk_row)
        width = max(label.sizeHint().width() for label in labels)
        for label in labels:
            label.setFixedWidth(width)  # spin boxes and sliders line up

        name_row = row(8)
        name_row.addWidget(self._mirror_checkbox(self.ignore_file_extensions_cb))
        name_row.addSpacing(12)
        name_row.addWidget(self._mirror_checkbox(self.first_name_only_cb))
        name_row.addStretch(1)
        outer.addLayout(name_row)

        ignore_row = row()
        ignore_row.addWidget(self._mirror_checkbox(self.ignore_empty_files_cb))
        ignore_row.addSpacing(20)
        ignore_row.addWidget(self._mirror_checkbox(self.ignore_hard_links_cb))
        ignore_row.addStretch(1)
        outer.addLayout(ignore_row)

        cache_row = row()
        cache_row.addWidget(self._mirror_checkbox(self.cache_hashed_folders_cb))
        cache_row.addSpacing(20)
        clear_button = QPushButton("Clear cache")
        clear_button.setToolTip(
            "Deletes every stored hash (the cache file's contents) and the "
            "hashes held in memory, so open folders show as unhashed.")
        clear_button.clicked.connect(self._on_clear_hash_cache)
        cache_row.addWidget(clear_button)
        cache_row.addStretch(1)
        outer.addLayout(cache_row)

        keep_row = row()
        keep_row.addWidget(QLabel("Extra copies keep:"))
        keep_row.addWidget(self._mirror_combo(self.extra_copies_keep_combo))
        keep_row.addStretch(1)
        outer.addLayout(keep_row)
        return group

    def _fill_quick_options_column(self):
        """Duplicate Finder mode's Quick Options: one column -- Recursive
        Folder Selections, its two dependents indented below it (Expand To
        Selection, and a copy of Scan expanded folders), Symmetrical GUI,
        and Refresh at the bottom right."""
        outer = getattr(self, "_quick_options_outer", None)
        if outer is None:
            return

        def indented(widget):
            layout = QHBoxLayout()
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addSpacing(20)
            layout.addWidget(widget)
            layout.addStretch(1)
            return layout

        outer.addWidget(self.recursive_folder_contents_cb)
        outer.addLayout(indented(self.expand_matching_cb))
        outer.addLayout(indented(self._mirror_checkbox(self.scan_expanded_cb)))
        outer.addWidget(self.symmetrical_buttons_cb)
        outer.addStretch(1)
        outer.addWidget(self.refresh_button, 0, Qt.AlignRight)

    def _fill_comparison_copy_holder(self):
        """Fills the Main tab's holder cell (Duplicate Finder mode): the
        Comparison Options copy, left-aligned whatever Symmetrical GUI says,
        and at the cell's right end the Quick Options group, which leaves the
        bottom row for it and takes the cell's full height (so Refresh ends up
        in the lower right corner). Needs the Settings tab to exist, so it
        runs after both tabs are built."""
        holder = getattr(self, "_cmp_copy_holder", None)
        if holder is None:
            return
        self._fill_quick_options_column()
        layout = holder.layout()
        layout.addWidget(self._build_comparison_options_copy(), 0, Qt.AlignTop)
        layout.addStretch(1)
        layout.addWidget(self.global_options_group)
