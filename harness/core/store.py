"""SQLite helpers for the run history and any other app's store (moved from the workbench server, 2026-11-08).

One short connection per use (rows as sqlite3.Row, committed when the block ends without an
error), a read-only opener, the journal setting this machine needs, and a column migration helper.
Which tables exist stays with each app.
"""
import sqlite3
from contextlib import contextmanager


def opener(path, timeout=15):
    """A db() context manager for the database at `path`."""
    @contextmanager
    def db():
        c = sqlite3.connect(path, timeout=timeout)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()
    return db


@contextmanager
def read_only(path, timeout=5):
    """Open another program's database without any chance of writing to it."""
    c = sqlite3.connect('file:' + str(path) + '?mode=ro', uri=True, timeout=timeout)
    c.row_factory = sqlite3.Row
    try:
        yield c
    finally:
        c.close()


def prepare(c):
    # Rollback journal, not WAL: WAL needs a shared-memory side file, which is unreliable on a
    # Windows-to-container bind mount (disk I/O errors on 2026-09-27).
    c.execute('PRAGMA journal_mode=DELETE')


def add_columns(c, table, columns):
    """Add any missing TEXT columns: columns is [(name, SQL default)]."""
    existing = {r[1] for r in c.execute('PRAGMA table_info(%s)' % table)}
    for name, default in columns:
        if name not in existing:
            c.execute('ALTER TABLE %s ADD COLUMN %s TEXT DEFAULT %s' % (table, name, default))
