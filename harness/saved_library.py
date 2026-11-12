"""Saved prompt library, kept in the workbench's own database (2026-10-28).

It used to live in an n8n data table, written by the n8n workflow's library tool. With the native
pipeline n8n is retired: the rows are copied here once, and library commands ("save it",
"find my study prompts", "open the summary prompt") are answered locally instead of being sent
to the model as if they were prompt requests.
"""
import json
import re
from datetime import datetime, timezone

N8N_TABLE = 'data_table_user_amkR8vZvMIm3QxXz'
SAVE = re.compile(r'^(?:(?:please|can you|could you)\s+)*(?:save|store|remember)\b', re.I)
OPEN = re.compile(r'^(?:(?:please|can you|could you)\s+)*(?:load|open|retrieve)\b', re.I)
FILLER = re.compile(r'\b(?:please|can|could|you|save|store|remember|retrieve|find|list|show|search|load|open|my|the|a|an|'
                    r'saved|prompts?|library|it|this|in|to|from|for|about|all|me|of)\b', re.I)


def init(c):
    c.execute('CREATE TABLE IF NOT EXISTS library (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, prompt TEXT, '
              'createdAt TEXT, updatedAt TEXT)')


def import_from_n8n(db, n8n_read):
    """Copy n8n's library once. Returns rows copied, 0 if already done, None if n8n is not readable."""
    with db() as c:
        if c.execute("SELECT 1 FROM settings WHERE key='library_imported'").fetchone():
            return 0
    try:
        with n8n_read() as nc:
            rows = [tuple(r) for r in nc.execute('SELECT id,title,prompt,createdAt,updatedAt FROM %s' % N8N_TABLE)]
    except Exception:
        return None
    with db() as c:
        c.executemany('INSERT OR IGNORE INTO library (id,title,prompt,createdAt,updatedAt) VALUES (?,?,?,?,?)', rows)
        c.execute("INSERT OR REPLACE INTO settings VALUES ('library_imported', ?)", (json.dumps(len(rows)),))
    return len(rows)


def rows(db):
    with db() as c:
        return [dict(r) for r in c.execute('SELECT id,title,prompt,createdAt,updatedAt FROM library '
                                           'ORDER BY updatedAt DESC, id DESC')]


def _now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]      # n8n's timestamp format


def save(db, title, prompt):
    prompt = prompt.strip()
    title = (re.sub(r'\s+', ' ', title or '').strip() or prompt.splitlines()[0])[:120]
    with db() as c:
        cur = c.execute('INSERT INTO library (title,prompt,createdAt,updatedAt) VALUES (?,?,?,?)',
                        (title, prompt, _now(), _now()))
        return cur.lastrowid, title


def command(db, text, parent=None):
    """Answer a library command. `parent` is the run it follows up on (for "save it")."""
    t = (text or '').strip()
    if SAVE.match(t):
        rest = t.split(':', 1)[1].strip() if ':' in t else ''
        if parent and parent.get('result') and len(rest) < 20:
            rid, title = save(db, parent.get('title') or '', parent['result'])
        elif len(rest) >= 20:
            rid, title = save(db, '', rest)
        else:
            return ('Nothing to save. Say "save it" as a follow-up to a finished prompt, or "save this prompt: '
                    '<the prompt>". To keep a prompt as a slash command, use Pin.')
        return 'Saved to your library as #%d "%s".' % (rid, title)
    words = [w for w in re.findall(r'[\w-]+', FILLER.sub(' ', t).lower()) if len(w) > 2]
    found = [r for r in rows(db) if all(w in ((r['title'] or '') + ' ' + (r['prompt'] or '')).lower() for w in words)]
    if not found:
        return ('No saved prompt matches "%s".' % ' '.join(words)) if words else 'Your library is empty.'
    if OPEN.match(t):
        r = found[0]
        return '#%d %s\n\n%s' % (r['id'], r['title'], r['prompt'])
    return '\n'.join('#%d %s' % (r['id'], r['title']) for r in found[:20])
