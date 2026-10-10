"""身份服务：主体解析、账号停用与 auth_epoch 失效。

认证 Adapter 创建身份时核验 issuer/provider 与 subject，不接受前端声明的 principal_id。
自建用户体系（D02）使用 PrincipalCredential 保存账号与密码哈希。
"""
from datetime import datetime, timezone

from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction

from identity.models import Patient, Principal, PrincipalCredential, PrincipalIdentity


class IdentityRejected(Exception):
    def __init__(self, reason_code):
        super().__init__(reason_code)
        self.reason_code = reason_code


@transaction.atomic
def resolve_principal(*, provider, provider_subject, verified_at=None) -> Principal:
    """按 provider + subject 查找或建立主体，返回 principal 与其 auth_epoch 一致。"""
    identity = PrincipalIdentity.objects.filter(provider=provider, provider_subject=provider_subject).first()
    if identity is not None:
        return identity.principal
    now = datetime.now(timezone.utc)
    principal = Principal.objects.create(account_status=Principal.AccountStatus.ACTIVE, created_at=now, updated_at=now)
    PrincipalIdentity.objects.create(
        principal=principal,
        provider=provider,
        provider_subject=provider_subject,
        verified_at=verified_at or now,
    )
    return principal


@transaction.atomic
def register_account(*, username, password) -> Principal:
    """自建账号注册：建立 principal、self 身份与密码凭证。"""
    if not username or not password:
        raise IdentityRejected("CREDENTIAL_REQUIRED")
    if len(password) < 8:
        raise IdentityRejected("PASSWORD_TOO_SHORT")
    if PrincipalCredential.objects.filter(username=username).exists():
        raise IdentityRejected("USERNAME_TAKEN")
    now = datetime.now(timezone.utc)
    principal = Principal.objects.create(account_status=Principal.AccountStatus.ACTIVE, created_at=now, updated_at=now)
    PrincipalIdentity.objects.create(principal=principal, provider="self", provider_subject=username, verified_at=now)
    PrincipalCredential.objects.create(
        principal=principal, username=username, password_hash=make_password(password),
        created_at=now, updated_at=now,
    )
    return principal


def authenticate_account(*, username, password) -> Principal:
    """自建账号登录：校验密码哈希并返回主体。"""
    credential = PrincipalCredential.objects.filter(username=username).first()
    if credential is None or not check_password(password, credential.password_hash):
        raise IdentityRejected("CREDENTIAL_INVALID")
    principal = credential.principal
    if principal.account_status != Principal.AccountStatus.ACTIVE:
        raise IdentityRejected("ACCOUNT_NOT_ACTIVE")
    return principal


@transaction.atomic
def disable_account(principal: Principal) -> Principal:
    principal.account_status = Principal.AccountStatus.DISABLED
    principal.auth_epoch = principal.auth_epoch + 1
    principal.updated_at = datetime.now(timezone.utc)
    principal.save(update_fields=["account_status", "auth_epoch", "updated_at"])
    return principal


def account_matches_epoch(principal: Principal, auth_epoch) -> bool:
    return principal.account_status == Principal.AccountStatus.ACTIVE and principal.auth_epoch == auth_epoch
