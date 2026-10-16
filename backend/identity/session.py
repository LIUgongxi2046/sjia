"""会话与 CSRF：绑定主体、轮换与清理。

auth_bootstrap 建立匿名会话 CSRF secret；登录成功轮换 Session 与 CSRF token。
"""
import json

from django.contrib.sessions.models import Session
from django.middleware.csrf import get_token, rotate_token
from django.utils import timezone
from django.utils.crypto import salted_hmac

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
    rotate_token(request)
    request.session.save()


def read_session_principal(request) -> tuple:
    principal_id = request.session.get(SESSION_PRINCIPAL_KEY)
    auth_epoch = request.session.get(SESSION_AUTH_EPOCH_KEY)
    audience = request.session.get(SESSION_AUDIENCE_KEY)
    return principal_id, auth_epoch, audience


def session_read_metadata(request, *, principal_id, auth_epoch, audience):
    """Read-only continuity hint and the persisted server-side expiry.

    Django's get_expiry_date() recomputes now + cookie age for ordinary sessions;
    reading it repeatedly must not suggest that an unchanged session lives
    longer. A pending middleware save may extend the stored expiry later, so the
    current persisted value is conservative. Missing/expired storage fails
    closed with an unknown binding and an already-due deadline.

    The HMAC is a domain-separated comparison marker, never a login credential.
    Its inputs and signing key are not exposed or accepted from a client.
    """
    now = timezone.now()
    key = request.session.session_key
    expires_at = (Session.objects.filter(session_key=key).values_list('expire_date', flat=True).first()
        if key else None)
    if expires_at is None or expires_at <= now:
        return {'session_binding': None, 'expires_at': (expires_at or now).isoformat()}
    value = json.dumps([key, str(principal_id), auth_epoch, audience], separators=(',', ':'))
    binding = salted_hmac('sjia.session-continuity.v1', value, algorithm='sha256').hexdigest()
    return {'session_binding': binding, 'expires_at': expires_at.isoformat()}


def clear_session(request) -> None:
    request.session.flush()
