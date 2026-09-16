"""
API 层包 — 薄调用函数集合(两级目录组织)。

重要: 取函数请用 lib.api_client.get_fn(project_id, service, resource, crud),
不要 `import projects.<id>.api...` —— 项目目录名可能含连字符(如 ecm-compute),
会导致包名导入失败。get_fn 走文件路径加载, 对含连字符的项目同样适用。
"""
from lib.api_client import get_fn, get_client, ApiResponse

__all__ = ["get_fn", "get_client", "ApiResponse"]
