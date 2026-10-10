import json
from decimal import Decimal
from pathlib import Path
from zoneinfo import available_timezones

from jsonschema import Draft202012Validator, FormatChecker


SCHEMA = json.loads(Path(__file__).with_name('quota.schema.json').read_text())
Draft202012Validator.check_schema(SCHEMA)
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())


def quota_reason(payload):
    if next(VALIDATOR.iter_errors(payload), None) is not None:
        return 'CONFIG_PAYLOAD_INVALID'
    amounts = [Decimal(payload[field]) for field in
        ('per_page_reservation', 'per_batch_budget', 'daily_budget')]
    if any(not amount.is_finite() or amount <= 0 for amount in amounts) or not amounts[0] <= amounts[1] <= amounts[2]:
        return 'CONFIG_ARCHIVE_BUDGET_INVALID'
    if payload['budget_timezone'] not in available_timezones():
        return 'CONFIG_ARCHIVE_TIMEZONE_INVALID'
    if payload['max_inflight_pages'] > payload['max_pages']:
        return 'CONFIG_ARCHIVE_CONCURRENCY_INVALID'
    if (payload['heartbeat_seconds'] * 2 >= payload['lease_seconds']
            or payload['visibility_timeout_seconds'] <= payload['execution_seconds']
            or payload['execution_seconds'] > payload['deadline_seconds']
            or payload['queue_wait_seconds'] + payload['execution_seconds'] > payload['deadline_seconds']
            or payload['queue_wait_seconds'] > payload['acceptable_wait_seconds']
            or payload['acceptable_wait_seconds'] > payload['deadline_seconds']):
        return 'CONFIG_ARCHIVE_TIMING_INVALID'
    return None
