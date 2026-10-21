# QA35 S3内容处置与前端传播

日期：2026-10-19。需求范围为FR-09、NFR-07／09和OPS，规范见[S3内容处置](../../backend-design/s3-content-disposition.md)。开发位置仍为codex/s3-private-storage独立分支，main保持既有发布。

## 当前实现

content-disposition-policy-2明确允许S3对象及文件用途集合。政策1保持本地对象含义；仅允许deidentified用途的政策无法选择原件，也不能加入超出用途范围的关联文件。

content-disposition-manifest-2保存物理binding及每份文件的完整S3ObjectReference。预览、批准与执行核验准确业务身份、实际登记和版本、唯一对象范围、当前政策与依据、保留限制及权限。未批准的请求返回明确错误。content-disposition-receipt-2通过准确登记、持久证明及实际版本缺失查询说明完成范围，全副本清除继续保持false。

ContentJournal在数据库内容处置之前持久保存准确清单，S3RegisteredDeletion继续保存独立删除证明。ContentJournal和checkpoint均不能与S3控制目录重叠。实际恢复缺少S3映射时，在数据库重放之前拒绝执行。

接口、六份正式JSON Schema、生命周期读取、前端TypeScript及支持页面共同接收准确S3引用。完成状态说明受控文件处置范围，前端校验业务字段、物理引用和完成凭证的binding一致。

## 真实验证

使用已有Python3.13.13、PostgreSQL17.11非超级用户及本地HTTPS S3，继续读取用户已授权资料。独立软件环境根据用户明确委托建立有限期服务授权和病例读取用途，只处置准确批准的技术副本，不授予专业核对资格。软件政策、保留审查、准确清单批准及其依据均实际保存；没有制造临床事实、专业答案或提供方回执。

| 检查 | 当前结果 |
| --- | --- |
| 一份真实字节S3技术副本的预览、准确批准、实际删除及凭证重读 | PASS |
| 重复批准和重复执行使用同一清单与凭证，内容journal只增加一次 | PASS |
| 十五项实际拒绝，涵盖原件排除、旧政策、存储类别、版本、摘要、共享对象、保留限制、撤权和批准范围 | PASS |
| 一份真实字节本地技术副本完成原有政策、清单、批准及实际删除 | PASS |
| 正式前端读取实际保存的S3结果，引用与凭证两项不一致输入拒绝 | PASS |
| 十九份内容处置Schema、Django及迁移检查 | PASS |
| 前端TypeScript和生产构建 | PASS |

S3完整服务检查耗时116.247秒，十五项负向检查由独立进程执行。两份技术副本分别使用原始授权字节，均已经实际删除。63份原件与290个页面保持；独立数据库现有66份FileObject、67份S3登记，其中63份登记available、四份登记disposed。当前内容处置journal包含S3和本地各一项，准确保存两份清单及证明。

来源只读连接的382份元数据与原始快照逐项比较。目标数据库的对应资料容器仅产生两次真实软件处置所需的data_epoch及verification_epoch变化，医学内容、来源记录和原件保持。原main运行保持报告于北京时间11:42:56确认两个API均ready和admitted_current、两个前端可访问，249项事实、原有权限及2CNY未知请求预留保持。本轮没有新增模型调用。

实际证据保存在原项目忽略目录.qa-work/QA35/reports：workflow-verified.json、local-workflow-verified.json、frontend-verified.json及final.json。该目录包含私人身份、批准依据和运行信息，不提交Git。独立技术环境没有启动新的API或Worker，没有配置完整Runtime采用。

## 完整验收范围

当前服务验证直接调用实际业务入口并保存读取数据库和S3结果，尚未代替完整HTTP认证、实际队列、S3恢复映射及部署验收。专业核对、独立临床评价、真实设备、微信及视觉继续保留原要求。全部60项任务及完整功能范围保持。
