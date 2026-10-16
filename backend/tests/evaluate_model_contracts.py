"""Reproducible offline software contract gate. No provider or clinical evaluation."""
import argparse
import json
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')
import django

django.setup()

from recognition.invocation import contract_snapshot
from tests.test_model_invocation_governance import ModelContractTests, MultiPageBudgetTests


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path,
        default=Path(__file__).with_name('fixtures') / 'model_contracts.synthetic.json')
    args = parser.parse_args()
    snapshots = {kind: contract_snapshot(kind) for kind in ('report', 'therapy', 'voice', 'archive')}
    baseline = json.loads(args.baseline.read_text(encoding='utf-8'))
    contracts_match = baseline['contracts'] == snapshots
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(case)
        for case in (ModelContractTests, MultiPageBudgetTests))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    summary = {'dataset': 'SYNTHETIC_SOFTWARE_REGRESSION_ONLY', 'real_provider_calls': 0,
        'clinical_validation': False, 'contract_baseline_matches': contracts_match,
        'contracts': snapshots, 'tests_run': result.testsRun, 'failures': len(result.failures),
        'errors': len(result.errors), 'passed': contracts_match and result.wasSuccessful()}
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if summary['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
