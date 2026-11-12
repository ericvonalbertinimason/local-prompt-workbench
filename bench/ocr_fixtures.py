"""Build hard OCR cases from two known pages. Runs inside the workbench container:

  docker exec prompt-workbench python /tmp/ocr_fixtures.py /tmp/syllabus.pdf /tmp/twocol.pdf /tmp/ocr_cases

Cases: plain scan, sideways scan, PDF /Rotate, phone photo with EXIF rotation, rough phone photo
(skew, blur, noise, JPEG), low-resolution scan, scanned page with a library text footer (JSTOR
style), two-column scan, multi-page TIFF.
"""
import ctypes
import io
import random
import sys
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as raw
from PIL import Image, ImageFilter

syllabus_pdf, twocol_pdf, out = sys.argv[1], sys.argv[2], Path(sys.argv[3])
out.mkdir(parents=True, exist_ok=True)


def page_image(path, dpi):
    return pdfium.PdfDocument(path)[0].render(scale=dpi / 72).to_pil().convert('L')


def image_pdf(img, dpi):
    b = io.BytesIO()
    img.save(b, 'PDF', resolution=dpi)
    return b.getvalue()


S, T = page_image(syllabus_pdf, 200), page_image(twocol_pdf, 200)
cases = {}
cases['scan_plain.pdf'] = image_pdf(S, 200)
cases['scan_sideways.pdf'] = image_pdf(S.rotate(90, expand=True, fillcolor=255), 200)

# Upright content but the page carries /Rotate 90 (some scanners do this).
doc = pdfium.PdfDocument(image_pdf(S.rotate(-90, expand=True, fillcolor=255), 200))
doc[0].set_rotation(90)
b = io.BytesIO()
doc.save(b)
cases['scan_pdf_rotate.pdf'] = b.getvalue()

# Phone camera: pixels stored sideways, EXIF Orientation 6 says "rotate 90 degrees clockwise to view".
photo = S.convert('RGB').resize((S.width * 3 // 4, S.height * 3 // 4))
exif = Image.Exif()
exif[0x0112] = 6
b = io.BytesIO()
photo.rotate(90, expand=True).save(b, 'JPEG', quality=85, exif=exif)
cases['photo_exif.jpg'] = b.getvalue()

# Rough photo: grey paper, 3 degree skew, blur, noise, heavy JPEG.
random.seed(7)
rough = S.point(lambda p: 70 + p * 0.62).rotate(3, expand=True, fillcolor=150, resample=Image.BICUBIC)
rough = rough.filter(ImageFilter.GaussianBlur(1.1))
px = rough.load()
for _ in range(rough.width * rough.height // 12):
    x, y = random.randrange(rough.width), random.randrange(rough.height)
    px[x, y] = max(0, min(255, px[x, y] + random.randint(-45, 45)))
rough = rough.resize((rough.width * 2 // 3, rough.height * 2 // 3))
b = io.BytesIO()
rough.convert('RGB').save(b, 'JPEG', quality=35)
cases['photo_rough.jpg'] = b.getvalue()

low = page_image(syllabus_pdf, 100).filter(ImageFilter.GaussianBlur(0.6))
cases['scan_lowres.pdf'] = image_pdf(low, 100)

# Library download: a scanned page plus a real text-layer footer on the same page.
doc = pdfium.PdfDocument(image_pdf(T, 200))
page = doc[0]
footer = ('This content downloaded from 128.148.252.10 on Mon, 26 Oct 2026 14:02:11 UTC. '
          'All use subject to https://about.jstor.org/terms')
obj = raw.FPDFPageObj_NewTextObj(doc.raw, b'Helvetica', 7.0)
text = (footer + '\0').encode('utf-16-le')
raw.FPDFText_SetText(obj, ctypes.cast(ctypes.create_string_buffer(text), ctypes.POINTER(raw.FPDF_WCHAR)))
raw.FPDFPageObj_Transform(obj, 1, 0, 0, 1, 40, 18)
raw.FPDFPage_InsertObject(page.raw, obj)
raw.FPDFPage_GenerateContent(page.raw)
b = io.BytesIO()
doc.save(b)
cases['scan_library_footer.pdf'] = b.getvalue()

cases['scan_twocol.pdf'] = image_pdf(T, 200)

S.convert('RGB').save(out / 'pages.tiff', save_all=True, append_images=[T.convert('RGB')], compression='raw')
cases['pages.tiff'] = (out / 'pages.tiff').read_bytes()

for name, data in cases.items():
    (out / name).write_bytes(data)
    print(name, len(data))
