import json
from pathlib import Path

from files.errors import FileRejected
from files.source_pages import inspect_source
from files.storage import digest_bytes


TEXT_PROCESSING_VERSION = 'confirmed-text-purpose-1'


def text_processing_artifact_checksum():
    root = Path(__file__).resolve().parents[1]
    names = ['files/purpose_text.py', 'files/purpose_plan.py', 'files/purpose_schemas.py', 'files/purpose_jobs.py',
        'files/purpose.py', 'files/model_input.py', 'files/source_pages.py', 'requirements.lock.txt']
    manifest = {name: digest_bytes((root / name).read_bytes()) for name in names}
    return digest_bytes(json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode())


def process_confirmed_text(*, plan, original_bytes, prepared_bytes, original_media_type, limits):
    if plan.mode != 'prepared_text' or original_media_type != 'text/plain':
        raise FileRejected('PURPOSE_TEXT_INPUT_INVALID')
    if digest_bytes(original_bytes) != plan.original_checksum:
        raise FileRejected('ORIGINAL_CHECKSUM_MISMATCH')
    if prepared_bytes is None or digest_bytes(prepared_bytes) != plan.prepared_checksum:
        raise FileRejected('PREPARED_CHECKSUM_MISMATCH')
    if plan.redactions or plan.date_strategy != 'unchanged' or plan.date_transformation is not None:
        raise FileRejected('PURPOSE_TEXT_PLAN_INVALID')
    original_pages = inspect_source(original_bytes, 'text/plain', limits)
    prepared_pages = inspect_source(prepared_bytes, 'text/plain', limits)
    text = prepared_bytes.decode('utf-8', errors='strict')
    if not text.strip() or len(text) > 8000:
        raise FileRejected('PURPOSE_TEXT_CONTENT_INVALID')
    page = original_pages[0]
    return prepared_bytes, {'processing_version': TEXT_PROCESSING_VERSION,
        'original_checksum': digest_bytes(original_bytes), 'prepared_checksum': digest_bytes(prepared_bytes),
        'derived_checksum': digest_bytes(prepared_bytes), 'width': page['width'], 'height': page['height'],
        'metadata_removed': True, 'file_name_removed': True, 'regions': [],
        'clinical_integrity': 'pending_review', 'date_strategy': 'unchanged', 'date_transformation': None,
        'file_page_id': str(plan.file_page_id), 'source_page_number': 1,
        'source_page_numbers': [page['number'] for page in original_pages],
        'prepared_page_numbers': [page['number'] for page in prepared_pages], 'full_text_included': True,
        'source_media_type': 'text/plain',
        'output_media_type': 'text/plain'}
