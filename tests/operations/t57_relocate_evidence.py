import hashlib
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))

import django

django.setup()

from django.conf import settings
from sources.models import SourceRegistry, SourceRevision


def main():
    source_root = Path(os.environ['WINDOW4_SOURCE_CONFIG_EVIDENCE_ROOT']).resolve()
    target_root = Path(settings.CONFIG_EVIDENCE_ROOT).resolve(strict=True)
    relocated = 0
    verified = 0
    for source in SourceRegistry.objects.order_by('id'):
        current = Path(source.location)
        if not current.is_absolute():
            continue
        resolved = current.resolve(strict=False)
        if not resolved.is_relative_to(source_root):
            continue
        target = target_root / resolved.relative_to(source_root)
        if not target.is_file():
            raise RuntimeError(f'恢复后的配置证据文件缺失：{target.name}')
        revisions = list(SourceRevision.objects.filter(source=source).values_list('checksum', flat=True))
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        if revisions and any(value != digest for value in revisions):
            raise RuntimeError(f'恢复后的配置证据摘要不匹配：{target.name}')
        SourceRegistry.objects.filter(id=source.id).update(location=str(target))
        relocated += 1
        verified += 1
    print({'relocated_sources': relocated, 'verified_sources': verified})


if __name__ == '__main__':
    main()
