"""
NameFilterMixin: the Global File Filter's matching rules, in one place.

The filter text is a list of terms separated by constants.FILTER_SEPARATOR
("|"). A name passes when ANY term matches it, or -- with Invert on -- when
none does. "Ignore UPPER / lowercase" (on by default) makes the comparison
case-insensitive. Applies to file names and folder names alike.

A term without wildcards matches anywhere in the name (extension included).
A term with a wildcard -- "*" for any run of characters, "?" for exactly one
-- must match the WHOLE name: "*.txt" is every name ending in .txt,
"rep?rt*" every name starting rep, any character, rt. Nothing else is
special (brackets and dots are plain characters).

A folder row is kept when its own name passes OR something inside it
passes, so the path down to every hit stays reachable:
  - Recursive Folder Selections on: looks through the whole folder tree
    (the snapshot).
  - Off: only looks inside folders that are currently expanded -- what is
    visible. A collapsed folder then stays only if its own name passes.

Everything that used to compare file extensions (ListingMixin._build_entries,
the Statistics View's visibility check, the Duplicate Finder's scope) asks
this module instead. The matching of a single term is _compile_term.
"""

import os
import re

from constants import FILTER_SEPARATOR


def _compile_term(term: str, ignore_case: bool):
    """One filter term as a predicate on a name -- see module docstring."""
    if "*" in term or "?" in term:
        pattern = "".join(
            ".*" if ch == "*" else "." if ch == "?" else re.escape(ch) for ch in term
        )
        flags = re.DOTALL | (re.IGNORECASE if ignore_case else 0)
        regex = re.compile(pattern, flags)
        return lambda name: regex.fullmatch(name) is not None
    if ignore_case:
        low = term.lower()
        return lambda name: low in name.lower()
    return lambda name: term in name


class NameFilter:
    """One snapshot of the filter settings, built by
    NameFilterMixin._name_filter(). Cheap to make; make one per listing."""

    def __init__(self, terms, invert, ignore_case, recursive, expanded_getter, memo):
        self.ignore_case = ignore_case
        self.terms = list(terms)
        self._matchers = [_compile_term(t, ignore_case) for t in self.terms]
        self.invert = invert
        self.recursive = recursive
        self._expanded_getter = expanded_getter
        self._expanded = None
        self._memo = memo

    @property
    def active(self) -> bool:
        return bool(self.terms)

    def name_matches(self, name: str) -> bool:
        """Does any term occur in `name` (ignoring Invert)."""
        return any(match(name) for match in self._matchers)

    def name_passes(self, name: str) -> bool:
        """Does `name` pass the filter (Invert applied). True when the
        filter is empty."""
        if not self.terms:
            return True
        return self.name_matches(name) ^ self.invert

    def _is_expanded(self, path: str) -> bool:
        if self._expanded is None:
            self._expanded = self._expanded_getter()
        return os.path.normpath(path) in self._expanded

    def dir_passes(self, node) -> bool:
        """Whether a folder row stays listed: its own name passes, or
        anything in its scope (see module docstring) does."""
        if not self.terms:
            return True
        if self.name_passes(node["name"]):
            return True
        # Whole-tree results only depend on the node and the filter, so
        # they are remembered (the memo is dropped when the filter changes).
        memo = self._memo if self.recursive else None
        if memo is not None:
            hit = memo.get(id(node))
            if hit is not None and hit[0] is node:
                return hit[1]
        result = self._has_passing_descendant(node)
        if memo is not None:
            memo[id(node)] = (node, result)
        return result

    def _has_passing_descendant(self, node) -> bool:
        if not self.recursive and not self._is_expanded(node["full_path"]):
            return False
        stack = list((node["children"] or {}).values())
        while stack:
            child = stack.pop()
            if self.name_passes(child["name"]):
                return True
            if child["is_dir"] and child["children"] and (
                    self.recursive or self._is_expanded(child["full_path"])):
                stack.extend(child["children"].values())
        return False


class NameFilterMixin:
    def _filter_terms(self) -> list:
        entry = getattr(self, "file_filter_entry", None)
        text = entry.text() if entry is not None else ""
        return [t.strip() for t in text.split(FILTER_SEPARATOR) if t.strip()]

    def _filter_ignore_case(self) -> bool:
        cb = getattr(self, "ignore_case_filter_cb", None)
        return cb.isChecked() if cb is not None else True

    def _filter_invert(self) -> bool:
        cb = getattr(self, "invert_filter_cb", None)
        return cb.isChecked() if cb is not None else False

    def _filter_expanded_paths(self, pane) -> set:
        """Normalized paths of the expanded folders of `pane` (both panes
        when None). While a pane's tree is being rebuilt it is empty, so
        the set captured before the rebuild stands in for it."""
        panes = [pane] if pane is not None else [getattr(self, "left", None), getattr(self, "right", None)]
        expanded = set()
        for p in panes:
            if p is None:
                continue
            frozen = getattr(p, "_filter_rebuild_expanded", None)
            if frozen is not None:
                paths = frozen
            elif getattr(p, "tree", None) is not None:
                paths = self._collect_expanded_paths(p.tree)
            else:
                paths = ()
            expanded.update(os.path.normpath(x) for x in paths)
        return expanded

    def _name_filter(self, pane=None) -> NameFilter:
        """The current filter settings as a NameFilter."""
        terms = self._filter_terms()
        invert = self._filter_invert()
        ignore_case = self._filter_ignore_case()
        recursive = self._recursive_folder_contents_enabled()
        key = (tuple(terms), invert, ignore_case)
        cache = getattr(self, "_name_filter_memo", None)
        if cache is None or cache[0] != key:
            cache = self._name_filter_memo = (key, {})
        return NameFilter(terms, invert, ignore_case, recursive,
                          lambda: self._filter_expanded_paths(pane), cache[1])
