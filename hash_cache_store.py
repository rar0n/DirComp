"""
HashCacheStore: the persistent half of the hash cache (see "Plan:
persistent hash cache" in docs/Roadmap.txt). One SQLite file holds, per
file path and comparison key, the size and whole-second mtime the digest
was computed for. No Qt, no knowledge of panes or snapshots: HashOpsMixin
reads it into RAM when a folder loads and writes to it only when hashes
are made or DirComp itself moves/renames/deletes/copies files.

Where: Cache/hashes.sqlite next to the program; if that folder can't be
written, the per-user cache folder (XDG_CACHE_HOME or ~/.cache, or
%LOCALAPPDATA% on Windows) instead. Nothing is created until the first
write: opening a folder only reads, and reading a file that doesn't exist
yet is just "empty".

Robustness: every public method swallows sqlite errors (the cache is
best-effort and the app must keep working without it). A file that is not
a database, or has another format version, is moved aside as
`hashes.sqlite.corrupt` (or deleted) and rebuilt empty on the next write.
A failed write rolls back, so a crash never leaves half a batch.

Keys: a comparison key is a tuple of ints (see
DiffEngineMixin._comparison_key); it is stored as its JSON text. A file
keeps at most MAX_KEYS_PER_FILE keys (the most recently written), and only
while they all agree on size and mtime: writing a digest for a changed
file drops the older digests of that path.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time

FORMAT_VERSION = 1
MAX_KEYS_PER_FILE = 3
DB_NAME = "hashes.sqlite"
_CACHE_DIRNAME = "Cache"
_APP_DIRNAME = "DirComp"


def _program_cache_dir() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), _CACHE_DIRNAME)


def _user_cache_dir() -> str:
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, _APP_DIRNAME)


def _prefix_range(root: str):
    """(low, high) such that every path below `root` satisfies
    low <= path < high (a plain index range scan, no LIKE escaping)."""
    prefix = root.rstrip(os.sep) + os.sep
    return prefix, prefix[:-1] + chr(ord(prefix[-1]) + 1)


def _key_text(key) -> str:
    return json.dumps(list(key), separators=(",", ":"))


def _key_tuple(text: str) -> tuple:
    return tuple(json.loads(text))


class HashCacheStore:
    """Rows are (path, key, size, mtime, digest). `candidates` (a list of
    directories, tried in order) is only for tests; the default is the
    program's Cache folder, then the per-user cache folder."""

    def __init__(self, candidates=None):
        self._candidates = list(candidates) if candidates else [_program_cache_dir(), _user_cache_dir()]
        self._conn = None
        self._path = None  # the database file in use, once found

    # ------------------------------------------------------------- location

    def path(self) -> str:
        """The database file in use, or where it would be created now."""
        if self._path:
            return self._path
        for folder in self._candidates:
            candidate = os.path.join(folder, DB_NAME)
            if os.path.exists(candidate):
                return candidate
        return os.path.join(self._candidates[0], DB_NAME)

    def exists(self) -> bool:
        return any(os.path.exists(os.path.join(f, DB_NAME)) for f in self._candidates)

    # ----------------------------------------------------------- connection

    def _connect(self, create: bool):
        """The open connection, or None (nothing stored yet and `create`
        is False, or no candidate folder is usable)."""
        if self._conn is not None:
            return self._conn
        for folder in self._candidates:
            db_file = os.path.join(folder, DB_NAME)
            if not os.path.exists(db_file):
                continue
            conn = self._open_existing(db_file)
            if conn is not None:
                self._conn, self._path = conn, db_file
                return conn
        if not create:
            return None
        for folder in self._candidates:
            db_file = os.path.join(folder, DB_NAME)
            try:
                os.makedirs(folder, exist_ok=True)
                conn = self._create(db_file)
            except (OSError, sqlite3.Error):
                continue
            self._conn, self._path = conn, db_file
            return conn
        return None

    def _open_existing(self, db_file):
        """Open a database file and check its format; a damaged or foreign
        file is set aside (or deleted) and None returned."""
        try:
            conn = sqlite3.connect(db_file, timeout=5.0, isolation_level=None)
        except sqlite3.Error:
            return None
        try:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
            if version == FORMAT_VERSION:
                conn.execute("SELECT 1 FROM hashes LIMIT 1").fetchall()
                self._tune(conn)
                return conn
        except sqlite3.Error:
            pass
        try:
            conn.close()
        except sqlite3.Error:
            pass
        self._set_aside(db_file)
        return None

    @staticmethod
    def _set_aside(db_file):
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(db_file + suffix)
            except OSError:
                pass

    @staticmethod
    def _tune(conn):
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.Error:
            pass  # e.g. a filesystem without shared memory: default journal is fine

    def _create(self, db_file):
        conn = sqlite3.connect(db_file, timeout=5.0, isolation_level=None)
        try:
            self._tune(conn)
            collate = " COLLATE NOCASE" if sys.platform.startswith("win") else ""
            conn.execute("BEGIN")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS hashes ("
                f" path TEXT{collate} NOT NULL, key TEXT NOT NULL,"
                " size INTEGER NOT NULL, mtime INTEGER NOT NULL,"
                " digest TEXT NOT NULL, stamp REAL NOT NULL,"
                " PRIMARY KEY (path, key)) WITHOUT ROWID")
            conn.execute(f"PRAGMA user_version = {FORMAT_VERSION}")
            conn.execute("COMMIT")
        except sqlite3.Error:
            conn.close()
            self._set_aside(db_file)
            raise
        return conn

    def close(self):
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None

    def _fail(self):
        """A statement failed: drop the connection; the next call reopens
        it (and rebuilds the file if it is what's broken)."""
        self.close()

    # --------------------------------------------------------------- reading

    def load_prefix(self, root: str):
        """Every row at or below directory `root`: {path: (size, mtime,
        {key: digest})}. Empty if nothing is stored. Never creates the file."""
        conn = self._connect(create=False)
        if conn is None:
            return {}
        low, high = _prefix_range(root)
        result = {}
        try:
            rows = conn.execute(
                "SELECT path, key, size, mtime, digest FROM hashes WHERE path >= ? AND path < ?",
                (low, high)).fetchall()
        except sqlite3.Error:
            self._fail()
            return {}
        for path, key, size, mtime, digest in rows:
            try:
                key = _key_tuple(key)
            except ValueError:
                continue
            entry = result.get(path)
            if entry is None:
                result[path] = entry = (size, mtime, {})
            if (entry[0], entry[1]) == (size, mtime):
                entry[2][key] = digest
        return result

    def paths_under(self, root: str):
        conn = self._connect(create=False)
        if conn is None:
            return []
        low, high = _prefix_range(root)
        try:
            return [r[0] for r in conn.execute(
                "SELECT DISTINCT path FROM hashes WHERE path >= ? AND path < ?", (low, high))]
        except sqlite3.Error:
            self._fail()
            return []

    def count(self) -> int:
        conn = self._connect(create=False)
        if conn is None:
            return 0
        try:
            return conn.execute("SELECT count(DISTINCT path) FROM hashes").fetchone()[0]
        except sqlite3.Error:
            self._fail()
            return 0

    # --------------------------------------------------------------- writing

    def put_many(self, rows) -> bool:
        """Store (path, key, size, mtime, digest) rows in one transaction.
        For each path, digests stored for another size/mtime are dropped
        (that file changed) and only the MAX_KEYS_PER_FILE most recent
        keys are kept. Returns False if nothing could be written."""
        rows = list(rows)
        if not rows:
            return True
        conn = self._connect(create=True)
        if conn is None:
            return False
        now = time.time()
        paths = {}
        try:
            conn.execute("BEGIN")
            for i, (path, key, size, mtime, digest) in enumerate(rows):
                conn.execute(
                    "INSERT OR REPLACE INTO hashes (path, key, size, mtime, digest, stamp)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (path, _key_text(key), int(size), int(mtime), digest, now + i * 1e-6))
                paths[path] = (int(size), int(mtime))
            for path, (size, mtime) in paths.items():
                conn.execute("DELETE FROM hashes WHERE path = ? AND (size <> ? OR mtime <> ?)",
                             (path, size, mtime))
                conn.execute(
                    "DELETE FROM hashes WHERE path = ? AND key NOT IN"
                    " (SELECT key FROM hashes WHERE path = ? ORDER BY stamp DESC LIMIT ?)",
                    (path, path, MAX_KEYS_PER_FILE))
            conn.execute("COMMIT")
        except sqlite3.Error:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            self._fail()
            return False
        return True

    def delete_paths(self, paths) -> bool:
        paths = list(paths)
        if not paths or not self.exists():
            return True
        return self._write(lambda c: c.executemany("DELETE FROM hashes WHERE path = ?", [(p,) for p in paths]))

    def delete_prefix(self, root: str) -> bool:
        """Remove every row below directory `root` (a folder deleted from DirComp)."""
        if not self.exists():
            return True
        low, high = _prefix_range(root)
        return self._write(lambda c: c.execute("DELETE FROM hashes WHERE path >= ? AND path < ?", (low, high)))

    def clear(self) -> int:
        """Wipe every row; returns how many files had entries."""
        before = self.count()
        if self.exists():
            self._write(lambda c: c.execute("DELETE FROM hashes"))
            conn = self._conn
            if conn is not None:
                try:
                    conn.execute("VACUUM")
                except sqlite3.Error:
                    pass
        return before

    def _write(self, action) -> bool:
        conn = self._connect(create=False)
        if conn is None:
            return True
        try:
            conn.execute("BEGIN")
            action(conn)
            conn.execute("COMMIT")
        except sqlite3.Error:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            self._fail()
            return False
        return True
