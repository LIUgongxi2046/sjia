import csv
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageOps


root = Path(__file__).resolve().parents[1] / "identity"
catalog = json.loads((root / "catalog.json").read_text())
records = json.loads((root / "generation-records.json").read_text())
expected = {f'{age["id"]}-{gender["id"]}-{season["id"]}' for age in catalog["ages"] for gender in catalog["genders"] for season in catalog["seasons"]}
expected.add(catalog["logo"])
if {record["id"] for record in records} != expected:
    raise ValueError("生成记录与形象分类不一致")
rows = []
for record in records:
    asset_id = record["id"]
    source = root / "master" / f"{asset_id}.png"
    with Image.open(source) as picture:
        if picture.mode != "RGBA" or picture.getextrema()[3] != (0, 255):
            raise ValueError(f"透明通道不符合要求：{asset_id}")
        for scale in (1, 2):
            box = (128 * scale, 128 * scale) if asset_id == catalog["logo"] else (240 * scale, 320 * scale)
            if picture.width < box[0] or picture.height < box[1]:
                raise ValueError("源图片尺寸不足")
            output = ImageOps.contain(picture, box, Image.Resampling.LANCZOS)
            output.save(root / f"{scale}x" / f"{asset_id}@{scale}x.png")
    for size in ("master", "1x", "2x"):
        filename = f"{asset_id}.png" if size == "master" else f"{asset_id}@{size}.png"
        path = root / size / filename
        with Image.open(path) as picture:
            picture.load()
            rows.append({"asset_id":asset_id,"file_path":str(path.relative_to(root)),"usage":"品牌图形" if asset_id == catalog["logo"] else "儿童形象正面","width":picture.width,"height":picture.height,"format":"PNG","transparency":"alpha","source":"image_gen__imagegen","sha256":hashlib.sha256(path.read_bytes()).hexdigest()})
with (root / "asset-manifest.csv").open("w",newline="",encoding="utf-8") as stream:
    writer = csv.DictWriter(stream,fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
with (root / "image-generation-manifest.csv").open("w",newline="",encoding="utf-8") as stream:
    writer = csv.DictWriter(stream,fieldnames=["asset_id","generation_tool","prompt_summary","source_image_path","output_file_path"])
    writer.writeheader()
    for record in records:
        writer.writerow({"asset_id":record["id"],"generation_tool":"image_gen__imagegen","prompt_summary":record["prompt"],"source_image_path":record["reference"],"output_file_path":f'master/{record["id"]}.png'})
result = {"generated_masters":len(records),"characters":len(expected)-1,"png_files":len(rows),"all_transparent":True,"all_decoded":True}
(root / "checks/asset-check.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(result,ensure_ascii=False))
