from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

output = Path(__file__).resolve().parents[2] / '.qa-work' / 'simulated-report.png'
output.parent.mkdir(exist_ok=True)
image = Image.new('RGB', (1200, 680), 'white')
draw = ImageDraw.Draw(image)
font = ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf', 32)
for index, text in enumerate([
    'SIMULATION - LOCAL SOFTWARE ACCEPTANCE ONLY',
    'This document contains no patient or personal information.',
    'Example item       Value       Unit       Reference',
    'Ferritin           150         ng/mL      20-200',
    'CRP                4.2         mg/L       0-5',
    'All values were created for software testing.',
]):
    draw.text((40, 50 + index * 85), text, fill='black', font=font)
image.save(output)
print(output)
