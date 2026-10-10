# 批次页面顺序增量

2026-10-08。维护任务 T42。原始 [v2-schema.sql](v2-schema.sql) 保持不可变，增量使用 [archive-page-order.sql](archive-page-order.sql) 和 archives.0004_page_order。

archive_page.ordinal 为非空正整数，同一 batch_id 内唯一。写入按照 BatchInput.file_group_ids 的准确顺序，再按照每个 FileGroup 内的 FilePage.ordinal 保存连续顺序。读取按 ordinal 排序，并核验从1至实际页面数量的完整顺序；批次数量及原件版本分别检查。

迁移不生成历史顺序。当前数据库正式批次和页面均为0，应用前备份实际数据库与29份原件，恢复到独立数据库并逐表、逐份比较；恢复库及当前库迁移后111张业务表内容与876210字节原件保持一致。已有正式页面的环境需要准确原请求和实际来源顺序，缺少依据时停止迁移。

当前结构、备份摘要及恢复回读由 backend/tests/verify_archive_page_order_current.py 核验，正式证据见 [T42 主记录](../docs/development/T42.md)。空页面的约束检查不能证明实际家庭批次顺序、并发保存或完整业务验收通过。
