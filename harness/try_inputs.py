"""Saved test inputs for "Try it": named sets of {{variable}} values kept per prompt family.

A family is a prompt and all its revisions (runs linked by parent_id), so inputs saved on one
version are there for every later one and can be re-run after each change. Stored in the
workbench history database, table try_inputs. Local page only (not served to hosted clients).
"""
import json
import time
import uuid

MAX_PER_FAMILY = 20


def init(c):
    c.execute('CREATE TABLE IF NOT EXISTS try_inputs (id TEXT PRIMARY KEY, family TEXT, name TEXT, '
              'input_values TEXT, created REAL)')


def family(c, run_id):
    """The first run of a revision chain, or None for an unknown run."""
    rid = run_id
    for _ in range(100):
        r = c.execute('SELECT id, parent_id FROM runs WHERE id=?', (rid,)).fetchone()
        if not r:
            return None
        if not r['parent_id']:
            return r['id']
        rid = r['parent_id']
    return rid


def list_inputs(db, run_id):
    with db() as c:
        fam = family(c, str(run_id or ''))
        if not fam:
            raise ValueError('Unknown run')
        rows = c.execute('SELECT id, name, input_values, created FROM try_inputs WHERE family=? ORDER BY created',
                         (fam,)).fetchall()
    return {'family': fam, 'rows': [{'id': r['id'], 'name': r['name'], 'values': json.loads(r['input_values']),
                                     'created': r['created']} for r in rows]}


def save_input(db, body):
    """Save one set of values, or delete one with {"run_id": ..., "delete": id}."""
    run_id = str(body.get('run_id') or '')
    with db() as c:
        fam = family(c, run_id)
        if not fam:
            raise ValueError('Unknown run')
        if body.get('delete'):
            c.execute('DELETE FROM try_inputs WHERE id=? AND family=?', (str(body['delete']), fam))
        else:
            values = {str(k)[:80]: str(v)[:20000] for k, v in (body.get('values') or {}).items() if str(v).strip()}
            if not values:
                raise ValueError('Fill in at least one variable before saving')
            if c.execute('SELECT count(*) FROM try_inputs WHERE family=?', (fam,)).fetchone()[0] >= MAX_PER_FAMILY:
                raise ValueError('%d saved inputs is the limit for one prompt; delete one first' % MAX_PER_FAMILY)
            name = (str(body.get('name') or '').strip() or 'Inputs')[:80]
            c.execute('INSERT INTO try_inputs VALUES (?,?,?,?,?)',
                      (uuid.uuid4().hex[:12], fam, name, json.dumps(values), time.time()))
    return list_inputs(db, run_id)
