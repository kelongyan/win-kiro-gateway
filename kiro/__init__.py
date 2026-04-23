# -*- coding: utf-8 -*-

"""
Kiro Gateway 包入口。

这里刻意保持轻量，只暴露版本信息，避免在导入 `kiro`
包时提前触发路由、认证或配置相关的副作用。
"""

from kiro.config import APP_VERSION as __version__

__all__ = [
    "__version__",
]
