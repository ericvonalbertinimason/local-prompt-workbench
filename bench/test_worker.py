"""The run queue must survive a failing run (regression test for 2026-09-27).

Loads harness/server.py against a throwaway data folder, forces one run to crash,
and checks that it is marked as an error with the reason, that the next queued run
is still processed, and that the database uses the rollback journal (not WAL).

  python bench/test_worker.py
"""
import importlib.util
import os
import sys
import tempfile
import time
from pathlib import Path

tmp = tempfile.mkdtemp()
os.environ['HARNESS_DATA'] = tmp
HARNESS = Path(__file__).resolve().parents[1] / 'harness'
sys.path.insert(0, str(HARNESS))  # server.py imports its sibling modules (pipeline, try_inputs)
spec = importlib.util.spec_from_file_location('server', HARNESS / 'server.py')
srv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(srv)
srv.init()


def queue(rid):
    with srv.db() as c:
        c.execute("INSERT INTO runs (id,created,title,prompt,mode,requirements,session_id,status) "
                  "VALUES (?,?,?,?,?,?,?,'queued')", (rid, time.time(), rid, 'test prompt', 'iterate-prompt', '', 's'))


def status(rid):
    with srv.db() as c:
        r = c.execute('SELECT status, error FROM runs WHERE id=?', (rid,)).fetchone()
        return r['status'], r['error']


processed = []


def fake_run(row):
    if row['id'] == 'crashes':
        raise RuntimeError('disk I/O error (simulated)')
    processed.append(row['id'])
    with srv.db() as c:
        c.execute("UPDATE runs SET status='success' WHERE id=?", (row['id'],))


srv.run_claimed = fake_run
queue('crashes')
time.sleep(0.01)
queue('next-one')

results = []
try:
    srv.process_next()
    results.append((False, 'crash propagated to the loop (expected) - but it did not'))
except RuntimeError:
    results.append((True, 'crash propagated to the worker loop, which logs and continues'))
st, err = status('crashes')
results.append((st == 'error' and 'disk I/O error' in (err or ''), 'crashed run marked error with reason: %s / %s' % (st, err)))
results.append((srv.process_next() is True and processed == ['next-one'], 'next queued run still processed'))
results.append((srv.process_next() is False, 'empty queue returns False'))
with srv.db() as c:
    mode = c.execute('PRAGMA journal_mode').fetchone()[0]
results.append((mode == 'delete', 'journal mode is rollback (%s), not WAL' % mode))

# A restart (Docker Desktop restarting) re-queues a plain prompt run once; a library command
# (it saves into n8n) and a run that was already re-queued stop as "interrupted" (2026-10-21).
def running(rid, prompt, error=''):
    with srv.db() as c:
        c.execute("INSERT INTO runs (id,created,started,title,prompt,mode,requirements,session_id,status,error) "
                  "VALUES (?,?,?,?,?,?,?,?,'running',?)", (rid, time.time(), time.time(), rid, prompt, 'iterate-prompt', '', 's', error))


running('cut-off', 'Write a prompt that grades a journal entry')
running('library', 'save it to the library')
running('second-time', 'Write a prompt that grades a journal entry', 'Re-queued after a server restart.')
srv.init()
results.append((status('cut-off')[0] == 'queued', 'prompt run cut off by a restart is re-queued: %s' % (status('cut-off'),)))
results.append((status('library')[0] == 'interrupted', 'library command is not re-run: %s' % (status('library')[0],)))
results.append((status('second-time')[0] == 'interrupted', 'a run is re-queued only once: %s' % (status('second-time')[0],)))

failed = 0
for ok, line in results:
    failed += not ok
    print('  %s  %s' % ('PASS' if ok else 'FAIL', line))
print('\n%d/%d passed' % (len(results) - failed, len(results)))
sys.exit(failed)
