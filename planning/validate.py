import csv
import hashlib
import json
import os
from collections import defaultdict
from datetime import datetime
from graphlib import TopologicalSorter
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import unquote, urlsplit
from zoneinfo import ZoneInfo

from jsonschema import Draft202012Validator
from markdown_it import MarkdownIt


ROOT = Path(__file__).resolve().parent.parent
PLAN = ROOT / "planning"
SKILL_ROOT = Path("/Users/liuhaoxian/.agents/skills/haonan-s000-pmp")
MARKDOWN = MarkdownIt("commonmark").enable("table")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def table_rows(text):
    rows = []
    current = None
    in_cell = False
    for token in MARKDOWN.parse(text):
        if token.type == "tr_open":
            current = []
        elif token.type in {"td_open", "th_open"}:
            in_cell = True
        elif token.type == "inline" and in_cell:
            current.append(token.content.strip())
        elif token.type in {"td_close", "th_close"}:
            in_cell = False
        elif token.type == "tr_close":
            rows.append(current)
            current = None
    return rows


def identifiers(text, pattern):
    return set(re.findall(pattern, text))


def run_check(command):
    started = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    environment = dict(os.environ, TMPDIR=str(ROOT / ".planning-work/tmp"), PYTHONPATH=str(ROOT / ".data-work/deps"))
    result = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, text=True, timeout=60)
    require(result.returncode == 0, f"检查失败：{command}\n{result.stdout}\n{result.stderr}")
    return {"command": command, "started_at": started, "finished_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(), "exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr}


def main():
    (ROOT / ".planning-work/tmp").mkdir(parents=True, exist_ok=True)
    ledger_path = PLAN / "delivery-ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    schema_path = SKILL_ROOT / "references/delivery-ledger.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(ledger)
    task_ids = {task["id"] for task in ledger["tasks"]}
    require(task_ids == {f"T{number:02}" for number in range(1, 61)} and len(ledger["tasks"]) == 60, "任务必须具有60个唯一ID")
    new_ids = {f"T{number:02}" for number in range(34, 61)}
    dependency_map = {task["id"]: task["depends_on"] for task in ledger["tasks"]}
    for task in ledger["tasks"]:
        require(set(task["depends_on"]) <= task_ids, f"未知任务依赖：{task['id']}")
        if task["id"] in new_ids and ledger["continuation"]["mode"] == "PLAN":
            require(task["state"] == "TODO", f"新增计划不得声明实施完成：{task['id']}")
    order = list(TopologicalSorter(dependency_map).static_order())
    pending_ids = {task["id"] for task in ledger["tasks"] if task["state"] != "ACCEPTED"}
    require(set(ledger["continuation"]["remaining_task_ids"]) == pending_ids, "未完成任务集合不完整")
    require(ledger["continuation"]["mode"] in {"PLAN", "AUTHORIZED_BACKLOG"}, "执行模式缺少授权依据")
    new_acceptances = {f"V{number:02}" for number in range(34, 61)}
    if ledger["continuation"]["mode"] == "PLAN":
        require(all(item["status"] == "NOT_RUN" and not item["evidence_ids"] for item in ledger["acceptances"] if item["id"] in new_acceptances), "规划模式不能登记新增业务验收")

    inputs = {ledger_path, schema_path, PLAN / "environment.json", Path(__file__).resolve(), ROOT / ".gitignore"}
    coverage_path = PLAN / "coverage.csv"
    inputs.add(coverage_path)
    coverage = defaultdict(dict)
    for row in read_csv(coverage_path):
        source = ROOT / row["source_ref"]
        require(source.is_file(), f"追溯来源不存在：{source}")
        inputs.add(source)
        owners = set(row["task_ids"].split(";"))
        require(bool(owners) and owners <= task_ids, f"追溯任务无效：{row}")
        for source_id in row["source_ids"].split(";"):
            require(source_id not in coverage[row["kind"]], f"重复追溯编号：{source_id}")
            require(source_id in source.read_text(encoding="utf-8"), f"来源未包含编号：{source_id}")
            coverage[row["kind"]][source_id] = sorted(owners)

    prd = (ROOT / "sJIA合并MAS预警平台PRD.md").read_text(encoding="utf-8")
    data = (ROOT / "sJIA合并MAS预警平台数据LLD.md").read_text(encoding="utf-8")
    back = (ROOT / "sJIA合并MAS预警平台后端LLD.md").read_text(encoding="utf-8")
    alert = (ROOT / "sJIA合并MAS预警平台预警编排LLD.md").read_text(encoding="utf-8")
    requirement = (ROOT / "sJIA合并MAS预警平台需求输入.md").read_text(encoding="utf-8")
    api_rows = read_csv(ROOT / "backend-design/api-catalog.csv")
    route_rows = read_csv(ROOT / "frontend-design/routes.csv")
    expected = {
        "G": identifiers((ROOT / "sJIA合并MAS预警平台产品目标.md").read_text(), r"\bG\d{2}\b"),
        "F": identifiers(requirement, r"\bF\d{2}\b"),
        "FR": identifiers(prd, r"\bFR-\d{2}\b"),
        "FD": identifiers(prd, r"\bFD-\d{2}\b"),
        "AC": identifiers(prd, r"\bAC-\d{2}\b"),
        "H5": identifiers(prd, r"\bAC-H5-\d{2}\b"),
        "NFR": identifiers(prd, r"\bNFR-\d{2}\b"),
        "BR": identifiers(prd, r"\bBR-[A-Z]\d{2}\b"),
        "UM": identifiers(prd, r"\bUM-\d{2}\b"),
        "EV": identifiers(prd, r"\bEV-\d{2}\b"),
        "ENV": identifiers(prd, r"\bENV-H5-\d{2}\b"),
        "QA": identifiers(data, r"\bQA\d{2}\b"),
        "DQ": identifiers(data, r"\bDQ\d{2}\b"),
        "BQA": identifiers(back, r"\bBQA\d{2}\b"),
        "AQA": identifiers(alert, r"\bAQA\d{2}\b"),
        "EVAL": identifiers(alert, r"\bEVAL-\d{2}\b"),
        "FRONT": {row["check_id"] for row in read_csv(ROOT / "frontend-design/acceptance.csv")},
        "API": {row["operation_id"] for row in api_rows},
        "ROUTE": {row["route_id"] for row in route_rows},
        "UI": {"A01", "A11", "A13", "A14", "A15", "A16", "A17", "A18", "A19", "A23", "A25", "A26"},
        "VOICE": {"A22"},
        "GR": identifiers(prd, r"\bGR-\d{2}\b"),
        "GRAC": identifiers(prd, r"\bGRAC-\d{2}\b"),
        "ACPLUS": identifiers(prd, r"\bAC-(?:DYN|NOTIFY|EVAL|AI|UX|COMP)-\d{2}\b"),
        "OPT": identifiers(prd, r"\bOPT-\d{2}\b"),
        "UI2": {row["screen_id"] for row in read_csv(ROOT / "ui-delivery/v2/screen-design-map.csv")},
    }
    for kind, source_ids in expected.items():
        actual = set(coverage[kind])
        require(actual == source_ids, f"{kind}覆盖不完整：缺少{sorted(source_ids - actual)}；未登记来源{sorted(actual - source_ids)}")
    require(len(expected["FR"]) == 22 and len(expected["FD"]) == 71 and len(expected["NFR"]) == 18, "PRD功能、字段或非功能基线变化")
    require(len(expected["API"]) == 141 and len(expected["ROUTE"]) == 24, "接口或路由基线变化")

    document = ROOT / "implementation-tasks.md"
    index = ROOT / "docs/development/index.md"
    iteration = ROOT / "2.0迭代计划.md"
    inputs.update({document, index, iteration})
    text = document.read_text(encoding="utf-8")
    tokens = MARKDOWN.parse(text)
    cards = {}
    for position, token in enumerate(tokens):
        if token.type == "heading_open" and token.tag == "h3":
            title = tokens[position + 1].content
            matched = re.match(r"(T\d{2})\s", title)
            if matched:
                start = token.map[0]
                end = len(text.splitlines())
                for following in tokens[position + 1:]:
                    if following.type == "heading_open" and following.tag in {"h1", "h2", "h3"}:
                        end = following.map[0]
                        break
                task_id = matched.group(1)
                require(task_id not in cards, f"重复工单：{task_id}")
                cards[task_id] = "\n".join(text.splitlines()[start:end])
    require(set(cards) == task_ids, "工单正文与任务记录不一致")
    registry = {row[0]: row for row in table_rows(index.read_text(encoding="utf-8")) if row and row[0] in task_ids}
    require(set(registry) == task_ids, "开发记录索引缺少任务")
    for task in ledger["tasks"]:
        task_id = task["id"]
        card = cards[task_id]
        record = f"docs/development/{task_id}.md"
        require(record in card and record in registry[task_id], f"主记录映射不一致：{task_id}")
        require(record in task["allowed_paths"], f"主记录不在修改范围：{task_id}")
        header = next(line for line in card.splitlines() if "`depends_on`" in line)
        dependencies = identifiers(header.split("`depends_on`:", 1)[1].split("；", 1)[0], r"\bT\d{2}\b")
        require(dependencies == set(task["depends_on"]), f"正文与JSON依赖不一致：{task_id}")
        for field in ("需求与结果", "上下文", "步骤", "修改范围", "验证", "风险与恢复", "PENDING"):
            require(field in card, f"工单缺少字段：{task_id}/{field}")
        require(all(aid in card for aid in task["acceptance_ids"]), f"工单验收不一致：{task_id}")
    runtime = json.loads((ROOT / "backend-design/runtime.schema.json").read_text(encoding="utf-8"))
    runtime_types = runtime["properties"]["tasks"]["items"]["properties"]["task_type"]["enum"]
    require(all(name in cards["T06"] for name in runtime_types), "T06需要覆盖全部正式任务类型")

    checked_links = []
    for source in (document, index, iteration):
        for token in MARKDOWN.parse(source.read_text(encoding="utf-8")):
            for child in token.children or []:
                if child.type == "link_open":
                    href = child.attrGet("href")
                    parsed = urlsplit(href)
                    require(not parsed.scheme, f"规划文件只引用项目依据：{href}")
                    target = (source.parent / unquote(parsed.path)).resolve()
                    require(target.is_file() or target == PLAN / "validation-report.json", f"本地链接不可解析：{target}")
                    if target.is_file() and target != PLAN / "validation-report.json":
                        inputs.add(target)
                    checked_links.append(href)
    prototype_paths = [
        "ui-delivery/screens/png/S03.png", "ui-delivery/screens/png/S03-dark.png",
        "ui-delivery/screens/png/S01.png", "ui-delivery/screens/png/S13-A.png",
        "ui-delivery/screens/png/S04-voice-permission.png", "ui-delivery/screens/png/S04-voice-review.png",
        "ui-delivery/v2/index.html", "ui-delivery/v2/src/catalog.js", "ui-delivery/v2/tokens.json",
        "ui-delivery/v2/screens/page-specifications.md", "ui-delivery/v2/states/state-specifications.md",
        "ui-delivery/v2/screen-design-map.csv", "ui-delivery/v2/ui-audit.md", "frontend-design/api-bindings.csv",
    ]
    for path in prototype_paths:
        require((ROOT / path).is_file(), f"原型引用不存在：{path}")
    for source in (document, index, iteration):
        body = source.read_text(encoding="utf-8")
        for forbidden in ("\u6808", "\u843d", "\u6b7b", "\u62c6", "\u504f", "\u5951\u7ea6"):
            require(forbidden not in body, f"文档包含受限文字：{source.name}")

    for path in ("guardian-h5/src/app/App.tsx", "guardian-h5/src/components/SaveActions.tsx", "guardian-h5/src/features/registration/ReportsPage.tsx", "guardian-h5/package.json", "guardian-h5/package-lock.json", "frontend-design/types.ts", "backend-design/protocol.schema.json", "backend-design/runtime.schema.json", "data-design/schema.sql", "data-design/case-schema.sql", "backend-design/schema.sql", "alert-design/policy.schema.json", "alert-design/node.schema.json", "data-design/clinical-dataset-status.json"):
        inputs.add(ROOT / path)
    for path in ("frontend-design/check-types.mjs", "frontend-design/describe-schema.py", "frontend-design/tsconfig.json", "guardian-h5/tsconfig.json", "data-design/dataset.schema.json"):
        inputs.add(ROOT / path)
    inputs.add(SKILL_ROOT / "scripts/validate_delivery.py")
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(inputs)}
    checks = [
        run_check([sys.executable, str(SKILL_ROOT / "scripts/validate_delivery.py"), "planning/delivery-ledger.json"]),
        run_check([sys.executable, "planning/check_receipts.py"]),
    ]
    require(all(hashlib.sha256(path.read_bytes()).hexdigest() == value for path, value in before.items()), "执行检查期间依据文件发生变化")
    report = {
        "checked_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "mode": ledger["continuation"]["mode"],
        "capability": "DESIGN",
        "status": "PASS",
        "scope": "计划Schema、工单、需求/接口/页面追溯、依赖、开发记录路径及本地链接",
        "command": "PYTHONPATH=.data-work/deps python3 planning/validate.py",
        "counts": {"tasks": len(task_ids), "commitments": len(ledger["commitments"]), "acceptances": len(ledger["acceptances"]), "source_ids": {key: len(value) for key, value in expected.items()}, "local_links": len(checked_links)},
        "topological_order": order,
        "next_task_id": ledger["continuation"]["next_task_id"],
        "current_authorization": ledger["continuation"]["mode"],
        "business_validation": "NOT_RUN",
        "visual_validation": "NOT_RUN",
        "mermaid_rendering": "NOT_RUN",
        "target_postgresql_17": "NOT_RUN",
        "inputs_unchanged": True,
        "execution_checks": checks,
        "source_sha256": {str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path): value for path, value in before.items()},
        "limits": ["编号映射不能单独证明需求语义完整", "原型路径仅检查存在，未读取像素", "未来业务验证命令尚待对应任务建立", "原始AC完成需要覆盖表中的组合验证，V编号为工单级验收", "本期不重跑业务测试，既有证据保留实际适用范围"],
    }
    (PLAN / "validation-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "counts": report["counts"], "next_task_id": report["next_task_id"], "report": "planning/validation-report.json"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
