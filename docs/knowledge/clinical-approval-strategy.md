# 临床批准策略（D04）

本平台规则用于**预警**，不用于**诊断**。分类与评分输出为提示监护人及时就医的信号，不是医学诊断结论。

## 规则依据

两条确定性规则，均来自同行评审文献，与 `backend/assessment/classification.py`、`backend/assessment/ms.py` 的实现一致。

### 1. 2016 EULAR/ACR/PRINTO MAS 分类条件

适用前提：已知或疑似 sJIA 且持续发热。

- 必需：铁蛋白 > 684 ng/mL。
- 另需至少满足 2 项（共 4 项）：
  - 血小板 ≤ 181×10⁹/L；
  - AST > 48 U/L；
  - 甘油三酯 > 156 mg/dL；
  - 纤维蛋白原 ≤ 360 mg/dL。

来源：Ravelli A, et al. *2016 Classification Criteria for Macrophage Activation Syndrome Complicating Systemic Juvenile Idiopathic Arthritis*. Ann Rheum Dis. 2016;75(3):481–489.

### 2. MS 评分

MS = 2.44×中枢神经受累 + 1.54×出血表现 − 1.30×活动性关节炎 − 0.003×血小板 + 0.001×LDH − 0.004×纤维蛋白原 + 0.0001×铁蛋白。

阈值：MS ≥ −2.1 提示 MAS。

来源：Minoia F, et al. *Clinical features, treatment, and outcome of macrophage activation syndrome complicating systemic juvenile idiopathic arthritis*. Arthritis Rheumatol. 2014;66(11):3160–3169.

## 批准流程

- 规则以 `config_version`（kind=rule / verification）登记，`config_approval` 记录 `approval_role=clinical` 的独立批准。
- 启用前需 `config_validation`（outcome=pass），启用提升 `environment_gate.version` 与 `publication_epoch`。
- 家庭出口（首页、预警、摘要、通知）只采用当前 `release_bundle` 内的获准文案与字段范围。

## 预警而非诊断的约束

- 全部家庭可见文案必须包含「本提示用于预警，不构成诊断，请及时就医」的表述。
- 分类结果 `meets`、MS `≥ −2.1` 只产生预警事件，不输出任何疾病结论。
- 治疗建议、药物剂量一律不进入平台输出，由医生在诊疗中给出。
