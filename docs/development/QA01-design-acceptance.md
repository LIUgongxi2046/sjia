# QA01 功能与设计验收

日期：2026-10-04。文档有效性：CURRENT。需求为 PRD FR-01—FR-12、AC-01—AC-12、AC-H5 与 UI 3.0；唯一交付记录为 planning/delivery-ledger.json。

首页健康档案展示四类资料；观察表单读取获准字段定义；报告与治疗支持照片整理和原图回读；观察、报告、治疗、更正均支持服务端草稿；查询按医学时间分页；历史保留全部字段、原图与更正原因；提示读取当前有效发布正文、来源及联系渠道。任务实现与逐项证据由 [QA02 主记录](QA02-local-completion.md) 和 [QA02 报告](../testing/QA02-report.md) 维护。

用户已授权本地模拟资料。工程、模拟业务与接口检查使用实际运行的 Django、PostgreSQL、Redis、Chrome、Vite 和 Ark 接口。没有替换服务返回，没有主动视觉检查。

本地模拟范围验收通过。实际手机、临床 Gold、真实患者与生产交付的证据继续由 T30—T33 管理。相关模块为 T10—T16、T21、T22、T24、T25、T29；版本绑定证据见 [manifest.json](../testing/evidence/QA02/manifest.json)。
