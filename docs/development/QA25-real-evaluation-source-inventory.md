# QA25 真实评价来源清单

当前状态ACCEPTED，范围为本地真实来源完整性及数据库页面映射。文档有效性CURRENT，last_verified为2026-10-19 04:03（Asia/Shanghai）。需求为FR-20、GRAC-08，关联T48、QA10、QA11、QA12和QA14；本记录为来源清单软件专项唯一状态来源，完整V48继续由planning/delivery-ledger.json维护。

Given 已授权的63份原件及已接收页面，When 使用现有inspect_evaluation_sources和当前数据库核验来源，Then 原件路径、准确SHA256、字节数、正式文件身份和完整页序逐项一致，未知临床单位保持未知，不创建临床标签或扩大用途。

本专项使用现有SourceManifest、管理命令、FileObject与FilePage；原件目录保持只读。清单生成在已忽略的原件目录上级，私有报告位于.qa-work/QA25。数据库核验使用只读事务，不创建正式评价集合、用途副本、独立答案、专业角色或模型请求。

验收条件为63份文件共968528634字节与现有接收证据及当前原件一致，290个准确FilePage逐项映射并保持连续页序，管理命令与直接读取结果一致，未登记评价单位继续未知，临床事实与识别任务保持。文件完整性检查不授予格式、流程或临床评价资格。

## 检查及证据

| check_id | 准确操作与断言 | 结果 |
| --- | --- | --- |
| QA25-01 | 当前63份FileObject的身份、版本、来源批准、大小和实际存储字节与原接收证据逐项相同 | PASS |
| QA25-02 | PyMuPDF及Pillow读取3份PDF、59份JPEG和1份PNG的准确元数据，页数分别累计为230页和60页 | PASS |
| QA25-03 | 原件对应290个准确FilePage及ArchivePage，每份文件与分组内页序连续，重传对象没有重复计数 | PASS |
| QA25-04 | 正式来源检查函数重新读取968528634字节，准确SHA256全部相同 | PASS |
| QA25-05 | inspect_evaluation_sources管理命令与直接函数返回相同完整JSON | PASS |
| QA25-06 | 未登记评价单位保持null，unit_mapping为unrecorded，用途资格为not_evaluated | PASS |
| QA25-07 | 只读事务内83项事实、249项事实版本、全部临床记录、历史整理及两项未知识别任务保持 | PASS |
| QA25-08 | 原始目录全部文件路径、大小和修改时间保持 | PASS |
| QA25-09 | 真实来源清单与报告绑定准确相同版本 | PASS |
| QA25-10 | 真实数据库仍无评价集合、分组、标签、运行及评价用途副本 | PASS |
| QA25-11 | 已采用后端制品和运行配置保持 | PASS |
| QA25-12 | 私有清单及完整映射报告由当前gitignore排除 | PASS |

共12项检查通过。真实来源清单SHA256为dc7e0d330646a0aaf7e8439e047e4a292c9437ca7f1270b111e69dd23bbac70b，完整原始报告SHA256为eb7d843275c862397a2025b613b12e7dca0cee19af9edb0efa614f911851b1e7。私有清单位于已忽略的模拟数据目录，完整文件及页面映射保存在.qa-work/QA25/reports/final.json；最终审计、源码摘要及检查列表保存在同目录audit.json。脚本为check_source_inventory.py与audit_final.py，使用backend/.venv及项目内TMPDIR；没有模型调用、截图或新增临床事实。

本专项测量采用的后端制品为cc805753fbcbf7db94423f453dd50780083e5e2a885758b92b6a22073152a68b，runtime为38487adc-a41d-4cb8-a339-b172ba2c6704。本次没有修改产品代码、数据库结构或配置，63份原件和290个正式页面保持。

## 单位与资格

来源文件数量为63，数据库页面数量为290，来源分组数量为5。这些资料尚未登记为获准的EvaluationUnitManifest；现有来源检查函数按其声明规则，将评价页面及其他未登记单位保持null。数据库页面核验与评价单位资格分别保存，不用目录分组推断独立患者、就诊、采样事件、MAS事件、标签或人时。

独立患者数量仍为null，专业核对及临床批准未产生。真实数据库中的EvaluationDatasetVersion、EvaluationPatientPartition、EvaluationLabel和EvaluationRun均为0；没有format_evaluation、workflow_evaluation或clinical_evaluation用途副本。完整评价集合、患者独立身份、用途批准、分组、标签、冻结及临床运行继续保留原验收要求，完整V48保持NOT_RUN。

## 关联记录

T48和来源说明采用本次准确原件与页面证据；QA14继续维护完整开发与06:00 main提交要求。当前运行、权限与252项有效整理由[QA27](QA27-real-clinical-access-scope.md)维护，来源清单、全部未知外部请求和临床验收要求保持。
