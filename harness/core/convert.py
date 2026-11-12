"""File to text: MarkItDown for documents, OCR for scanned pages and pictures.

Moved from the workbench server (2026-11-08) with settings passed in: `source_limit` caps the
returned text, `max_bytes` the file size. Scanned PDF pages and pictures come back as `ocr_pages`
for the caller to read one at a time with `ocr_document`.
"""
import os
import tempfile
from pathlib import Path

from . import ocr

TYPES = {'.pdf', '.docx', '.pptx', '.xlsx', '.html', '.htm', '.txt', '.md', '.csv', '.json'} | ocr.IMAGE_TYPES
MAX_BYTES = 40 * 1024 * 1024


def _check_size(data, max_bytes):
    if len(data) > max_bytes:
        raise ValueError('File is larger than %d MB' % (max_bytes // 1024 // 1024))


def convert_document(filename, data, source_limit=150000, max_bytes=MAX_BYTES):
    """Turn a file into text with MarkItDown; list scanned pages for OCR."""
    ext = Path(filename or '').suffix.lower()
    if ext not in TYPES:
        raise ValueError('Unsupported file type. Use: ' + ', '.join(sorted(TYPES)))
    _check_size(data, max_bytes)
    # Pictures and scanned PDF pages have no text layer: the caller reads them one at a time
    # with ocr_document (the local vision model), listed here in ocr_pages.
    if ext in ocr.IMAGE_TYPES:
        pages = ocr.image_pages(data, ext)
        return {'filename': filename, 'text': '', 'chars': 0, 'truncated': False, 'limit': source_limit,
                'pages': pages, 'ocr_pages': list(range(min(pages, ocr.MAX_OCR_PAGES))),
                'ocr_skipped': max(0, pages - ocr.MAX_OCR_PAGES)}
    scans, pages = [], 1
    if ext == '.pdf' and b'%PDF' not in data[:1024]:
        # A blocked download often saves the site's "checking your browser" page under a .pdf name.
        looks_html = data.lstrip()[:200].lower().startswith((b'<!doctype html', b'<html'))
        raise ValueError('This is not a real PDF' + (' (it is a saved web page; the download was probably blocked '
                         '- open the link in your browser and download the PDF again)' if looks_html else ''))
    if ext in ('.txt', '.md', '.csv', '.json'):
        text = data.decode('utf-8', errors='replace')
    else:
        from markitdown import MarkItDown
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as f:
            f.write(data)
            tmp = f.name
        try:
            try:
                text = MarkItDown(enable_plugins=False).convert(tmp).text_content or ''
            except Exception as e:
                if ext != '.pdf':
                    raise ValueError('This file could not be read (%s)' % str(e)[:120])
                text = ''                       # a scanned PDF can still be read page by page below
            if ext == '.pdf':
                try:
                    scans, pages = ocr.scanned_pages(tmp)
                except Exception:
                    if not text.strip():        # damaged beyond both readers
                        raise ValueError('This PDF is damaged and could not be read')
        finally:
            os.unlink(tmp)
    text = text.strip()
    if not text and not scans:
        raise ValueError('No text could be read from this file')
    return {'filename': filename, 'text': text[:source_limit], 'chars': len(text),
            'truncated': len(text) > source_limit, 'limit': source_limit, 'pages': pages,
            'ocr_pages': scans[:ocr.MAX_OCR_PAGES], 'ocr_skipped': max(0, len(scans) - ocr.MAX_OCR_PAGES)}


def ocr_document(filename, data, page, url, model, num_ctx, max_bytes=MAX_BYTES):
    """Transcribe one scanned PDF page or one picture with the local vision model at `url`."""
    ext = Path(filename or '').suffix.lower()
    if ext != '.pdf' and ext not in ocr.IMAGE_TYPES:
        raise ValueError('Only PDFs and pictures can be read this way')
    _check_size(data, max_bytes)
    out = ocr.read_page(ocr.page_png(data, ext, int(page)), url, model, num_ctx)
    return dict(out, page=int(page), model=model)
