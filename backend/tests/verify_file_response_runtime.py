import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import resource

import django

django.setup()

from configurations.evidence import backend_artifact_manifest
from configurations.service import compute_checksum
from case_details.evaluation_inventory import SourceManifest
from files.http_content import verified_file_response
from files.models import FileObject
from files.storage_factory import storage_selection


parser = argparse.ArgumentParser(description='使用获准原件验证完整文件响应、摘要、资源关闭及内存范围')
parser.add_argument('--output', required=True, type=Path)
parser.add_argument('--expected-count', required=True, type=int)
parser.add_argument('--expected-bytes', required=True, type=int)
parser.add_argument('--max-rss-bytes', required=True, type=int)
parser.add_argument('--source-manifest', type=Path)
arguments = parser.parse_args()
os.umask(0o077)


def require(condition, code):
    if not condition:
        raise RuntimeError(code)


def maximum_rss():
    system = os.uname().sysname
    require(system in ('Darwin', 'Linux'), 'FILE_RESPONSE_EXPLICIT_RESOURCE_PLATFORM_REQUIRED')
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value if system == 'Darwin' else value * 1024


require(not arguments.output.exists() and arguments.expected_count > 0
    and arguments.expected_bytes > 0 and arguments.max_rss_bytes > 0, 'FILE_RESPONSE_EXACT_RUNTIME_INPUT_REQUIRED')
query = FileObject.objects.filter(purpose='original', status='available')
manifest_checksum = None
if arguments.source_manifest is not None:
    source_bytes = arguments.source_manifest.read_bytes()
    source_manifest = SourceManifest.model_validate_json(source_bytes)
    require(source_manifest.original_attachment_comparison == 'PASS'
        and len(source_manifest.sources) == arguments.expected_count, 'FILE_RESPONSE_APPROVED_SOURCE_MANIFEST_REQUIRED')
    expected_sources = {str(item.id): item for item in source_manifest.sources}
    query = query.filter(pk__in=expected_sources)
    manifest_checksum = hashlib.sha256(source_bytes).hexdigest()
files = list(query.order_by('size_bytes', 'id'))
require(len(files) == arguments.expected_count and sum(file.size_bytes for file in files) == arguments.expected_bytes,
    'FILE_RESPONSE_EXACT_APPROVED_ORIGINAL_SCOPE_REQUIRED')
if arguments.source_manifest is not None:
    require(all(file.checksum == expected_sources[str(file.id)].sha256
        and file.size_bytes == expected_sources[str(file.id)].size_bytes for file in files),
        'FILE_RESPONSE_SOURCE_MANIFEST_METADATA_MISMATCH')
artifact = compute_checksum(backend_artifact_manifest())
control = storage_selection.store.control_store.root if storage_selection.kind == 's3' else None
before = sorted(str(path.relative_to(control)) for path in control.rglob('*')) if control is not None else None
baseline = maximum_rss()
results = []
for index, file in enumerate(files, 1):
    response = verified_file_response(file)
    stream = response.file_to_stream
    try:
        require(response.streaming and response.status_code == 200 and response.block_size == 1024 * 1024
            and response.headers['Content-Length'] == str(file.size_bytes)
            and response.headers['Content-Type'] == file.media_type and response.headers['Accept-Ranges'] == 'bytes',
            'FILE_RESPONSE_ACTUAL_STREAMING_METADATA_REQUIRED')
        digest = hashlib.sha256()
        count = 0
        chunks = 0
        for chunk in response.streaming_content:
            require(0 < len(chunk) <= response.block_size, 'FILE_RESPONSE_BOUNDED_CHUNKS_REQUIRED')
            digest.update(chunk)
            count += len(chunk)
            chunks += 1
        require(count == file.size_bytes and digest.hexdigest() == file.checksum,
            'FILE_RESPONSE_EXACT_ORIGINAL_CONTENT_REQUIRED')
    finally:
        response.close()
    require(stream.closed and response.closed, 'FILE_RESPONSE_RESOURCE_MUST_BE_CLOSED')
    peak = maximum_rss()
    require(peak <= arguments.max_rss_bytes, 'FILE_RESPONSE_CURRENT_RESOURCE_BUDGET_EXCEEDED')
    results.append({'file_id': str(file.id), 'size_bytes': count, 'checksum': digest.hexdigest(),
        'chunks': chunks, 'resource_closed': True, 'maximum_rss_bytes': peak})
    if index % 10 == 0 or index == len(files):
        print(json.dumps({'originals_verified': index, 'read_bytes': sum(item['size_bytes'] for item in results),
            'maximum_rss_bytes': peak}), flush=True)
if control is not None:
    require(sorted(str(path.relative_to(control)) for path in control.rglob('*')) == before,
        'FILE_RESPONSE_NO_TEMPORARY_CONTENT_MAY_REMAIN')
require(compute_checksum(backend_artifact_manifest()) == artifact, 'FILE_RESPONSE_IMPLEMENTATION_CHANGED')
report = {'result': 'PASS', 'measured_at': datetime.now(timezone.utc).isoformat(), 'storage_kind': storage_selection.kind,
    'originals': results, 'original_count': len(results), 'read_bytes': sum(item['size_bytes'] for item in results),
    'backend_artifact_sha256': artifact, 'maximum_rss_bytes': maximum_rss(), 'baseline_maximum_rss_bytes': baseline,
    'maximum_allowed_rss_bytes': arguments.max_rss_bytes, 'temporary_content_remaining': False,
    'source_manifest_sha256': manifest_checksum,
    'verification_source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    'new_model_calls': 0, 'clinical_qualification': 'NOT_ESTABLISHED'}
with arguments.output.open('x') as output:
    json.dump(report, output, indent=2)
print(json.dumps({'result': 'PASS', 'storage_kind': storage_selection.kind, 'originals': len(results),
    'read_bytes': report['read_bytes'], 'maximum_rss_bytes': report['maximum_rss_bytes']}))
