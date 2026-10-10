"""动作标识与类别。

病例动作使用 access_grant；全局配置、关联核验与运维动作使用 service_role_grant。
is_staff、is_superuser、Django Group 与服务进程身份均不自动授予病例读取。
"""

# 病例动作：需要 access_grant 且绑定患儿与字段范围。
CASE_ACTIONS = frozenset(
    {
        "read_raw",
        "write_record",
        "correct_record",
        "read_history",
        "read_file",
        "view_request",
        "manage_draft",
        "receive_alert",
        "create_summary",
        "read_summary",
        "request_correction",
        "request_disposition",
        "read_case",
    }
)

# 服务动作：需要 service_role_grant，按岗位与环境授予。
SERVICE_ACTIONS = frozenset(
    {
        "verify_field",
        "revoke_verification",
        "read_assessment",
        "execute_assessment",
        "import_case",
        "decide_relation",
        "manage_grant",
        "manage_config",
        "manage_configs",
        "approve_config",
        "register_validation",
        "publish_config",
        "manage_contacts",
        "manage_disposition",
        "manage_data_use",
        "manage_notifications",
    }
)

ALL_ACTIONS = CASE_ACTIONS | SERVICE_ACTIONS
