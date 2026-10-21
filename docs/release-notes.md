# 版本说明

doc_id：SJIA-RELEASE-NOTES；status：CURRENT；last_verified：2026-10-21（Asia/Shanghai）。维护责任：根据实际提交、制品与验证证据更新。当前包版本 `sjia-guardian-h5@0.1.0`；完整产品验收与生产发布尚未完成。

## 源码 `f05dbcf`，2026-10-19

该提交相对 `8ca48b9` 的变更依据为 Git 差异与 QA12—QA29 主记录。详细业务范围及全部未完成项见[当前交接](../CURRENT-DELIVERY-20261021.md)。

| 能力 | 当前行为 | 验证依据 |
| --- | --- | --- |
| 真实资料保存与来源 | 83 项真实候选经过本人确认、保存和来源复验；专业状态继续保持 unverified | [QA12](development/QA12-real-ocr-validation.md) |
| 文书、观察范围与关系 | 保存准确原文入口、本人查询范围与历史、PDF 第一页手工正文、相同字节关系及解除历史 | [QA16—QA23](development/index.md) |
| 支持访问与权限 | 支持登录核验受众及岗位，未授权专业核对和 current／replay 入口拒绝读取，提供访问恢复入口 | [QA26](development/QA26-support-access-recovery.md)、[QA27](development/QA27-real-clinical-access-scope.md) |
| 原件范围读取 | Range 使用登记大小与 `get_range`；保持原字节与权限，返回准确 206／416，长度不符立即拒绝 | [QA29](development/QA29-real-original-range-reads.md) |
| 配置制品来源 | 后端制品包括全部本地应用、config、API 与 Schema；QA29 清单共 780 个文件，其中 API 源码及 Schema 为 53 个 | [QA29](development/QA29-real-original-range-reads.md) |
| 目标版本兼容 | Python 3.13.13／PostgreSQL 17.11 完成锁定依赖、102 项正式迁移、完整目录比较与真实数值参数传输 | [QA28](development/QA28-target-runtime-compatibility.md) |

## 已测量结果

| 测量 | 真实输入、环境与结果 |
| --- | --- |
| 重复资料目录 | QA24 的同一本地请求：SQL 从 7486 条变为 2718 条，单次读取从 11.121 秒变为 3.350 秒；266 次授权和 265 次来源核验保持 |
| PDF 范围读取 | QA29 的 489686822 字节 PDF、1024 字节 Range：Python 分配峰值从 489936904 字节变为 264158 字节；63 份真实原件响应字节与头部逐项保持 |

测量方法、准确输入和原始结果分别见 [QA24](development/QA24-real-relation-read-performance.md)及 [QA29](development/QA29-real-original-range-reads.md)。单次本地测量没有覆盖正式 P95、P99、并发容量或临床效果。

## 接入与迁移事项

- 观察范围读取使用运行版本准确引用的 `observation_period_visibility_config_id`；保存还要求准确 observation 保留策略与本人写入权限，见[运行可见字段说明](../backend-design/observation-period-runtime.md)。
- 后端制品范围变化后，准确制品绑定的验证及运行采用需要重新核验；旧批准仅对其原范围有效，见[配置证明](../backend-design/configuration-evidence.md)。
- 支持操作需使用支持会话与实际岗位，专业字段和原件继续执行各自权限核验。身份接入见 [API 指南](reference/api-integration.md)。
- 目标版本空库兼容已验证；已有资料升级、备份恢复、对象存储适配与生产发布继续满足其完整验收条件。正式迁移以 `backend/*/migrations/` 为执行依据。

## 文档更新，2026-10-21

README、H5 说明、当前交接、知识索引、API 指南、身份与原件 OpenAPI、路由目录、数据字典、操作说明和开发索引均引用当前源码与证据范围。文档检查与前端构建结果见 [DOCS01](development/DOCS01-current-documentation.md)。本次仅更新本地资料，没有产生新的生产发布或临床验收结论。

## main 代码与测试资料，2026-10-21

源码接收 `b094448` 的全部改动，包括 QA30—QA43 的 S3 及恢复能力、共享会话、支持会话期限、测试登录、家庭资料与方案目录并发，以及 QA68 的历史时间和确定性提示。对应验证范围继续引用各自主记录，当前交付记录的 11 项 STALE 和 49 项 NOT_RUN 保持完整条件。

用户明确授权将测试账号资料随代码文档提交至 main。[测试资料包](../test-data/README.md)包含两位虚构患儿、47 张表、3201 条记录与 6 份生成附件，完整清单和校验和一并保存。真实资料、账号凭据、数据库文件、会话及私有执行报告保留本地。
