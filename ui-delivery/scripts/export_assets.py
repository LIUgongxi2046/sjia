from pathlib import Path
from PIL import Image, ImageOps

root = Path(__file__).resolve().parents[1]
for view in ("front", "back"):
    source = root / f"assets/master/child-neutral-{view}.png"
    with Image.open(source) as picture:
        if picture.mode != "RGBA":
            raise ValueError("儿童素材必须包含透明通道")
        print(source.name, picture.size, picture.getextrema()[3])
        for scale in (1, 2):
            output = ImageOps.contain(picture, (240 * scale, 320 * scale), Image.Resampling.LANCZOS)
            output.save(root / f"assets/{scale}x/child-neutral-{view}@{scale}x.png")
            print(scale, output.size)
