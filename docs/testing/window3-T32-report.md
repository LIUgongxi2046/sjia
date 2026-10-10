# T32 模拟评价准备记录

分类：`SIMULATION`。本记录汇总 T55、T59 和 T32 评价器的实际运行。独立 clinical Gold 原文、批准的分组边界、T49/T50 临床运行、家庭参与者观察及真实设备结果保持 `NOT_RUN`。

当前冻结集合含 1 名 development 患儿且没有 validation 患儿。已确认标签共 2 项，另有 1 项 unknown；unknown 没有计为阴性。集合中的 Gold 引用已登记，但原始答案文本未随当前本地响应提供，不能据此宣称临床答案复核完成。评价程序要求独立 Gold 原文摘要和维护来源，并拒绝模型生成答案。

标签分母按 partition 明列：development 为 1 positive、1 negative、1 unknown；validation 为 0 positive、0 negative、0 unknown。T48 Gold 原文状态为 `NOT_VERIFIED_FROM_T48_CAPTURE`。输入的两项答案为 `SIMULATION_FIXTURE_ONLY` 合成场景，独立于模型输出且没有模型请求标识；这只核对答案 Schema、摘要与冻结引用，不代表临床答案复核。

使用明确标记为 `SIMULATION_FIXTURE_ONLY` 的两条场景输入运行聚合器：任务完成 1/2，耗时 120 与 240 秒，协助 2 次，转录差错 1 次，文字误解 1 次，中断 1 次。四项场景理解结果分别为：来源 2/2，资料不足 1/2，下一步 1/1 已回答且另有 1 项未回答，联系方法 1/2。输入注明没有家庭参与者及实际设备。这些数值只核查汇总程序；真实家庭理解继续 `NOT_RUN`。

同样使用 `SIMULATION_FIXTURE_ONLY` 输入核查两项冻结标签的独立答案结构、答案摘要、维护者和冻结引用。答案文字清楚注明它属于合成场景，未作为 T48 原始 Gold 文字。临床人员复核继续 `NOT_RUN`。

实际历史回放需提供逐事实 `available_at` 与 `known_at`、固定执行版本、每条提示的独立结局和来源。当前 T48 replay 缺少这些字段，未来资料排除状态为 `NOT_VERIFIED`。分组报告显示没有验证患儿，独立验证表现无法测量。

历史回放输入要求按冻结患儿保留 `development`／`validation` 单一分组，逐条绑定标签答案引用和每项事实的 `known_at`。分组指标要求明确保存治疗状态、年龄、感染背景和资料完整度；当前集合只有一名 development 模拟患儿，四项患者分层均未测量。家庭观察输入需含授权引用、观察者、参与者、任务、设备、起止时点、协助、转录差错、文字误解、中断及四项理解回答。程序对空观察表即时终止，不把缺少参与者记录写成零差错。

T55/T59/T32 最终报告已在独立模拟数据库完成保存重读。真实临床效能、临床人员复核、家庭任务观察、手机／微信和视觉检查继续保持 `NOT_RUN`。场景输入与实际结果见 [.planning-work/parallel/window3](../../.planning-work/parallel/window3) 中的 T32 fixture 和报告。

本次往返 T32 artifact ID 为 `3f6ae92d-3704-40b7-905a-35846dc8d083`，SHA256 为 `ff4332196dbfa0f79637ba98e0894ba4cce3771f08988d0415a80afaa4a3c3d3`，完成时间为 `2026-10-09T05:41:06.076448+00:00`。T32 答案与家庭理解输入均为 `SIMULATION_FIXTURE_ONLY`；临床 Gold 原文、临床人员复核及真实家庭观察继续 `NOT_RUN`。
