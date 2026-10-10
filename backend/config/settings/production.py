"""生产与集成环境配置。

所有值来自环境变量，进程启动时调用 assert_runtime_config 核验。
"""
from .base import *  # noqa: F401,F403
from .base import assert_runtime_config

assert_runtime_config()
