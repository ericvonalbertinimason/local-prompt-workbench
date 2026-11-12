"""Read scanned pages and photos with the local vision model (OCR for source material).

MarkItDown reads the text layer of a PDF; a scanned page has none, so it came back empty (or a
mixed PDF silently lost its scanned pages). Those pages, and attached images, are rendered to a
picture and transcribed by the assistant model, which has vision. Measured 2026-10-25 on a
200 dpi scan of a syllabus page: every date, name and number transcribed, table kept, ~24 s.
The page sends one page per request, so it can show progress and a long scan never times out.

Hardened 2026-10-26 (bench/ocr_eval.py): a scanned page with a text footer (library downloads
such as JSTOR add "This content downloaded from ..." on every page) is still read; every page of
a multi-page TIFF is read; phone photos are turned upright from their EXIF orientation; a line the
model repeats over and over is collapsed.
"""
import base64
import io
import json
import re
import time
import urllib.error
import urllib.request

IMAGE_TYPES = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.tif', '.tiff'}
MULTIPAGE_IMAGES = {'.tif', '.tiff'}
MAX_OCR_PAGES = 40           # about 15 minutes at ~20 s a page
MIN_TEXT_CHARS = 25          # less text than this and any picture: scanned
FOOTER_TEXT_CHARS = 600      # less text than this and a picture covering most of the page: scanned
COVER_SHARE = 0.5            # "most of the page"
DPI = 200
MAX_SIDE = 2000              # pixels; larger pictures are scaled down before the model sees them
PROMPT = ('Transcribe all text in this scanned page exactly as written, in reading order. Keep headings, '
          'and write tables as Markdown tables. Do not summarize, correct, translate or add anything. '
          'If a word is unreadable, write [unreadable]. If the page has no text, answer [no text].')


def _image_share(page):
    """Share of the page covered by its largest picture (0 when it has none)."""
    import pypdfium2.raw as pdfium_c
    width, height = page.get_size()
    best = 0.0
    for obj in page.get_objects(max_depth=2):
        if obj.type != pdfium_c.FPDF_PAGEOBJ_IMAGE:
            continue
        left, bottom, right, top = obj.get_bounds()       # pypdfium2 5 (was get_pos in 4)
        best = max(best, max(0.0, right - left) * max(0.0, top - bottom) / (width * height or 1))
    return best


def scanned_pages(path):
    """Indexes of PDF pages that are a picture without a usable text layer, and the page count.

    A page is scanned when it has a picture and (almost) no text, or when a picture covers most
    of it and its text is only a footer or stamp. Blank pages are left out: reading them would
    only cost time.
    """
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(path)
    try:
        pages = []
        for i in range(len(pdf)):
            page = pdf[i]
            chars = len(page.get_textpage().get_text_range().strip())
            if chars >= FOOTER_TEXT_CHARS:
                continue
            share = _image_share(page)
            if (chars < MIN_TEXT_CHARS and share > 0) or share >= COVER_SHARE:
                pages.append(i)
        return pages, len(pdf)
    finally:
        pdf.close()


def image_pages(data, ext):
    """Number of pages in a picture file (a TIFF can hold several)."""
    if ext not in MULTIPAGE_IMAGES:
        return 1
    from PIL import Image
    try:
        return max(1, getattr(Image.open(io.BytesIO(data)), 'n_frames', 1))
    except Exception:
        return 1


def page_png(data, ext, page=0):
    """One page of a PDF or picture file as an upright greyscale PNG the model can read."""
    from PIL import Image, ImageOps
    if ext == '.pdf':
        import pypdfium2 as pdfium
        try:
            pdf = pdfium.PdfDocument(data)
        except Exception:
            raise ValueError('This PDF could not be opened (it may be damaged)')
        try:
            if not 0 <= page < len(pdf):
                raise ValueError('This PDF has no page %d' % (page + 1))
            img = pdf[page].render(scale=DPI / 72).to_pil()
        finally:
            pdf.close()
    else:
        try:
            img = Image.open(io.BytesIO(data))
            if page:
                img.seek(page)
            img.load()
        except EOFError:
            raise ValueError('This picture has no page %d' % (page + 1))
        except Exception:
            raise ValueError('This picture could not be opened')
        img = ImageOps.exif_transpose(img)           # phone photos are often stored sideways
    img = img.convert('L')
    if max(img.size) > MAX_SIDE:
        img.thumbnail((MAX_SIDE, MAX_SIDE))
    buf = io.BytesIO()
    img.save(buf, 'PNG')
    return buf.getvalue()


def collapse_repeats(text, limit=3):
    """Keep at most `limit` copies of a line the model repeats back to back (a vision-model loop)."""
    out, run, prev = [], 0, None
    for line in text.splitlines():
        run = run + 1 if line.strip() and line == prev else 1
        prev = line
        if run <= limit:
            out.append(line)
        elif run == limit + 1:
            out.append('[the line above repeats; extra copies removed]')
    return '\n'.join(out)


def read_page(png, url, model, num_ctx, timeout=300):
    """Transcribe one page. num_ctx matches the resident model so Ollama does not reload it."""
    body = {'model': model, 'prompt': PROMPT, 'images': [base64.b64encode(png).decode()], 'stream': False,
            'think': False, 'keep_alive': -1,
            'options': {'temperature': 0, 'num_ctx': num_ctx, 'num_predict': 4000}}
    started = time.time()
    req = urllib.request.Request(url, json.dumps(body).encode(), {'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            text = (json.load(r).get('response') or '').strip()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ValueError('The local model did not answer (%s). Check that Ollama is running, then try again.'
                         % str(getattr(e, 'reason', e))[:80])
    if text.lower().strip('[]. ') == 'no text':
        text = ''
    text = re.sub(r'[ \t]+$', '', collapse_repeats(text), flags=re.M)
    return {'text': text, 'ms': int((time.time() - started) * 1000)}
