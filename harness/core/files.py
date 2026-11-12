"""Files from Claude (2026-11-07): create_prompt can name files on this PC instead of pasting them.

The desktop-app bridge (mcp_stdio_bridge.py) reads only the files Claude names, on the PC, and sends
their bytes as `file_data`; the workbench container gets no access to your folders. Here the bytes
go through the same converter the page uses. Text is added to the source material at once. Scanned
pages and pictures are parked in DATA/uploads and marked; the worker reads them by OCR when the run
starts (so the tool call does not wait on a long scan) and then deletes the parked file.

A mark can only point into the uploads folder: marks typed into pasted text are ignored.
"""
import base64
import os
import re
import uuid
from pathlib import Path

MARK = '⟦workbench-ocr⟧'          # marks pages still to read; never typed by a person
MARK_LINE = re.compile(re.escape(MARK) + r'(?P<file>[0-9a-f]{32}/[^|\n/\\]+)\|(?P<page>\d+)$')
MAX_FILES = 5
MAX_BYTES = 40 * 1024 * 1024


def uploads(data_dir):
    return Path(data_dir) / 'uploads'


def from_uploads(file_data, convert_document, data_dir):
    """Convert files sent by the bridge. Returns source text; OCR marks come first so a long
    document can never push them past the source-material limit."""
    if not isinstance(file_data, list) or not file_data:
        raise ValueError('file_data must be a list of {name, data}')
    if len(file_data) > MAX_FILES:
        raise ValueError('At most %d files per request' % MAX_FILES)
    marks, texts, notes = [], [], []
    for item in file_data:
        name = Path(str((item or {}).get('name') or 'file')).name[:120]
        try:
            data = base64.b64decode(str(item.get('data') or ''), validate=True)
        except ValueError:
            raise ValueError('%s: not valid base64' % name)
        if len(data) > MAX_BYTES:
            raise ValueError('%s is larger than %d MB' % (name, MAX_BYTES // 1024 // 1024))
        res = convert_document(name, data)
        text = (res.get('text') or '').strip()
        if text:
            texts.append('--- %s ---\n%s' % (name, text))
        if res.get('ocr_pages'):
            folder = uploads(data_dir) / uuid.uuid4().hex
            folder.mkdir(parents=True, exist_ok=True)
            (folder / name).write_bytes(data)
            marks += ['%s%s/%s|%d' % (MARK, folder.name, name, p) for p in res['ocr_pages']]
        if res.get('ocr_skipped'):
            notes.append('[%d more scanned pages of %s were not read (limit)]' % (res['ocr_skipped'], name))
    return '\n\n'.join(['\n'.join(marks)] * bool(marks) + texts + notes)


def prune(data_dir, hours=24):
    """Remove parked files older than `hours` (a run that never started). Returns how many folders."""
    removed = 0
    root = uploads(data_dir)
    if not root.is_dir():
        return 0
    cutoff = __import__('time').time() - hours * 3600
    for folder in root.iterdir():
        if folder.is_dir() and folder.stat().st_mtime < cutoff:
            for f in folder.iterdir():
                f.unlink()
            folder.rmdir()
            removed += 1
    return removed


def has_pending(material):
    return MARK in (material or '')


def read_pending(material, ocr_document, data_dir):
    """Replace each OCR mark with its page's text, then delete the parked files."""
    base = uploads(data_dir).resolve()
    out, ocr, current, used = [], [], None, set()
    for line in (material or '').split('\n'):
        if not line.startswith(MARK):
            out.append(line)
            continue
        m = MARK_LINE.match(line)
        path = (base / m.group('file')).resolve() if m else None
        if not m or base not in path.parents or not path.is_file():
            continue                        # not one of ours (typed, damaged or already read): drop it
        page, name = int(m.group('page')), path.name
        if current != name:
            ocr.append('--- %s, read by OCR: check names and numbers ---' % name)
            current = name
        try:
            text = ocr_document(name, path.read_bytes(), page).get('text', '')
            ocr.append('[Page %d, read by OCR]\n%s' % (page + 1, text) if text else '[Page %d: no text]' % (page + 1))
        except Exception as e:
            ocr.append('[Page %d could not be read: %s]' % (page + 1, e))
        used.add(path.parent)
    for folder in used:
        for f in folder.iterdir():
            f.unlink()
        folder.rmdir()
    return '\n'.join(out + ([''] if out and ocr else []) + ocr).strip()
