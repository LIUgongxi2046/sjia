"""生产与集成环境配置。

所有值来自环境变量，进程启动时调用 assert_runtime_config 核验。
"""
from .base import *  # noqa: F401,F403
from .base import assert_runtime_config

assert_runtime_config()

# Opt in only when nginx is the sole trusted ingress and overwrites this header.
# API must not be published directly. No wildcard trusted CSRF origins are added.
from .proxy import trusted_proxy_header

SECURE_PROXY_SSL_HEADER = trusted_proxy_header(os.environ.get('SJIA_TRUST_PROXY_TLS', '0'))

# Enforced by actual WSGI/ASGI/worker/Beat entrypoints, not migrations.
REQUIRE_PROCESS_RUNTIME_ADMISSION = True
