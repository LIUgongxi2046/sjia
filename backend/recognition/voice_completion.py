import json

from django.conf import settings

from files.model_input import require_model_text
from recognition.ark import VOICE_PROMPT, VOICE_SYSTEM, _completion
from recognition.schemas import VoiceCandidates, VoiceRequest


def voice_completion_with_evidence(text, provider, *, before_provider_call, timeout_seconds):
    validated = VoiceRequest.model_validate({'text': text})
    require_model_text(validated.text.encode('utf-8'), 'text/plain', provider.payload['endpoint'].rstrip('/'))
    if provider.payload['endpoint'].rstrip('/') != settings.ARK_BASE_URL.rstrip('/'):
        raise ValueError('语音 Provider 与当前获准目标不一致')
    body, raw = _completion([
        {'role': 'system', 'content': VOICE_SYSTEM},
        {'role': 'user', 'content': VOICE_PROMPT.replace('__VOICE_TEXT__', json.dumps(validated.text, ensure_ascii=False))},
    ], model=provider.payload['model_version'], max_tokens=600, validate_output=False,
        before_provider_call=before_provider_call, timeout_seconds=timeout_seconds)
    return {'provider_request_id': body.id, 'model': body.model, 'usage': body.usage,
        'raw_response': raw, 'completion': body}


def checked_voice_candidates(result):
    body = result['completion']
    if len(body.choices) != 1:
        raise ValueError('语音模型返回的候选数量无效')
    choice = body.choices[0]
    if choice.finish_reason != 'stop' or choice.message.refusal or not choice.message.content:
        raise ValueError('语音模型未产生完整可用候选')
    return VoiceCandidates.model_validate_json(choice.message.content).model_dump()
