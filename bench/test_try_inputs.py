"""Saved test inputs for "Try it" (harness/try_inputs.py) against a throwaway database.

Inputs saved on one version must be there for its revisions, deletes stay inside the family,
and empty or over-limit saves are refused.

  python bench/test_try_inputs.py
"""
import importlib.util
import os
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

spec = importlib.util.spec_from_file_location('try_inputs', Path(__file__).resolve().parents[1] / 'harness' / 'try_inputs.py')
ti = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ti)
DB = os.path.join(tempfile.mkdtemp(), 'h.sqlite')


@contextmanager
def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    try:
        with c:
            yield c
    finally:
        c.close()


with db() as c:
    c.execute('CREATE TABLE runs (id TEXT PRIMARY KEY, parent_id TEXT)')
    c.executemany('INSERT INTO runs VALUES (?,?)', [('v1', None), ('v2', 'v1'), ('v3', 'v2'), ('other', None)])
    ti.init(c)


def refused(body):
    try:
        ti.save_input(db, body)
        return False
    except ValueError:
        return True


results = []
saved = ti.save_input(db, {'run_id': 'v1', 'name': 'strong entry', 'values': {'journal_entry': 'text', 'book': ' '}})
results.append((saved['family'] == 'v1' and saved['rows'][0]['values'] == {'journal_entry': 'text'},
                'saved on v1, blank values dropped'))
results.append((ti.list_inputs(db, 'v3')['rows'][0]['name'] == 'strong entry', 'a later revision sees the same inputs'))
results.append((ti.list_inputs(db, 'other')['rows'] == [], 'an unrelated prompt does not'))
sid = saved['rows'][0]['id']
ti.save_input(db, {'run_id': 'other', 'delete': sid})
results.append((len(ti.list_inputs(db, 'v2')['rows']) == 1, 'delete from another family is ignored'))
ti.save_input(db, {'run_id': 'v2', 'delete': sid})
results.append((ti.list_inputs(db, 'v1')['rows'] == [], 'delete from the same family works'))
results.append((refused({'run_id': 'v1', 'values': {'a': '  '}}), 'empty values are refused'))
results.append((refused({'run_id': 'nope', 'values': {'a': 'x'}}), 'unknown run is refused'))
for i in range(ti.MAX_PER_FAMILY):
    ti.save_input(db, {'run_id': 'v1', 'name': str(i), 'values': {'a': 'x'}})
results.append((refused({'run_id': 'v3', 'values': {'a': 'x'}}), 'limit of %d per prompt' % ti.MAX_PER_FAMILY))
results.append((ti.save_input(db, {'run_id': 'other', 'values': {'a': 'x'}})['rows'][0]['name'] == 'Inputs',
                'unnamed inputs get a default name'))

failed = 0
for ok, line in results:
    failed += not ok
    print('  %s  %s' % ('PASS' if ok else 'FAIL', line))
print('\n%d/%d passed' % (len(results) - failed, len(results)))
sys.exit(failed)
