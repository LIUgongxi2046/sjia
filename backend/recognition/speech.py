from functools import lru_cache
from io import BytesIO
from pathlib import Path
from threading import Lock

import av
import numpy as np
from django.conf import settings
from faster_whisper import WhisperModel

from api.base import ApiError

SAMPLE_RATE = 16000
MAX_SECONDS = 61
inference_lock = Lock()


@lru_cache(maxsize=1)
def speech_model():
    directory = settings.SPEECH_MODEL_DIR
    if not directory or not (Path(directory) / 'model.bin').is_file():
        raise ApiError('SPEECH_NOT_CONFIGURED', 503, '语音转写服务尚未配置，请联系服务管理员。')
    return WhisperModel(directory, device='cpu', compute_type='int8', cpu_threads=4, local_files_only=True)


def transcribe_audio(data):
    if not data or len(data) > 5 * 1024 * 1024:
        raise ApiError('AUDIO_SIZE_INVALID', 413, '录音不能为空，且不能超过5MB。')
    if not inference_lock.acquire(blocking=False):
        raise ApiError('SPEECH_BUSY', 429, '语音服务正在处理其他录音，请稍后重新转写。')
    try:
        chunks = []
        samples = 0
        with av.open(BytesIO(data)) as container:
            if not container.streams.audio:
                raise ApiError('AUDIO_STREAM_REQUIRED', 422, '文件中没有可以转写的声音。')
            resampler = av.AudioResampler(format='s16', layout='mono', rate=SAMPLE_RATE)
            for frame in container.decode(audio=0):
                for converted in resampler.resample(frame):
                    samples += converted.samples
                    if samples > SAMPLE_RATE * MAX_SECONDS:
                        raise ApiError('AUDIO_TOO_LONG', 422, '每段录音最多60秒，请分次记录。')
                    chunks.append(converted.to_ndarray().reshape(-1))
            for converted in resampler.resample(None):
                samples += converted.samples
                chunks.append(converted.to_ndarray().reshape(-1))
        if samples < SAMPLE_RATE / 2:
            raise ApiError('AUDIO_TOO_SHORT', 422, '录音太短，请完整描述后重新录音。')
        if samples > SAMPLE_RATE * MAX_SECONDS:
            raise ApiError('AUDIO_TOO_LONG', 422, '每段录音最多60秒，请分次记录。')
        audio = np.concatenate(chunks).astype(np.float32) / 32768.0
        segments, info = speech_model().transcribe(audio, language='zh', beam_size=5,
            condition_on_previous_text=False, vad_filter=True, initial_prompt='家庭观察记录。词汇：体温，摄氏度，华氏度，皮疹，关节，发热。')
        text = ''.join(segment.text for segment in segments).strip()
        if not text:
            raise ApiError('SPEECH_EMPTY', 422, '没有识别到清楚的语音，请靠近麦克风重新录音。')
        return {'text': text, 'duration_seconds': round(info.duration, 2)}
    finally:
        inference_lock.release()
