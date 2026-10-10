# T59 模拟分母与指标状态

类别：`SIMULATION`。实际计算命令为 `python3 tests/clinical/window3_evaluation.py --task T59`。完整结果及证据摘要见 [T59 运行结果](../../.planning-work/parallel/window3/T59-simulation-evaluation.json)。

| 分母 | 数值 | 含义 |
| --- | ---: | --- |
| 患儿 | 1 | 全部属于 development |
| validation 患儿 | 0 | 尚无独立验证患儿 |
| 观察时段 | 3 | 模拟冻结集合记录 |
| 观察人时 | 6 | T48 已保存准确人时 |
| positive 标签 | 1 | 模拟独立答案 |
| negative 标签 | 1 | 模拟独立答案 |
| unknown 标签 | 1 | 明确排除阴性分母 |
| development 标签 | positive 1、negative 1、unknown 1 | 同患儿仅计入 development |
| validation 标签 | 0 | 验证指标保持未测量 |
| MAS 事件单位 | 未登记 | 不推断为零事件 |
| 采样事件单位 | 未登记 | 不推断为零采样 |

T48 replay 任务已完成，运行结果为 `insufficient`。敏感度、漏报、PPV、观察时段提示负担、首次提示时间差、临床无法评估比例、静态／动态对照及不确定范围全部 `NOT_MEASURED`。没有静态／动态结果时，不将空提示解释为零提示；T48 单位清单检查不构成临床指标。

评价程序现核验每个患儿与 T48 分组完全相同，要求全部冻结标签对应观察时段齐全，并核对标签状态、阳性或未知事件的冻结答案引用、静态和动态固定规则／程序版本与执行引用、相同输入摘要、逐事实来源、可得时间与 `known_at`。提示结局必须提供独立答案来源；replay 副作用计数必须为零。报告包括全部观察时间提示数／人时和阴性观察时间提示数／人时；unknown 时段不进入阴性提示负担分母。程序还按登记的治疗、年龄、感染和资料完整度分组输出指标。

开发／验证分母分别输出；本冻结集合没有 validation 患儿，因此 T48 和规则场景的验证指标为未测量。T49/T50 保存结果专用只读捕获入口 `tests/integration/window3_saved_t49_t50_capture.py` 使用准确 Task ID 和 T48 dataset_version_id，连接共享 `sjia_simulation:5433`，以 PostgreSQL 只读事务读取已保存 Task、规则、输入事实、配置审批和冻结集合分组；并以 `record_version_id/field_id/verification_id` 核验结果输入与 Task 快照事实来源。实际捕获完成于 `2026-10-09T07:23:49.426281+00:00`，捕获与评价器摘要见下方。Task 为 `current` 模式或患儿不属于准确 T48 冻结版本时，临床指标保持 `NOT_RUN` 或 `NOT_MEASURED`。

## 评价程序场景检查

使用 `python3 tests/clinical/window3_simulation_fixtures.py` 创建清楚标识的 `SIMULATION_FIXTURE_ONLY` 数据，再执行 `python3 tests/clinical/window3_evaluation.py --task T59 --predictions .planning-work/parallel/window3/T59-algorithm-fixture.json`。场景绑定 T48 第 6 版的实际分母及分组，静态和动态结果为单独编写的算法场景输入，规则版本标为 `SIMULATION_ONLY_STATIC_FIXTURE_V1` 和 `SIMULATION_ONLY_DYNAMIC_FIXTURE_V1`。它没有执行 T49/T50 规则。

独立分组 Schema 场景另由 `--partition-fixture .planning-work/parallel/window3/T59-partition-fixture.json` 运行：development 为 1 名模拟患儿、1 positive、0 negative、1 unknown、4 人时；validation 为 1 名模拟患儿、1 positive、1 negative、1 unknown、6 人时。同患儿跨组数为 0。所有答案和来源都明确标为 `SIMULATION_FIXTURE_ONLY`，unknown 没有阴性答案。该场景只验证 partition 和分母处理，临床指标与历史未来资料排除均保持 `NOT_MEASURED`。

| 场景指标 | 静态 fixture | 动态 fixture |
| --- | ---: | ---: |
| 场景阳性事件敏感度 | 1/1 | 1/1 |
| 场景漏报 | 0/1 | 0/1 |
| 已知提示结局 PPV | 1/2 | 1/1 |
| 全部观察时段提示数／6 人时 | 3/6 = 0.5 | 2/6 = 0.3333 |
| negative 时段提示数／2 人时 | 1/2 = 0.5 | 0/2 = 0 |
| 首次提示减独立识别时点 | +600 秒 | -600 秒 |
| unknown 事件占 positive/unknown 事件 | 1/2 = 0.5 | 1/2 = 0.5 |
| 识别范围上下界 | 0.5–1.0 | 0.5–1.0 |
| PPV 识别范围上下界 | 0.3333–0.6667 | 0.5–1.0 |

场景输入排除 3 条 `known_at` 之后可得事实，使用未来事实数为 0；模拟场景输入的发布、通知和当前周期修改计数均为 0。患者相关置信区间为 `NOT_MEASURED`。上述数值只验证评价器计算行为。实际 T48 replay 状态仍为 `insufficient`，敏感度、漏报、PPV、提示负担、首次提示时间差、临床无法评估比例、静态／动态对照及不确定范围仍为 `NOT_MEASURED`。

## 已保存 T49/T50 current 结果的实际只读捕获

在 `sjia_simulation:5433` 的 READ ONLY 事务中捕获三个 completed Task：classification `36fe2110-577c-43a1-b955-40c3a5b3e350`，MS `a6bf735b-5044-4898-8f59-ac52fe241ba4`，dynamic `d17d89e4-345e-46c9-bb35-7d042d6a6988`。各有一条保存结果：classification `meets`，MS total `-1.0500` 且阈值为 true，dynamic 决策 `met`、AlertEvent `db547811-b132-416d-87dd-ebed5879c7a5`，episode `8c39107e-7bf3-4787-98f8-f02e95685d07`。这些为 `SIMULATION` 当前执行证据，未作为临床指标输入。

配置固定版本：classification rule `577201ec-c3ac-4460-b9f3-224639b1c182` v1；MS rule `c462de9a-fb56-4470-bdfe-1af03fc65498` v1；dynamic trigger `4732f31b-9d72-42ef-90f6-1643d8c216da` v2；window policy `a6b48142-42e7-4045-a857-6d73ee22326d` v1。准确校验值、审批、验证、来源修订及 TaskAttempt 见[只读捕获](../../.planning-work/parallel/window3/T59-saved-rule-result-capture.json)。三组 Task 的 `known_at/assessment_at` 同为 `2026-10-09T06:55:26+00:00`，mode 全为 `current`；相同时间戳不等同于历史 replay。

Task 患儿 `405514ce-9335-4705-a750-d811148bda4c` 不在 T48 v6 冻结集合。集合的唯一 development 患儿为 `e88faef5-8bf6-4256-b5a5-865c699103d6`，validation 患儿为 0；因此 Task 结果不得并入 T48 分母或报告成临床效能。分类、MS、dynamic 的结果输入为 10、12、13 条，分别与 Task 选择的 10、12、5 个唯一事实引用全部匹配；共核验 27 个已选事实，其中捕获字段显示在该 `known_at` 前已可得且核对一致。此项只记录字段时点和来源关联；三个任务的 `current` 模式及集合成员不匹配使历史未来资料排除验收保持 `NOT_VERIFIED`。

捕获 JSON 已重新读取，SHA256 为 `55a11e3141eb00f80b872b15d14567dab760d284d0ce8b2ceeddf238235c567e`。随后运行 `backend/.venv/bin/python tests/clinical/window3_evaluation.py --task T59 --saved-rule-capture .planning-work/parallel/window3/T59-saved-rule-result-capture.json`，退出码 0，返回 `SIMULATION_DENOMINATORS_ONLY`，指标 `NOT_MEASURED`。更新后的 [T59-simulation-evaluation.json](../../.planning-work/parallel/window3/T59-simulation-evaluation.json) 已重新读取，SHA256 为 `2882d06d644796f21d95f595f08af3be4629b0f468f96e4b48ab5f00425be633`。没有启动 API、Redis、Worker、Beat 或消费队列。

最终评价报告已在本窗口独立 `sjia_simulation` 实际保存并重新读取，数据库与 API 运行证据见 [runtime-roundtrip-evidence.json](../../.planning-work/parallel/window3/runtime-roundtrip-evidence.json)。该操作只验证本窗口评价报告存储与接口会话，不代表 T49/T50 规则执行或临床指标测量。

本次往返 T59 artifact ID 为 `d00e0a51-cadc-44f8-ae89-79b7c9565dbe`，SHA256 为 `546811c361e40c46bcaf12ce86da5f6e3ba4f969548f8c0c40ec7378e08b3060`，完成时间为 `2026-10-09T05:41:06.076448+00:00`。本次实际命令同时运行 `--partition-fixture .planning-work/parallel/window3/T59-partition-fixture.json`：development 为 1 名合成患儿、1 positive、0 negative、1 unknown、2 个时段、4 人时；validation 为另 1 名合成患儿、1 positive、1 negative、1 unknown、3 个时段、6 人时；跨组患儿数为 0。该输入的临床指标及未来信息排除仍为 `NOT_MEASURED`。全部场景指标属于 `SIMULATION_FIXTURE_ONLY`，不能作为 T48 临床表现。
