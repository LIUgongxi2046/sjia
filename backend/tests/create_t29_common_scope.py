import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import django


ROOT = Path(__file__).resolve().parents[2]
WINDOW = ROOT / '.planning-work' / 'parallel' / 'window1'
SIMULATION = ROOT / '.simulation-work'
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.simulation')
django.setup()


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def read_aware_time(value, name):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(parsed.tzinfo is not None and parsed.utcoffset() is not None,
        f'{name} must include a timezone.')
    return parsed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-tag', required=True)
    parser.add_argument('--fixture-file', required=True, type=Path)
    parser.add_argument('--qualification-report', required=True, type=Path)
    args = parser.parse_args()
    require(re.fullmatch(r'[a-z0-9][a-z0-9-]{2,47}', args.run_tag) is not None,
        'Run tag must be a unique lowercase alphanumeric label.')
    fixture_path = args.fixture_file.resolve()
    qualification_path = args.qualification_report.resolve()
    publication_path = (SIMULATION / 't29-publication-fixture.json').resolve()
    require(fixture_path.is_relative_to(SIMULATION.resolve()),
        'Business fixture must remain in the simulation-work directory.')
    require(qualification_path.is_relative_to(WINDOW.resolve()),
        'Qualification report must remain in the window1 planning directory.')
    output = WINDOW / f't29-common-scope-{args.run_tag}.json'
    require(not output.exists(), 'Common scope path already exists; choose a new run tag.')

    fixture_bytes = fixture_path.read_bytes()
    fixture = json.loads(fixture_bytes)
    require(fixture.get('mode') == 'SIMULATION_ONLY', 'Business fixture must be explicitly marked SIMULATION_ONLY.')
    patient_id = str(UUID(fixture['patient_id']))
    fixture_known_at = read_aware_time(fixture['known_at'], 'Business fixture known_at')

    qualification_bytes = qualification_path.read_bytes()
    qualification = json.loads(qualification_bytes)
    require(qualification.get('mode') == 'SIMULATION_ONLY'
        and qualification.get('status') == 'CURRENT_QUALIFICATION_READBACK'
        and qualification.get('database') == {'name': 'sjia_simulation', 'schema': 'sjia', 'port': 5433},
        'Qualification report is not a current read from sjia_simulation.')
    active_release = qualification['active_release']
    require(active_release is not None and active_release['status'] == 'enabled'
        and active_release['id'] == fixture.get('release_id')
        and active_release['publication_epoch'] == active_release['gate_publication_epoch']
        and active_release['qualification_reason'] is None
        and active_release['validation_config']['qualification_reason'] is None
        and qualification['gate']['enabled'] is True
        and qualification['gate']['runtime_config_reason'] is None
        and all(component['qualification_reason'] is None for component in active_release['components'])
        and all(provider['config_qualification_reason'] is None for provider in qualification['runtime_providers']),
        'Current simulation release or runtime qualification is unavailable.')
    qualification_at = read_aware_time(qualification['observed_at'], 'Qualification observation time')

    publication_bytes = publication_path.read_bytes()
    publication = json.loads(publication_bytes)
    require(publication.get('mode') == 'SIMULATION_ONLY'
        and publication['release_id'] == active_release['id']
        and publication['validation_config_id'] == active_release['validation_config']['id'],
        'Saved simulation publication evidence differs from the active qualified release.')
    publication_at = read_aware_time(publication['created_at'], 'Publication evidence time')
    generated_at = datetime.now(timezone.utc)
    require(generated_at > fixture_known_at and generated_at > qualification_at and generated_at > publication_at,
        'Current time must follow the source fixture, qualification read and publication evidence.')
    known_at = generated_at.isoformat()
    scope = {'schema_version': 't29-common-scope-1', 'mode': 'current',
        'known_at': known_at, 'assessment_at': known_at, 'source_cutoff_at': known_at,
        'qualification_verified_at': qualification_at.isoformat(),
        'publication_evidence_saved_at': publication_at.isoformat(),
        'release_id': active_release['id'],
        'validation_id': active_release['validation_config']['id'],
        'api_base': 'http://127.0.0.1:8001', 'patient_id': patient_id,
        'fixture_path': str(fixture_path.relative_to(ROOT)),
        'fixture_sha256': hashlib.sha256(fixture_bytes).hexdigest(),
        'qualification_report_path': str(qualification_path.relative_to(ROOT)),
        'qualification_report_sha256': hashlib.sha256(qualification_bytes).hexdigest(),
        'publication_evidence_path': str(publication_path.relative_to(ROOT)),
        'publication_evidence_sha256': hashlib.sha256(publication_bytes).hexdigest(),
        'publication_epoch': active_release['publication_epoch'],
        'clinical_approval': 'NOT_RUN'}
    encoded = json.dumps(scope, ensure_ascii=False, indent=2, sort_keys=True) + '\n'
    output.write_text(encoded, encoding='utf-8')
    reread = json.loads(output.read_text(encoding='utf-8'))
    require(reread == json.loads(encoded), 'Saved common scope differs from its readback.')
    print(json.dumps({'status': 'COMMON_SCOPE_SAVED_AND_READ', 'patient_id': patient_id,
        'release_id': active_release['id'], 'publication_epoch': active_release['publication_epoch'],
        'known_at': known_at, 'scope_path': str(output.relative_to(ROOT)),
        'scope_sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
        'saved_and_read': True}, sort_keys=True))


if __name__ == '__main__':
    main()
