from io import BytesIO
import wave
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from pydantic import ValidationError

from api.base import ApiError
from identity.models import Patient, Principal
from recognition.schemas import TherapyCandidates
from recognition.speech import inference_lock, transcribe_audio


class SpeechInputTests(TestCase):
    def setUp(self):
        call_command('seed_demo', verbosity=0)
        self.actor = Principal.objects.first()
        self.patient = Patient.objects.get(display_id='demo-child')
        from tests.synthetic_fixtures import qualify_demo
        from sources.models import DataUseAuthorization
        qualify_demo(self, self.actor, self.patient)
        authorization = DataUseAuthorization.objects.get(patient=self.patient)
        self.client.defaults['HTTP_X_USE_AUTHORIZATION'] = str(authorization.id)
        identity = patch('recognition.local_transcription._model_identity', return_value={
            'model_name': 'synthetic-identity-only', 'implementation_version': 'test', 'artifact_sha256': 'a' * 64})
        identity.start()
        self.addCleanup(identity.stop)
        model = patch('recognition.speech.speech_model', side_effect=AssertionError('Audio validation must not invoke a model'))
        model.start()
        self.addCleanup(model.stop)
        self.path = f'/api/patients/{self.patient.id}/voice-transcription'
        session = self.client.session
        session['_sjia_principal_id'] = str(self.actor.id)
        session['_sjia_auth_epoch'] = self.actor.auth_epoch
        session['_sjia_audience'] = 'family'
        session.save()

    def test_rejects_invalid_audio_and_releases_capacity(self):
        response = self.client.post(self.path, b'invalid audio', content_type='audio/wav')
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['code'], 'AUDIO_INVALID')
        self.assertFalse(inference_lock.locked())

    def test_accepts_codec_parameter_before_decoding(self):
        response = self.client.post(self.path, b'invalid audio', content_type='audio/webm;codecs=opus')
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['code'], 'AUDIO_INVALID')

    def test_requires_explicit_source_authorization_before_decoding(self):
        del self.client.defaults['HTTP_X_USE_AUTHORIZATION']
        response = self.client.post(self.path, b'invalid audio', content_type='audio/wav')
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['code'], 'TRANSCRIPTION_AUTHORIZATION_REQUIRED')

    def test_requires_session(self):
        self.client.cookies.clear()
        self.assertEqual(self.client.post(self.path, b'audio', content_type='audio/wav').status_code, 401)

    def test_rejects_size_and_type(self):
        self.assertEqual(self.client.post(self.path, b'audio', content_type='text/plain').status_code, 415)
        self.assertEqual(self.client.post(self.path, b'', content_type='audio/wav').status_code, 413)
        self.assertEqual(self.client.post(self.path, bytes(5 * 1024 * 1024 + 1), content_type='audio/wav').status_code, 413)

    def test_rejects_other_patient(self):
        from identity.service import register_account

        other = register_account(username='qa04-unrelated', password='Local-QA04-2026')
        session = self.client.session
        session['_sjia_principal_id'] = str(other.id)
        session['_sjia_auth_epoch'] = other.auth_epoch
        session.save()
        self.assertEqual(self.client.post(self.path, b'audio', content_type='audio/wav').status_code, 403)

    def test_rejects_duration_before_inference(self):
        buffer = BytesIO()
        with wave.open(buffer, 'wb') as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(bytes(16000 * 2 * 66))
        response = self.client.post(self.path, buffer.getvalue(), content_type='audio/wav')
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['code'], 'AUDIO_TOO_LONG')
        self.assertFalse(inference_lock.locked())

    def test_busy_does_not_queue_audio(self):
        with inference_lock:
            with self.assertRaises(ApiError) as raised:
                transcribe_audio(b'audio')
            self.assertEqual(raised.exception.code, 'SPEECH_BUSY')

    def test_therapy_ocr_validates_request_and_session(self):
        response = self.client.post('/api/recognition/therapy-ocr', {'file_id': 'invalid'}, content_type='application/json')
        self.assertEqual(response.status_code, 422)
        self.client.cookies.clear()
        response = self.client.post('/api/recognition/therapy-ocr', {'file_id': 'invalid'}, content_type='application/json')
        self.assertEqual(response.status_code, 401)

    def test_short_audio_is_rejected_before_model(self):
        buffer = BytesIO()
        with wave.open(buffer, 'wb') as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(bytes(16000 // 5 * 2))
        response = self.client.post(self.path, buffer.getvalue(), content_type='audio/wav')
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['code'], 'AUDIO_TOO_SHORT')

    def test_therapy_schema_preserves_original_and_rejects_advice(self):
        valid = {'items': [{'name': '原药名', 'source_text': '原药名，尚未使用'}]}
        self.assertEqual(TherapyCandidates.model_validate(valid).model_dump(), valid)
        with self.assertRaises(ValidationError):
            TherapyCandidates.model_validate({'items': [{'name': '原药名', 'source_text': '原文', 'recommendation': '建议'}]})
        with self.assertRaises(ValidationError):
            TherapyCandidates.model_validate({'items': [{'name': ' ', 'source_text': '原文'}]})
