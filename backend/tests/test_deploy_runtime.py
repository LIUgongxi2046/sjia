"""No-Docker regression checks for image layout and shared persistent file storage."""
import glob
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase
import yaml


ROOT = Path(__file__).resolve().parents[2]


class DeployRuntimeTests(SimpleTestCase):
    def test_api_and_worker_share_named_persistent_object_volume(self):
        compose = yaml.safe_load((ROOT / 'deploy/compose.yaml').read_text())
        mounts = []
        for name in ('api', 'worker'):
            service = compose['services'][name]
            destination = service['environment']['SJIA_FILE_OBJECT_ROOT']
            self.assertTrue(destination.startswith('/'))
            mount = next(value for value in service['volumes'] if value.split(':')[1] == destination)
            self.assertIn(mount.split(':')[0], compose['volumes'])
            mounts.append(mount)
            self.assertIn('SJIA_ENVIRONMENT', service['environment'])
            self.assertIn('SJIA_ALLOWED_HOSTS', service['environment'])
        self.assertEqual(mounts[0], mounts[1])

    def test_explicit_storage_root_is_used_by_settings(self):
        environment = {**os.environ, 'PYTHONPATH': str(ROOT / 'backend'),
            'SJIA_FILE_OBJECT_ROOT': '/var/lib/sjia/objects'}
        result = subprocess.run([sys.executable, '-c',
            'from config.settings.base import FILE_OBJECT_ROOT; print(FILE_OBJECT_ROOT)'],
            env=environment, capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), '/var/lib/sjia/objects')

    def test_dockerignore_excludes_local_credentials_and_virtualenv(self):
        patterns = (ROOT / '.dockerignore').read_text().splitlines()
        self.assertIn('**/.env', patterns)
        self.assertIn('**/.venv', patterns)
        self.assertIn('.backend-work', patterns)

    def test_docker_copy_layout_loads_wsgi_api_and_worker_without_database(self):
        # Stage the real COPY sources rather than importing from the complete checkout.
        # This exercises missing schema assets without claiming a Docker image was built.
        with TemporaryDirectory() as directory:
            stage = Path(directory)
            for line in (ROOT / 'deploy/Dockerfile').read_text().splitlines():
                if not line.startswith('COPY '):
                    continue
                _, source, destination = shlex.split(line)
                destination = destination.removeprefix('/app/').removeprefix('./')
                matches = glob.glob(str(ROOT / source))
                self.assertTrue(matches, f'Missing COPY source: {source}')
                for name in matches:
                    path = Path(name)
                    target = stage / destination
                    if destination.endswith('/'):
                        target /= path.name
                    if path.is_dir():
                        shutil.copytree(path, target, ignore=shutil.ignore_patterns(
                            '.venv', '__pycache__', '.env', '.env.*', '*.pyc', 'staticfiles'))
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(path, target)
            # Match Dockerfile's explicit import-only build configuration. A real
            # production WSGI startup must refuse without admitted dependencies;
            # a package-layout smoke is not production readiness acceptance.
            environment = {**os.environ, 'DJANGO_SETTINGS_MODULE': 'config.settings.development',
                'SJIA_ENVIRONMENT': 'test', 'SJIA_ALLOWED_HOSTS': 'localhost',
                'SJIA_SECRET_KEY': 'synthetic-build-smoke-only', 'SJIA_DB_NAME': 'unused',
                'SJIA_DB_USER': 'unused', 'PYTHONPATH': str(stage / 'backend')}
            result = subprocess.run([sys.executable, str(stage / 'deploy/smoke_runtime.py')],
                cwd=stage, env=environment, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('Runtime smoke passed', result.stdout)
            self.assertIn('import-only; not production readiness', result.stdout)
            (stage / 'alert-design/policy.schema.json').unlink()
            missing = subprocess.run([sys.executable, str(stage / 'deploy/smoke_runtime.py')],
                cwd=stage, env=environment, capture_output=True, text=True, timeout=60)
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn('policy.schema.json', missing.stderr)
