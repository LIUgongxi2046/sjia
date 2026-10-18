"""Bounded, fixed-workflow model contract. No clinical approval is implied by hashes.

Adapters return a receipt before parsing/accepting candidates so unexpected models,
invalid output, cancellation and unknown costs cannot erase a paid request.
"""
import hashlib
import json
import math
from decimal import Decimal, localcontext

from django.conf import settings
from django.utils import timezone


class InvocationRejected(ValueError):
    def __init__(self, reason_code):
        super().__init__(reason_code)
        self.reason_code = reason_code


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
        ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()


def contract_snapshot(kind, provider=None):
    from recognition import ark
    from recognition.schemas import ReportCandidates, TherapyCandidates, VoiceCandidates

    if kind == 'archive':
        from recognition.archive_extraction import SYSTEM, PROMPT, SCHEMA
        system, prompt, schema = SYSTEM, PROMPT, SCHEMA
    else:
        definitions = {'report': (ark.OCR_SYSTEM, ark.OCR_PROMPT, ReportCandidates),
            'therapy': (ark.THERAPY_SYSTEM, ark.THERAPY_PROMPT, TherapyCandidates),
            'voice': (ark.VOICE_SYSTEM, ark.VOICE_PROMPT, VoiceCandidates)}
        if kind not in definitions:
            raise InvocationRejected('MODEL_CONTRACT_UNREGISTERED')
        system, prompt, model = definitions[kind]
        if kind == 'report':
            from recognition.report_times import report_candidate_model, report_schema_version
            model = report_candidate_model(provider)
            if report_schema_version(provider) == 'report-recognition-3':
                prompt = ark.OCR_TIME_PROMPT
        schema = model.model_json_schema()
    return {'contract_version': 'fixed-model-invocation-1', 'kind': kind,
        'prompt_sha256': canonical_hash({'system': system, 'prompt_template': prompt}),
        'schema_sha256': canonical_hash(schema)}


def provider_timeout_seconds(task, execution_until, provider_entry=None):
    now = timezone.now()
    if task.lease_until is None or task.lease_until <= now or task.status != 'running':
        raise InvocationRejected('MODEL_PROVIDER_CALL_AUTHORITY_UNAVAILABLE')
    remaining = (min(task.deadline_at, execution_until) - now).total_seconds()
    limits = [remaining, float(settings.ARK_REQUEST_TIMEOUT_SECONDS)]
    if provider_entry is not None:
        limits.append(provider_entry['total_timeout_ms'] / 1000)
    if any(not math.isfinite(value) or value <= 0 for value in limits):
        raise InvocationRejected('MODEL_PROVIDER_CALL_AUTHORITY_UNAVAILABLE')
    return min(limits)


def invoke_model(messages, provider, kind, *, before_provider_call, timeout_seconds,
        max_tokens, thinking=None):
    from recognition.ark import _completion

    if provider is None or not callable(before_provider_call):
        raise InvocationRejected('MODEL_GOVERNED_INVOCATION_REQUIRED')
    if provider.payload['endpoint'].rstrip('/') != settings.ARK_BASE_URL.rstrip('/'):
        raise InvocationRejected('MODEL_PROVIDER_ENDPOINT_MISMATCH')
    model = provider.payload['model_version']
    if not isinstance(model, str) or not model.strip():
        raise InvocationRejected('MODEL_REQUESTED_IDENTITY_REQUIRED')
    if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
        raise InvocationRejected('MODEL_TIMEOUT_INVALID')
    if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens < 1:
        raise InvocationRejected('MODEL_TOKEN_LIMIT_INVALID')
    parameters = {'model': model, 'max_tokens': max_tokens, 'thinking': thinking}
    evidence = {**contract_snapshot(kind, provider), 'messages_sha256': canonical_hash(messages),
        'parameters_sha256': canonical_hash(parameters), 'requested_model': model,
        'provider_config_id': str(provider.id), 'provider_checksum': provider.checksum,
        'timeout_seconds': timeout_seconds, 'max_tokens': max_tokens,
        'phase': 'provider_started', 'provider_called': True}
    body, raw, http_response = _completion(messages, model=model, max_tokens=max_tokens, thinking=thinking,
        validate_output=False, timeout_seconds=timeout_seconds,
        before_provider_call=lambda: before_provider_call(evidence))
    returned_model = body.model if body is not None else None
    return {'provider_request_id': body.id if body is not None else http_response['request_id'],
        'model': returned_model, 'usage': body.usage if body is not None else None,
        'raw_response': raw, 'completion': body, 'invocation': {**evidence,
            'response_envelope_valid': body is not None, 'http_response': http_response,
            'returned_model': returned_model, 'model_authorized': returned_model == model,
            'phase': 'provider_finished'}}


def assert_authorized_result(result, provider):
    status = result['invocation']['http_response']['status']
    if status == 429:
        raise InvocationRejected('ARK_RATE_LIMITED')
    if not 200 <= status < 300:
        raise InvocationRejected('ARK_HTTP_ERROR')
    if result.get('completion') is None:
        raise InvocationRejected('MODEL_RESPONSE_ENVELOPE_INVALID')
    # No implicit alias, endpoint deployment-ID, or family-prefix matching.
    if result['model'] != provider.payload['model_version']:
        raise InvocationRejected('MODEL_RETURNED_IDENTITY_UNAUTHORIZED')


def checked_candidates(result, provider, candidate_model):
    assert_authorized_result(result, provider)
    body = result['completion']
    if len(body.choices) != 1:
        raise InvocationRejected('MODEL_CHOICE_COUNT_INVALID')
    choice = body.choices[0]
    if choice.finish_reason != 'stop' or choice.message.refusal or not choice.message.content:
        raise InvocationRejected('MODEL_RESPONSE_UNUSABLE')
    return candidate_model.model_validate_json(choice.message.content).model_dump()


def actual_cost(result, provider):
    usage = result.get('usage')
    if not isinstance(usage, dict) or 'prompt_tokens' not in usage or 'completion_tokens' not in usage:
        return None
    values = [usage['prompt_tokens'], usage['completion_tokens']]
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in values):
        raise InvocationRejected('MODEL_PROVIDER_USAGE_INVALID')
    prices = [Decimal(provider.payload[key]) for key in ('input_per_million', 'output_per_million')]
    if any(not value.is_finite() or value < 0 for value in prices):
        raise InvocationRejected('MODEL_PROVIDER_PRICING_INVALID')
    with localcontext() as arithmetic:
        arithmetic.prec = 80
        return sum((Decimal(value) * price for value, price in zip(values, prices)), Decimal('0')) / Decimal('1000000')


def failure_state(evidence, cancelled=False):
    calls = evidence.get('provider_calls')
    if calls is None:
        calls = [evidence]
    unknown = any((call.get('provider_called') or call.get('phase') in ('provider_started', 'provider_finished'))
        and not (call.get('provider_request_id') and call.get('raw_response_file_id'))
        and not (call.get('raw_response_file_id')
            and call.get('http_response', {}).get('status') in (400, 401, 403, 404, 413, 422, 429))
        for call in calls)
    if unknown:
        return 'uncertain'
    return 'cancelled' if cancelled else 'failed'
