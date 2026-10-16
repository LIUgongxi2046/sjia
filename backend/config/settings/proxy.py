"""Explicit proxy trust; only use with an isolated API and a header-overwriting proxy."""


def trusted_proxy_header(value='0'):
    if value not in ('0', '1'):
        raise ValueError('SJIA_TRUST_PROXY_TLS must be explicitly 0 or 1')
    return ('HTTP_X_FORWARDED_PROTO', 'https') if value == '1' else None
