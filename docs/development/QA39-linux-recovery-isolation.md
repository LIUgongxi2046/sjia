# QA39 Linux恢复隔离验证

task_id为QA39，来源为QA38-04／05／06，需求FR-02、FR-09、NFR-07／09及OPS，状态COMPLETE，日期2026-10-19。产品修改继续位于codex/s3-private-storage，main保持已发布内容。

## 输入与必需条件

读取[QA38](QA38-s3-operator-recovery.md)、实际备份入口和独立恢复规范。主机macOS13.7.8、Intel四个逻辑CPU，Virtualization.framework实际支持。IPv6 PostgreSQL17.11和SeaweedFS4.48通过真实进程、回环监听、TLS及私有Bucket检查；恢复操作依据用户已有软件测试授权和准确目标记录执行。

本任务使用官方Lima与Debian12建立独立Linux验证环境。工具和运行文件保存在原项目忽略目录.qa-work/QA39；应用依赖、原服务与真实原件保持。创建环境之前核验发布摘要、资源和配置，禁用默认用户目录挂载、SSH agent与容器服务。环境准备仅访问公开软件来源，真实资料在网络隔离通过后按准确范围接入。

| check_id | 必需行为 | 当前状态 |
| --- | --- | --- |
| QA39-01 | 官方工具摘要、主机兼容性及实际Linux运行身份核验 | PASS：Debian12、Linux6.1、Python3.13.13、80项准确锁定依赖及PostgreSQL17.11客户端 |
| QA39-02 | 限定挂载与真实目标连接，通过双地址族外发拒绝及子进程继承检查 | PASS：两个回环目标、九项拒绝、两项原生子进程拒绝及逐项账号专属内核计数 |
| QA39-03 | 独立可信journal与当前checkpoint核验，真实备份只读输入保持摘要 | PASS：73份输入文件、63份原件共968528634字节；来源SQL和journal锁定期间完成传递与完整核验，checkpoint4保持 |
| QA39-04 | 将准确隔离证据提供QA38，实际CLI恢复与拒绝验证分别执行 | PASS：真实CLI恢复及四项重放、63份完整读取、189处范围比较和15项CLI拒绝；目标业务表、控制文件与S3版本保持 |

## 有效性与接收

工具可以运行、配置存在或Codex模拟均不能替代隔离检查。只有实际允许目标可访问、其他目标受到内核规则拒绝、子进程保持同一限制及准确规则摘要保存后，QA39-02才能登记PASS。真实恢复、内容重放和运行采用分别归属QA38；本记录不能提升临床、设备或视觉验收。

linux-runtime-verified.json与process-isolation-verified.json记录实际启动身份、UID1000、禁止权限提升、空补充组、两个只读virtiofs挂载及准确规则SHA256。规则只允许到::1的19020和55434两个目标连接，以及既有SSH响应和回环错误报文；其他IPv4及IPv6外发受到拒绝。每项目标分别读取恢复账号专属计数前后变化，核验对应地址族增加、其他地址族保持，并核验完整检查前后的内核规则保持。

Linux6.1的ICMP行政拒绝分别返回IPv4的EHOSTUNREACH和IPv6的EACCES，检查同时要求实际内核计数，不根据错误文字单独判定。映射依据为[IPv4官方源码](https://github.com/torvalds/linux/blob/v6.1/net/ipv4/icmp.c#L169)及[IPv6官方源码](https://github.com/torvalds/linux/blob/v6.1/net/ipv6/icmp.c#L1089)。host-preservation-after-inputs.json确认原两套ready／admitted_current服务、249项事实版本、双方各63份原件和既有映射保持。

trusted-inputs-staged.json记录来源SQL全部业务表SHARE锁与独立journal锁期间的实际传递。备份通过APFS复制共享数据区块，文件inode分别独立；Linux挂载只读。可信journal和checkpoint保存在独立目录，由root所有，恢复账号只能读取记录与checkpoint，独立锁文件允许该账号使用。

restore-target-prepared.json记录实际不同集群、空数据库、准确TLS、私有Bucket和空对象范围。目标角色为非超级用户，目录和私有配置分别绑定UID1000。容量检查根据实际数据库18020019字节和全部原件计算，保留2GiB存储余量、数据库增长及操作余量。

restore-cli-executed.json记录正式deploy/recovery.py入口完成恢复，耗时117.319秒；目标68份文件、132项登记、290页及104项迁移保持。69项映射中包括63项复制与6项缺失，另有1项旧对象缺失；四项可信处置重放通过。restored-target-readback-verified.json记录实际读取968528634字节及189处范围响应与备份比较通过。该时长属于本地软件检查，生产RTO由T57继续验收。

restore-cli-refusals-verified.json记录15项实际CLI拒绝，覆盖既有恢复目录、准确身份、binding、控制目录、期限、存储选择、批准、备份摘要、非空S3范围、非空数据库及缺少独立发布依据。另直接核验RESTORE_SEPARATE_RELEASE_APPROVAL_REQUIRED。全部业务表、控制文件和实际S3版本在检查前后保持；ADOPTION_ADMITTED.json不存在，目标应用与Worker没有启动。当前没有新增模型调用，完整运行采用继续由QA38维护。

公开依据为[Lima安装说明](https://lima-vm.io/docs/installation/)、[VZ支持条件](https://lima-vm.io/docs/config/vmtype/vz/)、[挂载说明](https://lima-vm.io/docs/config/mount/)及[v2.2.1正式配置](https://github.com/lima-vm/lima/blob/v2.2.1/templates/default.yaml)。原始过程和结果分别保存在私有报告，后续按实际结果维护本记录。

current-scope-final.json汇集四份实际Linux输入与恢复报告，重新核验相同boot_id、当前内核规则及目标采用阻断状态。host-preservation-final.json确认来源、既有QA36目标和当前CLI目标分别保持63份可用原件，原main的83项事实、249项事实版本与两项待核清OCR保持，两套后端均为ready／admitted_current。此次检查没有新增模型调用；完整功能验收保持INCOMPLETE。

当前测试数据库结构由[QA40](QA40-s3-restore-lineage.md)维护，三处独立数据库均完成105项迁移，原有业务表与两份映射保持。Linux目标四项内容证明、19项SQL身份保护及当前内核规则继续通过。上述正式CLI恢复证据对应其执行时的104项迁移，旧备份在当前结构中的完整再次恢复由QA40继续验收。
