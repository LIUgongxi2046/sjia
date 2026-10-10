import argparse
import hashlib
import importlib.metadata
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from jsonschema import Draft202012Validator, FormatChecker
from PIL import Image


ROOT = Path(__file__).resolve().parent
CASE_DIR = ROOT / "current-patient"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def unique_ids(items):
    keys = [item["id"] for item in items]
    require(len(keys) == len(set(keys)), "同一集合中的标识重复")
    return set(keys)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--original-text", type=Path)
    parser.add_argument("--original-image-dir", type=Path)
    args = parser.parse_args()
    require(bool(args.original_text) == bool(args.original_image_dir), "原始文本与图片目录必须同时提供")
    index = read_json(ROOT / "index.json")
    case_path = (ROOT / index["current_patient_ref"]).resolve()
    require(case_path.is_relative_to(ROOT), "当前病例引用超出数据目录")
    data = read_json(case_path)
    schema = read_json(ROOT / "case.schema.json")
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(data)
    require(index["current_patient_id"] == data["patient"]["id"], "当前患者索引不一致")

    sources = {s["id"]: s for s in data["sources"]}
    unique_ids(data["sources"])
    times = {t["id"]: t for t in data["time_contexts"]}
    unique_ids(data["time_contexts"])
    source_text = (CASE_DIR / sources["case-text"]["path"]).read_text(encoding="utf-8")
    manifest = []
    for source in data["sources"]:
        path = (CASE_DIR / source["path"]).resolve()
        require(path.is_relative_to(CASE_DIR), "来源路径超出病例目录")
        entry = {"id": source["id"], "path": source["path"], "sha256": sha256(path), "size_bytes": path.stat().st_size}
        if source["kind"] == "image":
            with Image.open(path) as picture:
                require(picture.format == "PNG", "图片格式与后缀不一致")
                entry.update(format=picture.format, width=picture.width, height=picture.height)
                picture.verify()
        if args.original_text:
            original = args.original_text if source["kind"] == "text" else args.original_image_dir / path.name
            require(path.read_bytes() == original.read_bytes(), f"来源副本与用户附件不一致：{source['id']}")
        manifest.append(entry)
    manifest_path = CASE_DIR / "source-manifest.json"
    if args.original_text:
        manifest_path.write_text(json.dumps({"dataset_version": data["dataset_version"], "original_attachment_comparison": "PASS", "sources": manifest}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        require(read_json(manifest_path)["sources"] == manifest, "来源文件与保存的校验清单不一致")

    quoted = 0
    for obj in walk(data):
        if "source_id" in obj:
            require(obj["source_id"] in sources, "来源引用不存在")
        if obj.get("time_ref") is not None:
            require(obj["time_ref"] in times, "医学时间引用不存在")
        if "source_quote" in obj:
            require(obj["source_quote"] in source_text, f"引文未匹配原文：{obj['source_quote']}")
            quoted += 1
        if "report_text" in obj:
            require(obj["report_text"] in source_text, "影像报告文字与原文不一致")
        for field in ("value", "dose"):
            if obj.get(field) is not None:
                require(Decimal(obj[field]).is_finite(), "数值必须有限")

    references = set(data) | set(sources) | set(times)
    for key in ("history", "qualitative_findings", "measurement_panels", "microbiology", "imaging_studies", "diagnoses", "therapy_events", "followup_records", "data_issues"):
        references.update(unique_ids(data[key]))
    for panel in data["measurement_panels"]:
        references.update(f"{panel['id']}/{key}" for key in unique_ids(panel["items"]))
        for item in panel["items"]:
            require((item["value"] is None) == (item["qualifier"] is None), "数值与比较符必须同时提供")
            if panel["source_id"] == "case-text":
                require(item["raw_text"] in source_text, f"测量原文未匹配：{panel['id']}/{item['id']}")
    for group in data["microbiology"]:
        for result in group["results"]:
            require(result["raw_text"] in source_text, "病原学结果与原文不一致")
    for issue in data["data_issues"]:
        require(set(issue["refs"]) <= references, f"数据问题引用不存在：{issue['id']}")
    for time in times.values():
        precision = time["precision"]
        require((time["date"] is not None) == (precision == "date"), "日期精度与日期字段不一致")
        require((time["month"] is not None) == (precision == "month"), "月份精度与月份字段不一致")
        require((time["start_month"] is not None and time["end_month"] is not None) == (precision == "month_range"), "月份范围不完整")
        if precision == "month_range":
            require(time["start_month"] <= time["end_month"], "月份范围逆序")
    for event in data["therapy_events"]:
        require((event["date"] is not None) == (event["precision"] == "date"), "治疗日期精度不一致")
    studies = {s["id"]: s for s in data["imaging_studies"]}
    for asset in data["image_assets"]:
        require(sources[asset["source_id"]]["kind"] == "image", "影像引用必须指向图片")
        if asset["linked_study_id"] is not None:
            require(asset["linked_study_id"] in studies, "影像检查引用不存在")
        require(asset["linked_study_id"] is None and asset["link_status"] == "requires_confirmation", "本病例附件未提供可确认的检查关联")

    panels = {p["id"]: {i["id"]: i for i in p["items"]} for p in data["measurement_panels"]}
    require(panels["labs-image"]["ferritin"]["qualifier"] == "gt", "图片铁蛋白的比较符必须保留")
    require(panels["labs-image"]["ferritin"]["unit"] == "ng/mL" and panels["diagnostic-values"]["ferritin"]["unit"] == "μg/mL", "不同来源的单位必须分别保留")
    require(panels["admission-vitals"]["weight-admission"]["value"] == "21" and panels["diagnostic-values"]["weight"]["value"] == "23", "不同来源的体重必须分别保留")
    require(data["patient"]["reported_age"]["raw_text"] in source_text, "年龄原文未匹配")
    require(data["patient"]["name"] is None and data["patient"]["birth_date"] is None, "姓名与出生日期未提供")
    rows = data["disease_activity"]["rows"]
    require([r["date"] for r in rows] == ["2025-03-14", "2025-03-22", "2025-04-03", "2025-05-29", "2026-04-14"], "评分日期与附件记录不一致")
    columns = ["sjadas", "sms", "ajc", "physician_global", "parent_patient_global", "esr"]
    expected = [["28", "3", "4", "6", "5", "10"], ["15", "2", "2", "4", "3", "4"], ["8", "2", "2", "2", "2", "0"], ["10", "1", "5", "2", "2", "0"], ["6", "0", "0", "0", "0", "0"]]
    require([[r[c] for c in columns] for r in rows] == expected, "评分表转录值与附件核读记录不一致")
    score_dates = {row["date"] for row in rows}
    laboratory_ids = {group["id"] for group in data["measurement_panels"] + data["microbiology"]}
    for followup in data["followup_records"]:
        require(set(followup["laboratory_refs"]) <= laboratory_ids, "随访引用的检查组不存在")
        if followup["score_row_date"] is not None:
            require(followup["score_row_date"] in score_dates and followup["date"] == followup["score_row_date"], "随访评分日期不一致")
            require(followup["source_id"] == data["disease_activity"]["source_id"], "随访评分来源不一致")
    require({r["score_row_date"] for r in data["followup_records"] if r["score_row_date"]} == score_dates, "随访缺少评分引用")
    require(len(panels["routine-screening-results"]) == 11, "常规筛查项目不完整")
    require(set(panels["post-treatment-labs"]) == {"wbc", "crp", "esr", "ferritin"}, "治疗后炎症指标不完整")
    require(all(item["value"] is None and item["flag"] == "正常" for item in panels["post-treatment-labs"].values()), "定性治疗结果不能补充数值")
    micro = {group["id"]: group for group in data["microbiology"]}
    for key in ("sputum-recheck", "cmv-dna-recheck"):
        require(micro[key]["results"][0]["value"] is None and micro[key]["results"][0]["interpretation"] == "阴性", "阴性复查不得补充数值")
        require(times[micro[key]["time_ref"]]["precision"] == "relative", "复查日期未提供")
    require(micro["plasma-cmv-diagnostic-basis"]["specimen"] == "血浆", "诊断依据的 CMV DNA 标本必须保留")
    report = {
        "status": "PASS", "dataset_version": data["dataset_version"],
        "checked_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "checks": {"json_schema": "PASS", "source_bytes_and_image_format": "PASS", "reference_integrity": "PASS", "text_quotes": quoted, "source_differences_preserved": "PASS", "score_transcription_regression": "PASS", "image_report_link_unconfirmed": "PASS"},
        "counts": {"patients": 1, "source_files": len(sources), "images": sum(s["kind"] == "image" for s in sources.values()), "measurement_items": sum(len(p["items"]) for p in data["measurement_panels"]), "microbiology_results": sum(len(p["results"]) for p in data["microbiology"]), "imaging_reports": len(studies), "therapy_events": len(data["therapy_events"]), "score_rows": len(rows), "followup_records": len(data["followup_records"]), "data_issues": len(data["data_issues"])},
        "production_database_import": "NOT_RUN", "clinical_verification": "NOT_RUN", "ocr_accuracy_validation": "NOT_RUN",
        "scope": "项目文件读取、原始字节、人工转录结构与来源检查；图片转录检查未使用独立临床参考答案。",
        "dependencies": {name: importlib.metadata.version(name) for name in ("jsonschema", "Pillow")},
        "inputs_sha256": {str(p.relative_to(ROOT)): sha256(p) for p in [ROOT / "index.json", case_path, ROOT / "case.schema.json", Path(__file__), manifest_path]}
    }
    (ROOT / "validation-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], **report["counts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
