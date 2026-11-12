"""Regression test for the source-material converter (POST /api/convert).

Builds one small file of each supported type containing a known phrase, sends
each through the live workbench, and checks the phrase comes back. Also checks
the guard rails: unsupported types and oversize files are refused, and the
retired gateway port 8790 stays closed.
Scanned pages and pictures (2026-10-25): listed for OCR, blank pages skipped, and
one scanned page read back through /api/ocr with the local vision model.

  python bench/test_convert.py
"""
import base64
import io
import json
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile

PHRASE = 'Brightwater Rowing Club spring regatta'
UI = 'http://127.0.0.1:8789'
HEADERS = {'Content-Type': 'application/json', 'Host': '127.0.0.1:8789', 'Origin': UI, 'X-Harness-Client': 'workbench'}


def post(url, body, headers=HEADERS):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or '{}')


def make_docx(text):
    parts = {
        '[Content_Types].xml': '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        '_rels/.rels': '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
        'word/document.xml': '<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body><w:p><w:r><w:t>%s</w:t></w:r></w:p></w:body></w:document>' % text,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        for name, xml in parts.items():
            z.writestr(name, xml)
    return buf.getvalue()


def make_pdf(text):
    objs = [b'<< /Type /Catalog /Pages 2 0 R >>',
            b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
            b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>',
            None,
            b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    stream = ('BT /F1 14 Tf 72 700 Td (%s) Tj ET' % text).encode()
    objs[3] = b'<< /Length %d >>\nstream\n' % len(stream) + stream + b'\nendstream'
    out, offsets = io.BytesIO(), []
    out.write(b'%PDF-1.4\n')
    for i, o in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(b'%d 0 obj\n' % i + o + b'\nendobj\n')
    xref = out.tell()
    out.write(b'xref\n0 %d\n0000000000 65535 f \n' % (len(objs) + 1))
    for off in offsets:
        out.write(b'%010d 00000 n \n' % off)
    out.write(b'trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % (len(objs) + 1, xref))
    return out.getvalue()


def office_in_container(kind, text):
    """python-pptx and openpyxl ship in the workbench image, not necessarily on the host."""
    code = {
        'xlsx': "import openpyxl,io,sys;w=openpyxl.Workbook();w.active['A1']=%r;b=io.BytesIO();w.save(b);sys.stdout.buffer.write(b.getvalue())",
        'pptx': "import pptx,io,sys;p=pptx.Presentation();s=p.slides.add_slide(p.slide_layouts[5]);s.shapes.title.text=%r;b=io.BytesIO();p.save(b);sys.stdout.buffer.write(b.getvalue())",
    }[kind] % text
    return subprocess.run(['docker', 'exec', 'prompt-workbench', 'python', '-c', code], capture_output=True, check=True).stdout


SCANS = r"""
import base64, io, json, sys, pypdfium2 as pdfium
text_pdf = pdfium.PdfDocument(base64.b64decode(sys.argv[1]))
img = text_pdf[0].render(scale=200 / 72).to_pil().convert('L')
out = {}
b = io.BytesIO(); img.save(b, 'PNG'); out['photo.png'] = b.getvalue()
b = io.BytesIO(); img.save(b, 'PDF', resolution=200); scan = b.getvalue(); out['scan.pdf'] = scan
mixed = pdfium.PdfDocument.new()
mixed.import_pages(text_pdf)
mixed.new_page(612, 792)                      # blank page: must not be sent to OCR
mixed.import_pages(pdfium.PdfDocument(scan))
b = io.BytesIO(); mixed.save(b); out['mixed.pdf'] = b.getvalue()
print(json.dumps({k: base64.b64encode(v).decode() for k, v in out.items()}))
"""


def scans_in_container(pdf):
    """A photo, a scanned PDF and a mixed PDF (text page, blank page, scanned page) of the phrase."""
    raw = subprocess.run(['docker', 'exec', 'prompt-workbench', 'python', '-c', SCANS, base64.b64encode(pdf).decode()],
                         capture_output=True, check=True).stdout
    return {k: base64.b64decode(v) for k, v in json.loads(raw).items()}


def main():
    files = {
        'notes.txt': PHRASE.encode(),
        'notes.md': ('# Regatta\n\n%s\n' % PHRASE).encode(),
        'page.html': ('<html><body><h1>Event</h1><p>%s</p></body></html>' % PHRASE).encode(),
        'brief.docx': make_docx(PHRASE),
        'flyer.pdf': make_pdf(PHRASE),
        'budget.xlsx': office_in_container('xlsx', PHRASE),
        'deck.pptx': office_in_container('pptx', PHRASE),
    }
    results = []
    for name, data in files.items():
        status, body = post(UI + '/api/convert', {'filename': name, 'data': base64.b64encode(data).decode()})
        ok = status == 200 and PHRASE.lower() in body.get('text', '').lower()
        results.append((ok, 'convert %-12s -> HTTP %s, %s' % (name, status, ('%d chars' % body['chars']) if status == 200 else body.get('error'))))

    scans = scans_in_container(make_pdf(PHRASE))
    expect = {'photo.png': ('', [0]), 'scan.pdf': ('', [0]), 'mixed.pdf': (PHRASE, [2])}
    for name, (text, pages) in expect.items():
        status, body = post(UI + '/api/convert', {'filename': name, 'data': base64.b64encode(scans[name]).decode()})
        ok = status == 200 and body.get('ocr_pages') == pages and (text.lower() in body.get('text', '').lower())
        results.append((ok, 'convert %-12s -> HTTP %s, OCR pages %s' % (name, status, body.get('ocr_pages', body.get('error')))))
    status, body = post(UI + '/api/ocr', {'filename': 'mixed.pdf', 'data': base64.b64encode(scans['mixed.pdf']).decode(), 'page': 2})
    results.append((status == 200 and PHRASE.lower() in body.get('text', '').lower(),
                    'OCR scanned page       -> HTTP %s, %.1f s: %s' % (status, body.get('ms', 0) / 1000, body.get('text', body.get('error', ''))[:60])))
    status, body = post(UI + '/api/convert', {'filename': 'flyer.pdf', 'data': base64.b64encode(make_pdf(PHRASE)).decode()})
    results.append((status == 200 and body.get('ocr_pages') == [], 'text PDF not sent to OCR -> %s' % body.get('ocr_pages')))
    page = b'<!doctype html><html><head><title>Just a moment</title></head><body>Checking your browser</body></html>'
    status, body = post(UI + '/api/convert', {'filename': 'article.pdf', 'data': base64.b64encode(page).decode()})
    results.append((status == 400 and 'saved web page' in body.get('error', ''),
                    'web page named .pdf    -> HTTP %s %s' % (status, body.get('error', '')[:40])))
    status, body = post(UI + '/api/ocr', {'filename': 'broken.pdf', 'data': base64.b64encode(b'%PDF-1.4 broken').decode(), 'page': 0})
    results.append((status == 400, 'OCR on damaged PDF     -> HTTP %s %s' % (status, body.get('error', '')[:40])))
    status, body = post(UI + '/api/ocr', {'filename': 'notes.docx', 'data': '', 'page': 0})
    results.append((status == 400, 'OCR refuses non-PDF    -> HTTP %s' % status))

    status, body = post(UI + '/api/convert', {'filename': 'tool.exe', 'data': base64.b64encode(b'MZ').decode()})
    results.append((status == 400, 'reject .exe            -> HTTP %s %s' % (status, body.get('error', '')[:50])))
    big = base64.b64encode(b'x' * (40 * 1024 * 1024 + 1)).decode()
    status, body = post(UI + '/api/convert', {'filename': 'big.txt', 'data': big})
    results.append((status == 400, 'reject >40 MB file     -> HTTP %s %s' % (status, body.get('error', '')[:50])))
    status, _ = post(UI + '/api/convert', {'filename': 'notes.txt', 'data': ''}, headers={'Content-Type': 'application/json', 'Host': '127.0.0.1:8789'})
    results.append((status == 403, 'reject without UI hdr  -> HTTP %s' % status))
    # The ChatGPT/Sites gateway was removed on 2026-10-28: nothing may answer on its port.
    try:
        status, _ = post('http://127.0.0.1:8790/api/convert', {'filename': 'a.txt', 'data': ''}, headers={'Content-Type': 'application/json'})
    except OSError:
        status = 'closed'
    results.append((status == 'closed', 'retired gateway port 8790 -> %s' % status))

    for ok, line in results:
        print('  %s  %s' % ('PASS' if ok else 'FAIL', line))
    failed = sum(not ok for ok, _ in results)
    print('\n%d/%d passed' % (len(results) - failed, len(results)))
    sys.exit(failed)


if __name__ == '__main__':
    main()
