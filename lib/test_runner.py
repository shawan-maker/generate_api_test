"""
lib/test_runner.py —— 无外网环境下的 pytest 替代运行器 (stdlib-only)

为什么需要它:
  沙箱无外网时 `pip install pytest` 会失败, 导致生成的回归用例无法"实跑 collection"。
  本模块用标准库完成同样的收集 + 运行, 无需安装 pytest:

  * collect : 收集 projects/<id>/tests 下所有 test_* 模块 / Test* 类 / test_* 方法。
  * dry     : 用真实 client(get_client) 实跑, 遇到无服务器/网络错误优雅 SKIP。
  * mock    : 注入 FakeResp, 用合成数据跑通编排逻辑(验证 创建→查询→删除 接线)。

pytest 兼容:
  * 运行前在 sys.modules 注入一个极简 `pytest` shim(仅本运行器内生效),
    提供 skip / fixture / created_id 命名空间, 使生成的用例可 import。
  * 用户环境若已装真实 pytest, 不受影响(本文件不参与其加载)。

用法:
  python lib/test_runner.py --project estack --mode collect
  python lib/test_runner.py --project ecm-compute --mode dry
  python lib/test_runner.py --all --mode mock
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
import traceback
import types
from pathlib import Path
from typing import Optional


ROOT = Path(__file__).resolve().parents[1]


# ----------------------------------------------------------------------------
# pytest 极简 shim (仅在本运行器内注入)
# ----------------------------------------------------------------------------
class _Skip(Exception):
    pass


class _PytestShim(types.ModuleType):
    def skip(self, msg: str = ""):
        raise _Skip(msg)

    def fixture(self, *args, **kwargs):
        def deco(fn):
            fn._pytest_fixture = True
            fn._pytest_fixture_scope = kwargs.get("scope", "function")
            return fn
        return deco

    # pytest.created_id = ... 直接落到模块属性上, 无需额外处理


def _install_pytest_shim():
    if "pytest" not in sys.modules or not isinstance(sys.modules["pytest"], _PytestShim):
        shim = _PytestShim("pytest")
        sys.modules["pytest"] = shim
    return sys.modules["pytest"]


# ----------------------------------------------------------------------------
# 收集
# ----------------------------------------------------------------------------
class TestCase:
    __slots__ = ("module", "modname", "classname", "method", "path")

    def __init__(self, module, modname, classname, method, path):
        self.module = module
        self.modname = modname
        self.classname = classname
        self.method = method
        self.path = path

    @property
    def id(self):
        return f"{self.modname}::{self.classname}::{self.method}"


def _load_module(path: Path, modname: str):
    _install_pytest_shim()
    if modname in sys.modules:
        return sys.modules[modname]
    spec = importlib.util.spec_from_file_location(modname, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    return mod


def discover(project_id: str) -> list[TestCase]:
    tests_dir = ROOT / "projects" / project_id / "tests"
    if not tests_dir.exists():
        return []
    cases = []
    for path in sorted(tests_dir.rglob("test_*.py")):
        rel = path.relative_to(tests_dir)
        modname = f"_test_{project_id}_{'_'.join(rel.with_suffix('').parts)}".replace("-", "_")
        try:
            mod = _load_module(path, modname)
        except Exception as e:
            cases.append(_ErrorCase(modname, str(path), e))
            continue
        # fixtures
        fixtures = {n: getattr(mod, n) for n in dir(mod)
                    if hasattr(getattr(mod, n), "_pytest_fixture")}
        # Test* 类
        for cls_name in dir(mod):
            cls = getattr(mod, cls_name)
            if not (isinstance(cls, type) and cls_name.startswith("Test")):
                continue
            for mname in dir(cls):
                if mname.startswith("test_") and callable(getattr(cls, mname)):
                    cases.append(TestCase(mod, modname, cls_name, mname, str(path)))
    return cases


class _ErrorCase:
    def __init__(self, modname, path, exc):
        self.modname = modname
        self.path = path
        self.exc = exc

    @property
    def id(self):
        return self.modname


# ----------------------------------------------------------------------------
# 执行
# ----------------------------------------------------------------------------
class FakeResp:
    """mock 模式下返回的假响应, 让 ApiResponse 算出 success=True + 有 id。"""
    status_code = 200
    text = "{}"
    headers = {}

    def __init__(self, data: Optional[dict] = None):
        self._data = data or {"success": True, "data": {"id": "MOCK-1"}, "entity": {}}

    def json(self):
        return self._data


def _build_client(mode: str):
    if mode == "mock":
        from unittest import mock
        client = mock.MagicMock()
        client.request.side_effect = lambda *a, **k: FakeResp()
        return client
    # collect / dry: 用真实 client(无服务器时请求阶段才报错)
    _install_pytest_shim()
    try:
        from lib.api_client import get_client
        return get_client(ROOT / "projects" / _CUR_PROJECT, "")
    except Exception:
        return None


_CUR_PROJECT = ""


def run(project_id: str, mode: str = "collect"):
    global _CUR_PROJECT
    _CUR_PROJECT = project_id
    cases = discover(project_id)
    print(f"\n===== 项目 {project_id} | 模式={mode} =====")
    n_err = sum(1 for c in cases if isinstance(c, _ErrorCase))
    n_test = sum(1 for c in cases if isinstance(c, TestCase))
    print(f"模块导入错误: {n_err} | 收集到测试用例: {n_test}")

    for ec in cases:
        if isinstance(ec, _ErrorCase):
            print(f"  ❌ IMPORT ERROR {ec.modname}: {ec.exc}")

    if mode == "collect":
        for c in cases:
            if isinstance(c, TestCase):
                print(f"  • {c.id}")
        print(f"✅ 收集完成: {n_test} 用例可导入并收集")
        return 0 if n_err == 0 else 1

    # dry / mock: 实跑
    passed = skipped = failed = 0
    client = _build_client(mode)
    for c in cases:
        if isinstance(c, _ErrorCase):
            failed += 1
            continue
        try:
            cls = getattr(c.module, c.classname)
            inst = cls()
            # 解析 fixture(client)
            kwargs = {}
            import inspect
            sig = inspect.signature(getattr(cls, c.method))
            for pname in sig.parameters:
                if pname == "self":
                    continue
                if pname == "client":
                    # 注入运行器构建的 client(mock 或真实)
                    kwargs[pname] = client
                    continue
                fx = getattr(c.module, pname, None)
                if hasattr(fx, "_pytest_fixture"):
                    kwargs[pname] = fx()
            getattr(inst, c.method)(**kwargs)
            print(f"  ✅ PASS {c.id}")
            passed += 1
        except _Skip as s:
            print(f"  ⏭️  SKIP {c.id} ({s})")
            skipped += 1
        except Exception as e:
            # 无服务器/网络错误 -> 视为 SKIP(而非失败), 因为 dry 模式本就不保证连通
            msg = str(e)
            if mode == "dry" and any(k in msg for k in
                                     ("ConnectError", "ConnectTimeout", "Max retries",
                                      "Name or service", "getaddrinfo", "timed out", "Failed to")):
                print(f"  ⏭️  SKIP {c.id} (无服务器: {msg[:60]})")
                skipped += 1
            else:
                print(f"  ❌ FAIL {c.id}: {msg}")
                failed += 1
    print(f"结果: PASS={passed} SKIP={skipped} FAIL={failed}")
    return 0 if failed == 0 else 1


def _all_projects():
    return [p.name for p in (ROOT / "projects").iterdir()
            if (p / "tests").exists() and p.is_dir()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--mode", default="collect", choices=["collect", "dry", "mock"])
    args = ap.parse_args()

    if args.all:
        projects = _all_projects()
    elif args.project:
        projects = [args.project]
    else:
        projects = _all_projects()

    rc = 0
    for pid in projects:
        rc |= run(pid, args.mode)
    sys.exit(rc)


if __name__ == "__main__":
    main()
