# 应用存储选择与运行身份

本规范承接HLD C09、FR-02、FR-09及NFR-07／09。应用使用单一明确存储选择，所有现有文件生产者和读取方继续通过files.service.object_store调用。

## 配置与构造

SJIA_FILE_OBJECT_BACKEND显式选择local或s3，本地开发缺省为local。local使用现有SJIA_FILE_OBJECT_ROOT，s3必须提供SJIA_S3_STORAGE_CONFIGURATION_FILE的绝对文件路径。未登记类别、缺少配置或配置同时冲突时立即抛出准确错误。

S3配置文件使用s3-private-storage-1，正式Schema为files/s3-private-storage.schema.json。文件必须属于当前进程身份、为普通文件、禁止组权限及其他用户权限且不超过65536字节；实际配置采用0600，读取前后核验准确文件身份。JSON由Pydantic解析，秘密字段使用SecretStr并隐藏验证错误中的输入值。

| 字段 | 用途 |
| --- | --- |
| provider_id、endpoint_url、region、bucket、prefix、owner_id | 准确物理提供方、位置及拥有者 |
| access_key、secret_key、session_token | 实际提供方凭据，仅从受限私有文件读取 |
| ca_bundle | 显式CA文件绝对路径，空值使用系统证书 |
| control_directory | 独立0700证明目录绝对路径，身份与S3登记适配共同核验 |
| bucket_policy_sha256 | 精确Bucket策略摘要或明确absent |
| connect_timeout、read_timeout | 实际SDK连接及读取期限 |

files/storage_factory.py保存StorageSelection，files/service.py通过SimpleLazyObject使用该选择。模块及URL检查不会隐式连接数据库或提供方，管理命令可以在准备真实凭据之前导入应用。实际文件操作与运行准入才解析所选配置。

## 运行配置传播

backend-runtime-1增加可选object_storage声明。已有本地声明保持其原有形式；选择S3时必须提供完整S3声明。

| 声明 | 准确字段 |
| --- | --- |
| local | kind与root_identity_sha256 |
| s3 | kind、binding_sha256、access_configuration_sha256、control_directory_sha256、configuration_file_sha256 |

控制目录摘要包含绝对目录、device、inode、owner及权限，不包含正常内容写入引起的目录时间变化。该目录身份仅证明实际文件系统控制目录，S3物理引用继续使用真实VersionId及ETag。

运行核验比较当前真实选择与已批准声明，并核查S3 Bucket版本、AES256、owner、ACL及准确策略。凭据配置文件内容变化要求明确重启与相应配置采用，不能静默采用新选择。完整声明进入运行依赖摘要，实际进程保存自己的采用身份。

原件与用途字节继续使用S3负载接口，journal和receipt使用独立控制目录。S3没有本地负载root属性。现有本地恢复标记、数据库恢复状态或明确恢复根路径无法直接作为S3恢复采用依据；准确S3恢复目标、版本映射与独立检查继续完整接入。

## 错误与验证

core.errors.CodedRuntimeError共同维护准确reason_code。S3错误、存储配置错误及运行准入错误可以沿实际Worker与运行状态传递，秘密输入不会进入运行声明。

验证使用已授权真实原件、准确业务元数据、当前独立私有S3及实际文件权限。普通应用入口的存储读取、不可用配置、声明不一致、目录身份变化、离线管理导入及真实副本写入与处置分别保留实际证据。完整申请、队列执行、内容清除、S3恢复和部署仍需其完整运行验证。
