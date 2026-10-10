import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID


ROOT = Path(__file__).resolve().parents[2]
WINDOW = ROOT / '.planning-work' / 'parallel' / 'window1'
SIMULATION = ROOT / '.simulation-work'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def read_bound_json(relative_path, expected_sha256, allowed_root, label):
    path = (ROOT / relative_path).resolve()
    require(path.is_relative_to(allowed_root.resolve()), f'{label} path is outside its approved directory.')
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    require(digest == expected_sha256, f'{label} SHA-256 differs from the saved scope binding.')
    return path, json.loads(raw)


def read_time(value, label):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(parsed.tzinfo is not None and parsed.utcoffset() is not None,
        f'{label} must include a timezone offset.')
    return parsed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-tag', required=True)
    parser.add_argument('--scope-file', required=True, type=Path)
    args = parser.parse_args()
    require(re.fullmatch(r'[a-z0-9][a-z0-9-]{2,47}', args.run_tag) is not None,
        'Run tag must be a unique lowercase alphanumeric label.')

    scope_path = args.scope_file.resolve()
    require(scope_path.is_relative_to(WINDOW.resolve()), 'T29 scope must remain in the ignored window1 directory.')
    scope_raw = scope_path.read_bytes()
    scope = json.loads(scope_raw)
    require(scope.get('schema_version') == 't29-common-scope-1'
        and scope.get('patient_id') == str(UUID(scope['patient_id']))
        and scope.get('mode') == 'current'
        and scope.get('clinical_approval') == 'NOT_RUN',
        'Saved scope identity or simulation boundaries are invalid.')
    require(scope['known_at'] == scope['assessment_at'] == scope['source_cutoff_at'],
        'Saved scope timestamps do not form one common time range.')
    scope_time = read_time(scope['known_at'], 'Scope known_at')

    fixture_path, fixture = read_bound_json(scope['fixture_path'], scope['fixture_sha256'],
        SIMULATION, 'Business fixture')
    qualification_path, qualification = read_bound_json(scope['qualification_report_path'],
        scope['qualification_report_sha256'], WINDOW, 'Qualification report')
    qualification_log_path = ROOT / '.planning-work' / 'coordination' / 'root' / 'first-alert-main-qualification.log'
    qualification_log = json.loads(qualification_log_path.read_text(encoding='utf-8'))
    publication_path, publication = read_bound_json(scope['publication_evidence_path'],
        scope['publication_evidence_sha256'], SIMULATION, 'Publication evidence')

    require(fixture.get('mode') == 'SIMULATION_ONLY'
        and fixture.get('scenario') == 'INDEPENDENT_FIRST_ALERT'
        and str(UUID(fixture['patient_id'])) == scope['patient_id']
        and fixture.get('clinical_approval') == 'NOT_RUN'
        and fixture.get('release_id') == scope['release_id'],
        'Saved scope does not match the explicitly simulated first-alert fixture.')
    require(qualification.get('mode') == 'SIMULATION_ONLY'
        and qualification.get('status') == 'CURRENT_QUALIFICATION_READBACK'
        and qualification_log.get('saved_and_read') is True
        and qualification_log.get('output') == str(qualification_path.relative_to(ROOT))
        and qualification.get('database') == {'name': 'sjia_simulation', 'schema': 'sjia', 'port': 5433},
        'Saved qualification report does not identify the approved simulation database readback.')
    release = qualification['active_release']
    validation = release['validation_config']
    require(release.get('status') == 'enabled'
        and release.get('id') == scope['release_id']
        and release.get('publication_epoch') == scope['publication_epoch']
        and release.get('gate_publication_epoch') == scope['publication_epoch']
        and validation.get('id') == scope['validation_id']
        and release.get('qualification_reason') is None
        and validation.get('qualification_reason') is None
        and qualification['gate'].get('enabled') is True
        and qualification['gate'].get('publication_epoch') == scope['publication_epoch']
        and qualification['gate'].get('runtime_config_reason') is None
        and all(item.get('qualification_reason') is None for item in release['components'])
        and all(item.get('config_qualification_reason') is None for item in qualification['runtime_providers']),
        'Active release or runtime qualification differs from the saved scope.')
    require(publication.get('mode') == 'SIMULATION_ONLY'
        and publication.get('release_id') == scope['release_id']
        and publication.get('validation_config_id') == scope['validation_id'],
        'Saved publication evidence differs from the saved scope.')

    fixture_time = read_time(fixture['known_at'], 'Fixture known_at')
    qualification_time = read_time(qualification['observed_at'], 'Qualification observed_at')
    publication_time = read_time(publication['created_at'], 'Publication created_at')
    require(scope_time > fixture_time and scope_time > qualification_time and scope_time > publication_time,
        'Common scope does not follow its bound fixture, qualification and publication evidence.')
    output_path = WINDOW / f't29-scope-binding-{args.run_tag}.json'
    require(not output_path.exists(), 'Scope binding report exists; choose a new run tag.')
    report = {'task_id': 'T29', 'mode': 'SIMULATION_ONLY',
        'status': 'PASS_SCOPE_BINDINGS_SAVED_AND_READ',
        'scope_path': str(scope_path.relative_to(ROOT)),
        'scope_sha256': hashlib.sha256(scope_raw).hexdigest(),
        'fixture_path': str(fixture_path.relative_to(ROOT)),
        'fixture_sha256': scope['fixture_sha256'],
        'qualification_path': str(qualification_path.relative_to(ROOT)),
        'qualification_sha256': scope['qualification_report_sha256'],
        'qualification_readback_log_path': str(qualification_log_path.relative_to(ROOT)),
        'qualification_readback_log_sha256': hashlib.sha256(qualification_log_path.read_bytes()).hexdigest(),
        'publication_path': str(publication_path.relative_to(ROOT)),
        'publication_sha256': scope['publication_evidence_sha256'],
        'patient_id': scope['patient_id'], 'release_id': scope['release_id'],
        'validation_id': scope['validation_id'], 'publication_epoch': scope['publication_epoch'],
        'known_at': scope['known_at'], 'clinical_approval': 'NOT_RUN',
        'business_task_execution': 'AWAITING_MAIN_WINDOW_REPORT',
        'checked_at': datetime.now(timezone.utc).isoformat(),
        'external_calls': 0, 'model_calls': 0}
    encoded = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + '\n'
    output_path.write_text(encoded, encoding='utf-8')
    reread = json.loads(output_path.read_text(encoding='utf-8'))
    require(reread == json.loads(encoded), 'Saved scope binding report differs from its readback.')
    print(json.dumps({'status': report['status'], 'saved_and_read': True,
        'report_path': str(output_path.relative_to(ROOT)),
        'scope_sha256': report['scope_sha256'], 'fixture_sha256': report['fixture_sha256'],
        'patient_id': report['patient_id'], 'release_id': report['release_id'],
        'publication_epoch': report['publication_epoch'],
        'business_task_execution': report['business_task_execution']}, sort_keys=True))


if __name__ == '__main__':
    main()
