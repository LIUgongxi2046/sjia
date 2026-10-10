import io
import json
from pathlib import Path

from PIL import Image, ImageDraw

from files.service import FileRejected
from files.storage import digest_bytes, validate_image
from files.source_pages import PARSER_VERSION, render_source_page


PROCESSING_VERSION = 'pillow-purpose-2'


def processing_artifact_checksum():
    root = Path(__file__).resolve().parents[1]
    names = ['files/purpose_processing.py', 'files/source_pages.py', 'files/storage.py', 'requirements.lock.txt']
    manifest = {name: digest_bytes((root / name).read_bytes()) for name in names}
    return digest_bytes(json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode())


def process_image(*, plan, original_bytes, prepared_bytes=None, original_media_type='image/png', page_number=1, limits=None):
    if digest_bytes(original_bytes) != plan.original_checksum:
        raise FileRejected('ORIGINAL_CHECKSUM_MISMATCH')
    if not original_media_type.startswith('image/') and limits is None:
        raise FileRejected('FILE_PARSING_CONFIGURATION_REQUIRED')
    source_bytes = render_source_page(original_bytes, original_media_type, page_number, limits) if limits is not None else original_bytes
    original_info = validate_image(source_bytes)
    if plan.mode == 'prepared_image':
        if prepared_bytes is None or digest_bytes(prepared_bytes) != plan.prepared_checksum:
            raise FileRejected('PREPARED_CHECKSUM_MISMATCH')
        source_bytes = prepared_bytes
    source_info = validate_image(source_bytes)
    if (source_info['width'], source_info['height']) != (original_info['width'], original_info['height']):
        raise FileRejected('PURPOSE_DIMENSIONS_CHANGED')
    with Image.open(io.BytesIO(source_bytes)) as source:
        if getattr(source, 'n_frames', 1) != 1:
            raise FileRejected('PURPOSE_MULTIFRAME_UNSUPPORTED')
        image = source.convert('RGB')
        image.info.clear()
        draw = ImageDraw.Draw(image)
        for region in plan.redactions:
            x0, y0, x1, y1 = region.box
            if not 0 <= x0 < x1 <= image.width or not 0 <= y0 < y1 <= image.height:
                raise FileRejected('PURPOSE_REGION_INVALID')
            # 坐标使用左闭右开，处理范围保持批准方案中的准确边界。
            draw.rectangle((x0, y0, x1 - 1, y1 - 1), fill=(255, 255, 255))
        output = io.BytesIO()
        image.save(output, format='PNG')
    data = output.getvalue()
    with Image.open(io.BytesIO(data)) as result:
        if result.info or result.size != (original_info['width'], original_info['height']):
            raise FileRejected('PURPOSE_IMAGE_VALIDATION_FAILED')
    return data, {
        'processing_version': PROCESSING_VERSION,
        'original_checksum': digest_bytes(original_bytes),
        'prepared_checksum': digest_bytes(prepared_bytes) if prepared_bytes is not None else None,
        'derived_checksum': digest_bytes(data), 'width': original_info['width'], 'height': original_info['height'],
        'metadata_removed': True, 'file_name_removed': True,
        'regions': [item.model_dump(mode='json') for item in plan.redactions],
        'clinical_integrity': 'pending_review', 'date_strategy': plan.date_strategy,
        'file_page_id': str(plan.file_page_id) if plan.file_page_id is not None else None,
        'source_page_number': page_number, 'source_media_type': original_media_type,
        'parser_version': PARSER_VERSION if not original_media_type.startswith('image/') else None,
        'date_transformation': plan.date_transformation.model_dump() if plan.date_transformation is not None else None,
    }
