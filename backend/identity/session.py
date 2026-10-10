"""会话与 CSRF：绑定主体、轮换与清理。

auth_bootstrap 建立匿名会话 CSRF secret；登录成功轮换 Session 与 CSRF token。
"""
from django.middleware.csrf import get_token

SESSION_PRINCIPAL_KEY = "_sjia_principal_id"
SESSION_AUTH_EPOCH_KEY = "_sjia_auth_epoch"
SESSION_AUDIENCE_KEY = "_sjia_audience"


def bootstrap_csrf(request) -> str:
    """建立（如需要）匿名会话 CSRF secret，返回 masked token。"""
    return get_token(request)


def establish_session(request, *, principal_id, auth_epoch, audience) -> None:
    request.session.pop('_sjia_selected_patient_id', None)
    request.session[SESSION_PRINCIPAL_KEY] = str(principal_id)
    request.session[SESSION_AUTH_EPOCH_KEY] = auth_epoch
    request.session[SESSION_AUDIENCE_KEY] = audience
    request.session.cycle_key()
    request.session.save()


def read_session_principal(request) -> tuple:
    principal_id = request.session.get(SESSION_PRINCIPAL_KEY)
    auth_epoch = request.session.get(SESSION_AUTH_EPOCH_KEY)
    audience = request.session.get(SESSION_AUDIENCE_KEY)
    return principal_id, auth_epoch, audience


def clear_session(request) -> None:
    request.session.flush()
