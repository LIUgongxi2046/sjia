"""Explicit offline entrypoint; verified backend/settings supplied by operator."""
import argparse
import json


def main():
    parser = argparse.ArgumentParser(description='Add one fictional family-experience batch without changing old data.')
    parser.add_argument('--username', required=True)
    parser.add_argument('--principal-id', required=True)
    parser.add_argument('--acknowledge-test-only', action='store_true', required=True)
    parser.add_argument('--check-only', action='store_true', help='Read identity and existing receipts without creating data')
    options = vars(parser.parse_args())
    check_only = options.pop('check_only')
    import django
    django.setup()
    from core.demo_worker import admit_family_worker
    from enrich_family_experience import enrich_family_experience, inspect_enrichment
    # No bypass: actual installed backend qualification/dependencies must pass.
    admit_family_worker(**options)
    operation = inspect_enrichment if check_only else enrich_family_experience
    print(json.dumps(operation(**options), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
