import io
import math
import unicodedata
from html import escape

import pymupdf

from files.errors import FileRejected
from files.storage import validate_image


PARSER_VERSION = 'pymupdf-source-pages-1'
MEDIA_TYPES = ('image/png', 'image/jpeg', 'image/webp', 'application/pdf', 'text/plain')
DPI = 144


def text_document(data, max_pages):
    text = data.decode('utf-8-sig', errors='strict')
    if not text.strip() or any(ord(character) < 32 and character not in '\t\r\n\f' for character in text):
        raise FileRejected('TEXT_CONTENT_INVALID')
    stream = io.BytesIO()
    story = pymupdf.Story(html='<pre>' + escape(text) + '</pre>',
        user_css='pre { white-space: pre-wrap; font-family: sans-serif; font-size: 12pt; line-height: 1.5; }')
    with pymupdf.DocumentWriter(stream) as writer:
        more = True
        count = 0
        while more:
            count += 1
            if count > max_pages:
                raise FileRejected('FILE_PAGE_LIMIT_EXCEEDED')
            device = writer.begin_page(pymupdf.Rect(0, 0, 595, 842))
            more, filled = story.place(pymupdf.Rect(36, 36, 559, 806))
            if pymupdf.Rect(filled).is_empty:
                raise FileRejected('TEXT_PAGE_LAYOUT_INVALID')
            story.draw(device)
            writer.end_page()
    data = stream.getvalue()
    with pymupdf.open(stream=data, filetype='pdf') as document:
        spans = [span for page in document for block in page.get_text('dict', clip=pymupdf.INFINITE_RECT())['blocks']
            for line in block.get('lines', []) for span in line['spans']]
        recovered = ''.join(span['text'] for span in spans)
        expected = ''.join(character for character in unicodedata.normalize('NFKC', text) if not character.isspace())
        actual = ''.join(character for character in unicodedata.normalize('NFKC', recovered) if not character.isspace())
        if expected != actual or any(span['bbox'][0] < 0 or span['bbox'][1] < 0 or span['bbox'][2] > 595 or span['bbox'][3] > 842 for span in spans):
            raise FileRejected('TEXT_PAGE_LAYOUT_INVALID')
    return data


def document_bytes(data, media_type, max_pages):
    if media_type == 'text/plain':
        return text_document(data, max_pages)
    if media_type != 'application/pdf':
        raise FileRejected('FILE_TYPE_INVALID')
    return data


def document_pages(data, media_type, limits, page_number=None):
    source = document_bytes(data, media_type, limits['max_pages'])
    with pymupdf.open(stream=source, filetype='pdf') as document:
        if not document.is_pdf or document.needs_pass or document.is_repaired:
            raise FileRejected('PDF_CONTENT_INVALID')
        if not 1 <= document.page_count <= limits['max_pages']:
            raise FileRejected('FILE_PAGE_LIMIT_EXCEEDED')
        if page_number is not None and not 1 <= page_number <= document.page_count:
            raise FileRejected('FILE_PAGE_NUMBER_INVALID')
        rows = []
        for number, page in enumerate(document, start=1):
            width, height = math.ceil(page.rect.width * DPI / 72), math.ceil(page.rect.height * DPI / 72)
            if width <= 0 or height <= 0 or width * height > limits['max_pixels']:
                raise FileRejected('FILE_PIXEL_LIMIT_EXCEEDED')
            row = {'number': number, 'width': width, 'height': height, 'rotation': page.rotation}
            if page_number == number:
                pixmap = page.get_pixmap(dpi=DPI, colorspace=pymupdf.csRGB, alpha=False, annots=True)
                row.update(width=pixmap.width, height=pixmap.height, data=pixmap.tobytes('png'))
            rows.append(row)
        return rows


def inspect_source(data, media_type, limits=None):
    if media_type not in MEDIA_TYPES:
        raise FileRejected('FILE_TYPE_INVALID')
    if media_type.startswith('image/'):
        info = validate_image(data)
        expected = {'image/png': 'PNG', 'image/jpeg': 'JPEG', 'image/webp': 'WEBP'}[media_type]
        if info['format'] != expected:
            raise FileRejected('FILE_MEDIA_TYPE_MISMATCH')
        if limits is not None and info['width'] * info['height'] > limits['max_pixels']:
            raise FileRejected('FILE_PIXEL_LIMIT_EXCEEDED')
        return [{'number': 1, 'width': info['width'], 'height': info['height'], 'rotation': 0}]
    if limits is None:
        raise FileRejected('FILE_PARSING_CONFIGURATION_REQUIRED')
    return document_pages(data, media_type, limits)


def render_source_page(data, media_type, number, limits):
    if media_type.startswith('image/'):
        if number != 1:
            raise FileRejected('FILE_PAGE_NUMBER_INVALID')
        inspect_source(data, media_type, limits)
        return data
    rows = document_pages(data, media_type, limits, page_number=number)
    return rows[number - 1]['data']
