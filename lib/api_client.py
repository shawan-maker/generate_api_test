"""
lib/api_client.py — 框架级共享 API 客户端 + 函数注册表。

为什么需要它:
  生成代码(api 层薄函数)分散在 projects/<id>/api/<一级>/<二级>/<module>.py 两级目录中。
  项目目录名可能含连字符(如 ecm-compute), 导致 `import projects.ecm-compute...` 这种
  包名导入直接失败(连字符不是合法标识符)。相对导入也因包名非法而无法解析。

解决方案:
  * ApiResponse / get_client 放在框架层 lib, 任何生成代码都用 `from lib.api_client import ...`,
    lib 永远在 ROOT 上, 不存在包名问题。
  * 项目专属常量(base_url / api_base / 鉴权头)存为 api/_client.json(按路径读取, 不靠 import)。
  * 运行时通过 get_fn(project_id, service, resource, crud) 用 importlib 按文件路径加载
    api 模块并取出对应薄函数 —— 完全不依赖包名, 对含连字符的项目同样适用。
"""
import importlib.util
import json
from pathlib import Path
from typing import Callable, Optional

import httpx


ROOT = Path(__file__).resolve().parents[1]
_MOD_CACHE: dict = {}


class ApiResponse:
    """薄响应包装: 状态码 + JSON body + 原始响应。"""

    def __init__(self, resp: httpx.Response):
        self.status = resp.status_code
        self.headers = dict(resp.headers)
        self.text = resp.text
        self._json = None
        try:
            self._json = resp.json()
        except Exception:
            pass

    @property
    def json(self) -> Optional[dict]:
        return self._json

    @property
    def success(self) -> bool:
        """estack 真成功标准: HTTP 2xx + body.success==true + !errorCode。"""
        if self.status >= 300:
            return False
        if self._json and isinstance(self._json, dict):
            return bool(self._json.get("success")) and not self._json.get("errorCode")
        return self.status < 300

    def __repr__(self):
        return "<ApiResponse status=%s, success=%s>" % (self.status, self.success)


def _client_cfg(project_dir) -> dict:
    """读取 api/_client.json 中的项目专属常量。"""
    p = Path(project_dir) / "api" / "_client.json"
    if not p.exists():
        raise FileNotFoundError(f"未找到 api/_client.json: {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def get_client(project_dir, token: str = "") -> httpx.Client:
    """
    构造带鉴权头的 httpx.Client。
    project_dir: projects/<id> 目录路径(字符串或 Path)。
    token: 可选, 为空时跳过 Authorization 头(由调用方通过 cookie/session 注入鉴权)。
    """
    cfg = _client_cfg(project_dir)
    headers = dict(cfg.get("fixed_headers", {}))
    if token:
        headers[cfg["header_name"]] = cfg["header_prefix"] + token
    return httpx.Client(
        base_url=cfg["base_url"] + cfg["api_base"],
        headers=headers,
        verify=False,
        timeout=30,
    )


def _safe_fn_name(crud: str, resource: str, service: str) -> str:
    """
    与 generators/gen_api_layer._safe_fn_name 保持一致的命名规则,
    避免两套命名逻辑漂移。直接复用生成器中的实现。
    """
    from generators.gen_api_layer import _safe_fn_name as _gen
    return _gen("", crud, resource, service, "")


def _module_path(project_id: str, service: str, resource: str) -> Path:
    """根据 menu_tools 的两级目录映射, 计算 api 模块文件路径。"""
    proj_dir = ROOT / "projects" / project_id
    catalog_path = proj_dir / "kb" / "api_catalog.json"
    if not catalog_path.exists():
        raise FileNotFoundError(f"未找到 catalog: {catalog_path}")
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    from generators.menu_tools import get_resource_dir
    rel = get_resource_dir(str(proj_dir), catalog, f"{service}/{resource}")
    module_name = resource.replace("-", "_").replace(".", "_") or "resource"
    if rel == "未归类":
        return proj_dir / "api" / "未归类" / f"{module_name}.py"
    group, label = rel.split("/", 1)
    return proj_dir / "api" / group / label / f"{module_name}.py"


def _load_module(project_id: str, service: str, resource: str):
    """按文件路径用 importlib 加载 api 模块(带缓存)。"""
    mod_path = _module_path(project_id, service, resource)
    if not mod_path.exists():
        raise FileNotFoundError(f"api 模块不存在: {mod_path}")
    key = str(mod_path)
    if key not in _MOD_CACHE:
        synth_name = f"_api_{project_id}_{service}_{resource}".replace("-", "_").replace(".", "_")
        spec = importlib.util.spec_from_file_location(synth_name, mod_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _MOD_CACHE[key] = mod
    return _MOD_CACHE[key]


def get_fn(project_id: str, service: str, resource: str, crud: str) -> Callable:
    """
    按 (project_id, service, resource, crud) 取出 api 层薄函数。
    例: get_fn("estack", "draco", "accesskey", "create") -> draco_create_accesskey
    例: get_fn("ecm-compute", "draco", "accesskey", "list") -> draco_list_accesskey
    """
    mod = _load_module(project_id, service, resource)
    fn_name = _safe_fn_name(crud, resource, service)
    fn = getattr(mod, fn_name, None)
    if fn is None:
        raise AttributeError(f"模块 {mod.__name__} 中无函数 {fn_name}")
    return fn
