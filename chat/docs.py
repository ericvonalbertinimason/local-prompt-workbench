"""Search over your own documents for the chatbot: files -> text (core.convert) -> chunks -> SQLite FTS5.

Hybrid search: keyword (BM25, built into SQLite FTS5) plus meaning (embeddings from the local
`nomic-embed-text` model in Ollama), merged by reciprocal rank fusion. A passage worded differently
from the question is still found. If the embedding model is missing or CHAT_EMBED=off, keyword
search alone is used.
Scanned pages and pictures are read with the local vision model (core.ocr), as in the workbench;
set CHAT_OCR=off to skip them. OCR takes several seconds per page.
"""
import json
import math
import os
import re
import sqlite3
import struct
import urllib.request
from pathlib import Path

from core import convert, store

CHUNK = 1200       # characters per chunk
OVERLAP = 200
OCR_URL = os.environ.get('OCR_URL', 'http://127.0.0.1:11434/api/generate')
OCR_MODEL = os.environ.get('ASSISTANT_MODEL', 'qwen3.5-assistant-fast:4b')
EMBED_URL = os.environ.get('EMBED_URL', 'http://127.0.0.1:11434/api/embed')
EMBED_MODEL = os.environ.get('EMBED_MODEL', 'nomic-embed-text')
SKIP_DIRS = {'.git', 'node_modules', '__pycache__', '.venv', 'venv', 'data', 'backups'}
# Never index secrets, whatever folder is chosen.
SECRET = re.compile(r'(^\.env|\.key$|\.pem$|api-key|action-key|id_rsa|\.sqlite$)', re.I)


def open_index(path):
    c = sqlite3.connect(path, timeout=15)
    store.prepare(c)
    c.execute('CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(path UNINDEXED, part UNINDEXED, text)')
    c.execute('CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, mtime REAL, chunks INTEGER)')
    c.execute('CREATE TABLE IF NOT EXISTS vectors (path TEXT, part INTEGER, vec BLOB, PRIMARY KEY (path, part))')
    return c


def embed(texts, kind):
    """Unit-length vectors for `texts` (kind: 'document' or 'query'), or None when unavailable."""
    if os.environ.get('CHAT_EMBED', 'on') == 'off' or not texts:
        return None
    body = {'model': EMBED_MODEL, 'input': ['search_%s: %s' % (kind, t) for t in texts], 'truncate': True}
    req = urllib.request.Request(EMBED_URL, data=json.dumps(body).encode(), method='POST',
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            vecs = json.load(r)['embeddings']
    except (OSError, ValueError, KeyError):
        return None
    out = []
    for v in vecs:
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        out.append([x / n for x in v])
    return out


def pack(v):
    return struct.pack('%df' % len(v), *v)


def unpack(b):
    return struct.unpack('%df' % (len(b) // 4), b)


def chunks(text):
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    step = CHUNK - OVERLAP
    return [text[i:i + CHUNK] for i in range(0, max(len(text) - OVERLAP, 1), step)]


def index(c, folder, log=print):
    """Add or refresh every supported file under `folder`. Unchanged files are skipped."""
    added = skipped = failed = 0
    for f in sorted(Path(folder).rglob('*')):
        if not f.is_file() or f.suffix.lower() not in convert.TYPES or SECRET.search(f.name):
            continue
        if any(p in SKIP_DIRS for p in f.parts):
            continue
        key, mtime = str(f.resolve()), f.stat().st_mtime
        row = c.execute('SELECT mtime FROM files WHERE path=?', (key,)).fetchone()
        if row and row[0] == mtime:
            skipped += 1
            continue
        try:
            data = f.read_bytes()
            doc = convert.convert_document(f.name, data, source_limit=2_000_000)
            text = doc['text']
            if doc['ocr_pages'] and os.environ.get('CHAT_OCR', 'on') != 'off':
                log('  OCR %s (%d pages)' % (f.name, len(doc['ocr_pages'])))
                pages = [convert.ocr_document(f.name, data, n, OCR_URL, OCR_MODEL, 16384)['text']
                         for n in doc['ocr_pages']]
                text = '\n\n'.join([text] + [t for t in pages if t]).strip()
        except Exception as e:
            failed += 1
            log('  skip %s (%s)' % (f.name, str(e)[:80]))
            continue
        parts = chunks(text) if text else []
        c.execute('DELETE FROM chunks WHERE path=?', (key,))
        c.executemany('INSERT INTO chunks (path, part, text) VALUES (?,?,?)', [(key, i, t) for i, t in enumerate(parts)])
        c.execute('DELETE FROM vectors WHERE path=?', (key,))
        vecs = embed(parts, 'document')
        if vecs:
            c.executemany('INSERT INTO vectors VALUES (?,?,?)', [(key, i, pack(v)) for i, v in enumerate(vecs)])
        c.execute('INSERT OR REPLACE INTO files VALUES (?,?,?)', (key, mtime, len(parts)))
        added += 1
    c.commit()
    return {'indexed': added, 'unchanged': skipped, 'failed': failed}


STOPWORDS = set('the and for are was were what when where which who whom why how with from that this these those '
                'there their they them you your yours our ours his her hers its into about does did have has had '
                'can could would should will shall not but all any some than then also just only over such very '
                'is my me of to in on at a an it be do'.split())


def keyword(c, query, k):
    """BM25 matches; words are OR-ed so a long question still matches."""
    words = [w for w in re.findall(r'\w{3,}', query.lower()) if w not in STOPWORDS][:20]
    if not words:
        return []
    q = ' OR '.join('"%s"' % w for w in words)
    try:
        return c.execute('SELECT path, part FROM chunks WHERE chunks MATCH ? ORDER BY bm25(chunks) LIMIT ?',
                         (q, k)).fetchall()
    except sqlite3.OperationalError:
        return []


def meaning(c, query, k):
    """Nearest chunks by cosine similarity (brute force; fine for a personal folder)."""
    rows = c.execute('SELECT path, part, vec FROM vectors').fetchall()
    q = embed([query], 'query') if rows else None
    if not q:
        return []
    q = q[0]
    scored = sorted(((sum(a * b for a, b in zip(q, unpack(v))), p, i) for p, i, v in rows), reverse=True)
    return [(p, i) for _, p, i in scored[:k]]


def search(c, query, k=4):
    """Best `k` chunks for `query`: [(path, part, text)], keyword and meaning merged by rank fusion."""
    score = {}
    for ranked in (keyword(c, query, 20), meaning(c, query, 20)):
        for rank, hit in enumerate(ranked):
            score[tuple(hit)] = score.get(tuple(hit), 0) + 1 / (60 + rank)
    best = sorted(score, key=score.get, reverse=True)[:k]
    return [(p, i, c.execute('SELECT text FROM chunks WHERE path=? AND part=?', (p, i)).fetchone()[0]) for p, i in best]


def context(hits):
    """Numbered excerpts for the prompt, and the matching source list for the reply."""
    blocks = ['[%d] %s\n%s' % (i + 1, Path(p).name, t) for i, (p, _, t) in enumerate(hits)]
    return '\n\n'.join(blocks), ['[%d] %s' % (i + 1, p) for i, (p, _, _) in enumerate(hits)]
