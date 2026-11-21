"""
ComparisonCopyMixin: a second copy of the Settings tab's "Comparison Options"
group, shown on the Main tab in Duplicate Finder mode (below the right pane,
where the button groups sit in Compare Folders mode) -- see
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

    def _mirror_spin(self, primary: SliderSpinBox):
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
        return copy, SpinSlider(copy, live=False)

    # ------------------------------------------------------------- The group

    def _build_comparison_options_copy(self) -> QGroupBox:
        """The compact copy: the same controls as the Settings tab's
        Comparison Options group, in fewer, wider rows."""
        def row(indent=20):
            layout = QHBoxLayout()
            layout.setContentsMargins(0, 0, 0, 0)
            if indent:
                layout.addSpacing(indent)
            return layout

        group = QGroupBox("Comparison Options")
        outer = QVBoxLayout(group)
        margins = outer.contentsMargins()
        outer.setContentsMargins(_CLUSTER_GROUP_PAD, margins.top(), _CLUSTER_GROUP_PAD, margins.bottom())
        group.setAlignment(Qt.AlignRight if self._symmetrical_buttons else Qt.AlignLeft)

        type_row = row(0)
        type_row.addWidget(QLabel("Comparison type:"))
        type_row.addWidget(self._mirror_combo(self.comparison_type_combo))
        type_row.addSpacing(20)
        type_row.addWidget(self._mirror_label(self.hash_algorithm_label))
        type_row.addWidget(self._mirror_combo(self.hash_algorithm_combo))
        type_row.addStretch(1)
        outer.addLayout(type_row)

        name_row = row()
        name_row.addWidget(self._mirror_checkbox(self.ignore_file_extensions_cb))
        name_row.addSpacing(20)
        name_row.addWidget(self._mirror_checkbox(self.first_name_only_cb))
        name_row.addStretch(1)
        outer.addLayout(name_row)

        labels = []
        for label_source, spin_source in ((self.fingerprint_start_label, self.fingerprint_start_spin),
                                          (self.fingerprint_end_label, self.fingerprint_end_spin)):
            chunk_row = row()
            label = self._mirror_label(label_source)
            labels.append(label)
            spin, slider = self._mirror_spin(spin_source)
            chunk_row.addWidget(spin_slider_inline(label, spin, slider), 0, Qt.AlignVCenter)
            chunk_row.addStretch(1)
            outer.addLayout(chunk_row)
        width = max(label.sizeHint().width() for label in labels)
        for label in labels:
            label.setFixedWidth(width)  # spin boxes and sliders line up

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
        cache_row.addSpacing(20)
        cache_row.addWidget(QLabel("Extra copies keep:"))
        cache_row.addWidget(self._mirror_combo(self.extra_copies_keep_combo))
        cache_row.addStretch(1)
        outer.addLayout(cache_row)
        return group

    def _fill_comparison_copy_holder(self):
        """Puts the copy into the Main tab's holder cell (Duplicate Finder
        mode). Needs the Settings tab to exist, so it runs after both tabs
        are built."""
        holder = getattr(self, "_cmp_copy_holder", None)
        if holder is None:
            return
        group = self._build_comparison_options_copy()
        layout = holder.layout()
        if self._symmetrical_buttons:
            layout.addStretch(1)  # like the right pane's mirrored groups
        layout.addWidget(group, 0, Qt.AlignTop)
        if not self._symmetrical_buttons:
            layout.addStretch(1)
