# QA29 真实原件范围读取与准确制品绑定

当前状态ACCEPTED，文档有效性CURRENT，last_verified为2026-10-19 05:38（Asia/Shanghai）。关联FR-02、FR-09、NFR-07、OPS、T12、T28、T33、T46、T56、T57及QA14。本记录维护该专项的准确源码、真实原件、当前运行与检查证据，完整文件服务、生产部署及临床验收保留原条件。

Given 用户已授权的63份真实原件、既有账号及准确当前运行配置，When 从正式原件接口读取指定字节范围并复验来源、权限及运行状态，Then 返回准确原始字节，部分读取使用请求范围，当前制品包含完整API源码与Schema，原有资料及专业状态保持。

## 当前实现

| 文件及identifier | 当前行为 | 使用位置 |
| --- | --- | --- |
| backend/api/views_family3.py；file_content | 完整原件授权、内容处置及可用状态检查继续在读取前执行；Range使用登记的不可变原件大小与LocalFileStore.get_range，范围长度不一致立即返回409 FILE_OBJECT_SIZE_MISMATCH | GET /api/files/{file_id}/content |
| backend/files/storage.py；get_range、parse_range | 使用已有存储范围读取与成熟Werkzeug范围解析；请求边界、后缀及416处理保留准确语义 | 当前原件接口；存储代码没有修改 |
| backend/configurations/evidence.py；backend_artifact_manifest、actual_artifact_checksum | 后端制品清单包含全部本地应用、config、api、依赖及正式Schema，共780个文件，其中完整API源码及Schema为53个文件 | 配置核验、进程采用、业务读取及准确结果来源 |

没有Range的完整原件读取继续返回200及完整字节，部分读取返回206及准确Content-Range。非法范围由已有统一处理返回416及实际登记大小。范围请求继续要求完整原件授权，字段或页面范围授权保持各自边界。

## 真实检查与测量

| 检查范围 | 实际结果 | 本地证据 |
| --- | --- | --- |
| 63份实际原件范围 | 修改前后分别登录现有账号逐份请求前1024字节；原始字节、完整大小、Content-Range、Content-Length、Accept-Ranges及私有缓存头逐项保持 | .qa-work/QA29/reports/before-http.json；after-http.json |
| 同一真实PDF测量 | 原件489686822字节、请求1024字节；Python内存峰值从489936904字节变为264158字节；返回字节摘要与范围完全相同 | before-profile.json；after-profile.json及两个实际HTTP报告 |
| 13项边界与权限 | 后缀、开放结束、超出结束、单字节、完整开放范围、超长后缀、无法满足范围、非法单位、多范围、反向范围、完整读取、匿名及没有病例授权的实际管理账号均通过 | boundaries.json |
| 准确制品资格 | 313份真实配置及26份原验收配置逐份核验；290份图像处理配置保持其准确制品，23份真实后端与26份原验收后端采用当前制品 | real-qualification.json；previous-qualification.json |
| Chrome实际入口 | 16项共享支持端登录、受众、岗位拒绝、返回及手动恢复检查通过，页面运行错误为零 | support-browser.json |
| 整理采用 | 原整理按准确新制品失效并保留全部历史；新结果包含相同252项完整内容，准确原值、来源、未知时间与未核对状态逐项相同 | delta-refresh.json；delta-current.json |
| 全部来源与资料 | 63份原件共968528634字节重新完整计算SHA256，290个原始数据库页面身份保持；全部249项事实投影、本人权限及临床数量保持 | final.json |
| 模型与正式迁移 | Python3.13实际执行makemigrations --check --dry-run，退出码0，No changes detected；当前真实数据库只读核验，没有生成文件或修改Schema | .qa-work/QA29/migration-state-check.log |
| 当前服务与清理 | 两套后端均ready及admitted_current，5176与5177可访问；8004分析进程已停止 | final.json及准确运行进程记录 |

内存测量使用同一实际接口、同一原件与相同Range，在独立本地Django进程使用tracemalloc读取Python分配峰值。单次本地耗时分别为0.627秒和0.318秒，测量范围没有作为P95、P99或正式容量结论。完整原件长度不符分支具有立即拒绝检查，本次原始文件大小均匹配，没有制造损坏文件。

## 当前准确状态

后端制品为f40674cd99096eeb58bb4c506745f0b50bcf125800c87178d9bdd78861f8d023，runtime保持38487adc-a41d-4cb8-a339-b172ba2c6704。图像处理制品保持b8c9f9233fb4ec77cb520bde843a933f34f5c0baf1e8935ba5ab80f32bc38ea5，配置正文、用途、audience和联系期限没有修改。

三个RecordVersion、249个RecordField、83个ArchiveFact、249个ArchiveFactVersion与两个ObservationPeriodVersion保持。当前十份整理保留2520项历史，一个有效结果4797a494-0805-45ef-b546-e2987cd4a8b5包含252项。专业核对证明、核对请求、静态及动态评估和临床事件数量均为零，没有增加病例核对授权。全部事实完整投影SHA256保持c842805778a23c4228d1d7bbd98cdf7249979a94742dcbe69f0f141f53cf98e6。

三项OCR仍有两项uncertain，继续保留2 CNY及两个并发位置；没有在途任务或新增模型调用。其他页面识别、专业核对、独立临床答案、真实音频、实际设备、视觉、正式对象存储及生产恢复继续按原要求验证。

最终八项审计通过，准确源码SHA256分别为views_family3.py的e6a3a39013074d53c893534b25cc3758502e4d7c0c09a1844919fc274f13bc29，以及configurations/evidence.py的7b80b7862ab76597d2ee345fcba1d5c8baa30ae4392cd303491065a7409869e7。原始字节、账号、配置证据、测量日志及全部私有报告继续保留于忽略目录。

本次限定审查覆盖真实原件读取、完整授权、准确制品、配置采用、整理内容及运行保持，结论为该专项GO。V12、V33、V56、V57及完整交付保留原有全量条件，当前交付记录继续维护PASS两项、STALE九项、NOT_RUN四十九项和全部60项实施任务。
