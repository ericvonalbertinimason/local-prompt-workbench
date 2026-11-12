"""Saved library (harness/saved_library.py) against throwaway databases.

The n8n library is copied once (and only once), library commands are answered locally:
"save it" keeps the prompt it follows, "save this prompt: ..." keeps the text, "find ..."
lists matches, "open ..." returns the full prompt.

  python bench/test_saved_library.py
"""
import importlib.util
import os
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

spec = importlib.util.spec_from_file_location('saved_library', Path(__file__).resolve().parents[1] / 'harness' / 'saved_library.py')
lib = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lib)
tmp = tempfile.mkdtemp()
DB, N8N = os.path.join(tmp, 'h.sqlite'), os.path.join(tmp, 'n8n.sqlite')


def opener(path):
    @contextmanager
    def connect():
        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()
    return connect


db, n8n = opener(DB), opener(N8N)
with n8n() as c:
    c.execute('CREATE TABLE %s (id INTEGER PRIMARY KEY, createdAt TEXT, updatedAt TEXT, title TEXT, prompt TEXT)' % lib.N8N_TABLE)
    c.execute('INSERT INTO %s VALUES (2, "2026-09-10", "2026-09-10", "Three-bullet article summary", '
              '"Summarize {{article}} in three bullets.")' % lib.N8N_TABLE)
with db() as c:
    c.execute('CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)')
    lib.init(c)


@contextmanager
def no_n8n():
    raise sqlite3.OperationalError('unable to open database file')
    yield


results = []
results.append((lib.import_from_n8n(db, no_n8n) is None, 'n8n unreadable: nothing copied, retried next start'))
results.append((lib.import_from_n8n(db, n8n) == 1, 'n8n library copied'))
results.append((lib.import_from_n8n(db, n8n) == 0, 'copied only once'))
results.append((lib.rows(db)[0]['id'] == 2, 'ids kept (slash commands and resources keep working)'))
reply = lib.command(db, 'save it', {'title': 'Flashcards from lecture notes', 'result': 'Turn {{lecture_notes}} into 10 cards.'})
results.append((reply.startswith('Saved to your library as #3 "Flashcards'), 'save it keeps the prompt it follows: ' + reply))
reply = lib.command(db, 'save this prompt: Rewrite {{draft}} in plain English for a busy reader.')
results.append(('Saved' in reply and len(lib.rows(db)) == 3, 'save this prompt: <text> keeps the text'))
results.append(('Nothing to save' in lib.command(db, 'save it'), 'save it with nothing to save explains Pin'))
reply = lib.command(db, 'find my flashcards prompts')
results.append((reply == '#3 Flashcards from lecture notes', 'find lists matches: ' + reply))
reply = lib.command(db, 'open the summary prompt')
results.append(('Summarize {{article}}' in reply, 'open returns the full prompt'))
results.append(('No saved prompt matches' in lib.command(db, 'find saved prompts about tax returns'), 'no match is said plainly'))
results.append((len(lib.command(db, 'list my saved prompts').splitlines()) == 3, 'list shows everything'))

failed = 0
for ok, line in results:
    failed += not ok
    print('  %s  %s' % ('PASS' if ok else 'FAIL', line))
print('\n%d/%d passed' % (len(results) - failed, len(results)))
sys.exit(failed)
