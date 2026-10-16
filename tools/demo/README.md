# 追加家庭体验资料

这是现有 H5 的离线数据工具，调用既有后端服务；没有新 mock 页面，也不会改旧 seed。仅支持已存在的两个虚构示例档案、明确匹配的家庭账号、DEBUG 和 test_* PostgreSQL 数据库，以及无外部 provider 的已批准软件测试 runtime。

固定批次 `family-enrichment-20261012-v1` 新增：

- 56 条早晚观察：每名示例患儿 14 天、每天 2 次；上海时区的分钟级时间由正常后端解析
- 6 份检验记录，每份 8 项，6 张数值/单位/范围/标记/采样日期完全对应的原图
- 4 条已结束的虚构药物使用备忘，药名/剂量均明确为示例，无治疗建议
- 4 份家庭就诊备忘及原图，保持未专业核对，无 OCR/临床就诊认定
- 4 份可恢复的报告/治疗表单草稿
- 2 份由真实本地任务服务生成的固定摘要

既有记录、更正、用户新建记录、草稿、摘要、权限和配置不会修改。批次固定键重跑是 0 新增；已更改、放弃或过期草稿不恢复。撤回、删除或冲突的资料会使整个批次失败，不能借重跑复活。旧摘要可能因合法新增数据自然过期，仍保留原 ID 与快照；重跑不会重生成新批次摘要。

体温点图保留真实原值。家庭自报没有机构依据，因此不为画线填造机构；有日期不完整的旧记录也保持原样。该数据没有临床评估、专业核验、临床预警、对外推送或自动识别结果。

## 执行

操作人员必须指定当前已资格确认的后端和配置目录，通过 `PYTHONPATH` 指向它们；工具不寻找账号秘密、不读密码/会话、不切换或重启服务。执行前需另行获得当前实例的数据追加许可，并保护用户正在使用的页面。交付本工具不代表已更新任何运行实例。

```sh
PYTHONPATH=/verified/frozen/backend:/verified/runtime/settings \
DJANGO_SETTINGS_MODULE=integration_settings \
/verified/python tools/demo/run_enrichment.py \
  --username EXACT_EXISTING_USER --principal-id EXACT_EXISTING_PRINCIPAL \
  --acknowledge-test-only
```

可先在同一命令增加 `--check-only`，只校验已有账号/患者/资格和本批次记录、草稿的既有回执，不创建数据。预览会明确列出完整计划数量与已存在数量；执行时仍会进一步逐字节核对原件及所有不可变输入、文书和摘要，不把预览等同于完成。

入口先运行既有 `admit_family_worker` 真实 artifact/配置/依赖校验，再执行同一事务中的普通服务写入。不要改动后端 artifact 排除集合或跳过资格检查。新工具和测试位于仓库根 `tools/demo/`，不加入 Django 应用，不改变既有部署 artifact；本工具仍应单独锁定 SHA 并审查。

## 隔离测试

在新的隔离 PostgreSQL/Redis 测试实例中，加入 `tools/demo` 和 `tools/demo/tests` 到 `PYTHONPATH`，使用既有 Django runner：

```sh
python backend/manage.py test test_enrichment --noinput
```

不可使用保留的浏览器体验数据库运行测试。测试覆盖严格身份/环境限制、66 条真实写入、原件一致性、分钟级时间解析、真实摘要、双患者隔离、旧数据与权限保留、重跑、用户编辑后重跑、失败回滚与已处理内容不可恢复。恢复重建后的候选必须重新验证，旧候选的历史通过结果不能证明新候选通过。
