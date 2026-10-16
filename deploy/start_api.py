"""Create one private metrics directory for exactly one Gunicorn master lifetime.

An exclusive fresh directory avoids old/recycled PID files and accidental cleanup
of a running sibling API's counters. Container tmpfs removes abandoned directories.
"""
import os
from pathlib import Path
import tempfile


def main():
    base = Path('/run/sjia-metrics')
    base.mkdir(parents=True, exist_ok=True)
    os.environ['PROMETHEUS_MULTIPROC_DIR'] = tempfile.mkdtemp(prefix='api-', dir=base)
    os.execvp('gunicorn', ['gunicorn', 'config.wsgi:application', '--bind', '0.0.0.0:8000',
        '--workers', os.environ.get('SJIA_API_WORKERS', '3'), '--config', '/app/deploy/gunicorn.conf.py'])


if __name__ == '__main__':
    main()
