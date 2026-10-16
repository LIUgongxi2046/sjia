"""Offline evaluation of a declared capture; never grants evidence approval.

python backend/tests/evaluate_clinical_capture.py --plan PLAN.json --capture CAPTURE.json --output REPORT.json
Use --schemas to export machine-readable input contracts. No DB/network used.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from case_details.clinical_metrics import ClinicalCapture, MeasurementPlan, evaluate
from case_details.clinical_metrics_v2 import ClinicalCaptureV2, MeasurementPlanV2
from case_details.clinical_intervals import interval_engine, interval_protocol_schema


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--capture', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--schemas', action='store_true')
    parser.add_argument('--interval-protocol', type=Path, help='Exact explicit candidate CI artifact; no method defaults')
    parser.add_argument('--engine', action='store_true', help='Write the local numerical engine identity only')
    args = parser.parse_args()
    if args.engine:
        result = interval_engine()
    elif args.schemas:
        result = {'measurement_plan': MeasurementPlan.model_json_schema(), 'result_capture': ClinicalCapture.model_json_schema(),
            'measurement_plan_v2': MeasurementPlanV2.model_json_schema(), 'result_capture_v2': ClinicalCaptureV2.model_json_schema(),
            'interval_protocol': interval_protocol_schema()}
    else:
        if args.plan is None or args.capture is None:
            parser.error('--plan and --capture are required for evaluation')
        paths = [args.plan.resolve(), args.capture.resolve()]
        if args.interval_protocol is not None:
            paths.append(args.interval_protocol.resolve())
        if args.output.resolve() in paths:
            parser.error('output cannot overwrite either input')
        contents = [path.read_bytes() for path in paths]
        interval_args = {}
        if args.interval_protocol is not None:
            interval_args = {'interval_protocol': json.loads(contents[2]),
                'interval_protocol_sha256': hashlib.sha256(contents[2]).hexdigest()}
        elif json.loads(contents[0]).get('confidence_interval_protocol_ref') is not None:
            interval_args = {'interval_protocol': None}
        result = evaluate(*[json.loads(content) for content in contents[:2]], **interval_args)
        result['input_sha256'] = dict(zip(('measurement_plan', 'result_capture', 'interval_protocol'),
            [hashlib.sha256(content).hexdigest() for content in contents]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    print('Saved numerical engine identity.' if args.engine else 'Saved input schemas.' if args.schemas else
        'Saved deterministic report; clinical validation is not established.')


if __name__ == '__main__':
    main()
