import json

from django.conf import settings

from files.model_input import require_model_text
from recognition.ark import VOICE_PROMPT, VOICE_SYSTEM
from recognition.invocation import invoke_model, checked_candidates
from recognition.schemas import VoiceCandidates, VoiceRequest


def voice_completion_with_evidence(text, provider, *, before_provider_call, timeout_seconds):
    validated = VoiceRequest.model_validate({'text': text})
    require_model_text(validated.text.encode('utf-8'), 'text/plain', provider.payload['endpoint'].rstrip('/'))
    if provider.payload['endpoint'].rstrip('/') != settings.ARK_BASE_URL.rstrip('/'):
        raise ValueError('语音 Provider 与当前获准目标不一致')
    return invoke_model([
        {'role': 'system', 'content': VOICE_SYSTEM},
        {'role': 'user', 'content': VOICE_PROMPT.replace('__VOICE_TEXT__', json.dumps(validated.text, ensure_ascii=False))},
    ], provider, 'voice', max_tokens=600,
        before_provider_call=before_provider_call, timeout_seconds=timeout_seconds)


def checked_voice_candidates(result, provider):
    return checked_candidates(result, provider, VoiceCandidates)
