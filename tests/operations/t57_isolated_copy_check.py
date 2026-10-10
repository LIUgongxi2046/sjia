import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORK_PARENT = ROOT / '.planning-work' / 'parallel' / 'window4'
OBJECT_SOURCE = ROOT / '.simulation-work' / 'object-store'
EVIDENCE_SOURCE = ROOT / '.simulation-work' / 'config-evidence'
DEFAULT_REPORT = ROOT / 'docs' / 'testing' / 'T57-isolated-file-manifest-report.json'


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def manifest(directory):
    directory = Path(directory)
    if not directory.is_dir():
        raise RuntimeError(f'隔离恢复目录缺失：{directory}')
    files = sorted(path for path in directory.rglob('*') if path.is_file())
    entries = [f'{path.relative_to(directory).as_posix()}:{sha256(path)}' for path in files]
    return {'file_count': len(files), 'total_bytes': sum(path.stat().st_size for path in files),
        'inventory_sha256': hashlib.sha256('\n'.join(entries).encode()).hexdigest()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--report', default=str(DEFAULT_REPORT))
    parser.add_argument('--run-name', default='isolated')
    parser.add_argument('--database-port', type=int, default=55471)
    args = parser.parse_args()
    if not args.run_name.startswith('isolated') or '/' in args.run_name or args.run_name in ('.', '..'):
        raise ValueError('恢复运行名必须以 isolated 开头且不得包含路径。')
    work_root = WORK_PARENT / args.run_name
    object_copy = work_root / 'object-store'
    evidence_copy = work_root / 'config-evidence'
    dump_path = work_root / 'sjia-simulation-source.dump'
    if not dump_path.is_file():
        raise RuntimeError('窗口四模拟数据库备份文件缺失。')
    object_source, object_copy_manifest = manifest(OBJECT_SOURCE), manifest(object_copy)
    evidence_source, evidence_copy_manifest = manifest(EVIDENCE_SOURCE), manifest(evidence_copy)
    report = {'task': 'T57', 'classification': 'SIMULATION_ONLY_RESTORED_COPY_INTEGRITY',
        'source_database': 'sjia_simulation', 'isolated_database': 'sjia_simulation',
        'isolated_database_port': args.database_port, 'run_root': str(work_root),
        'source_dump': {'bytes': dump_path.stat().st_size, 'sha256': sha256(dump_path)},
        'object_copy': {'source': object_source, 'restored': object_copy_manifest, 'match': object_source == object_copy_manifest},
        'configuration_evidence_copy': {'source': evidence_source, 'restored': evidence_copy_manifest,
            'match': evidence_source == evidence_copy_manifest},
        'saved_and_reread': True}
    path = Path(args.report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    if json.loads(path.read_text()) != report:
        raise RuntimeError('T57 文件副本报告保存后回读不一致。')
    if not report['object_copy']['match'] or not report['configuration_evidence_copy']['match']:
        raise RuntimeError('隔离恢复文件副本摘要不匹配。')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
