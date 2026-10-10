import hashlib
import json
import wave
from pathlib import Path

import av
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2] / '.qa-work/QA04'
ROOT.mkdir(parents=True, exist_ok=True)
FONT = ImageFont.truetype('/System/Library/Fonts/PingFang.ttc', 32)
CASES = {
    'report': ['合成检验报告 · 不含患者信息', '项目                 结果              单位         参考范围',
        '铁蛋白               150.5             ng/mL        20—200',
        'C反应蛋白            <5                mg/L         0—5',
        '血小板计数           230               10^9/L       100—300',
        '抗核抗体             阴性'],
    'therapy': ['合成处方 · 不含患者信息 · 仅供软件测试',
        '原药名：甲氨蝶呤片', '规格：2.5 mg/片', '处方原文：每周一次，每次10 mg；尚未开始使用。',
        '原药名：叶酸片', '处方原文：5 mg，时间未注明。'],
    'blank': [],
}
manifest = []
for name, lines in CASES.items():
    image = Image.new('RGB', (1400, 800), 'white')
    draw = ImageDraw.Draw(image)
    for index, line in enumerate(lines):
        draw.text((40, 45 + index * 100), line, font=FONT, fill='black')
    path = ROOT / f'{name}.png'
    image.save(path)
    manifest.append({'file': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'text': lines})
(ROOT / 'inputs.json').write_text(json.dumps({'source': '用户授权的无患者信息合成图片', 'inputs': manifest}, ensure_ascii=False, indent=2))

with av.open(str(ROOT / 'observation.aiff')) as source, wave.open(str(ROOT / 'observation.wav'), 'wb') as target:
    target.setnchannels(1)
    target.setsampwidth(2)
    target.setframerate(16000)
    resampler = av.AudioResampler(format='s16', layout='mono', rate=16000)
    for frame in source.decode(audio=0):
        for converted in resampler.resample(frame):
            target.writeframes(converted.to_ndarray().tobytes())
    for converted in resampler.resample(None):
        target.writeframes(converted.to_ndarray().tobytes())
