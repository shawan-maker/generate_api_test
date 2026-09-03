"""
pytest_api_ai_test plugin - 核心插件实现

自动发现 scripts/ 下的 API 测试脚本并转为 pytest 用例
"""

import pytest
import sys
from pathlib import Path
from typing import Optional


def pytest_addoption(parser):
    """添加命令行选项"""
    group = parser.getgroup('api_ai_test', 'API AI Test 插件选项')
    group.addoption(
        '--api-ai-flows',
        action='store',
        default=None,
        metavar='PATH',
        help='指定 flows 目录路径（支持通配符，如 projects/*/flows）'
    )
    group.addoption(
        '--api-ai-project',
        action='store',
        default=None,
        metavar='PROJECT',
        help='指定项目名称（如 ecm-compute），自动查找对应 flows 目录'
    )


def pytest_configure(config):
    """插件配置"""
    config.addinivalue_line(
        "markers", "api_ai_test: mark test as API AI generated test"
    )


def pytest_collect_file(parent, file_path: Path) -> Optional['ApiAiTestFile']:
    """收集测试文件"""
    # 检查是否在 flows 目录下
    flows_dirs = _get_flows_dirs(parent.config)

    if not flows_dirs:
        return None

    # 检查文件是否在 flows 目录下且为 Python 测试脚本
    for flows_dir in flows_dirs:
        try:
            flows_path = Path(flows_dir).resolve()
            file_resolved = file_path.resolve()

            # 检查是否在 flows 目录下
            if flows_path in file_resolved.parents or flows_path == file_resolved.parent:
                # 检查是否为 API 测试脚本
                if file_path.suffix == '.py' and 'API测试' in file_path.stem:
                    return ApiAiTestFile.from_parent(parent, path=file_path)
        except Exception:
            continue

    return None


def _get_flows_dirs(config) -> list:
    """获取 flows 目录列表"""
    flows_option = config.getoption('--api-ai-flows', default=None)
    project_option = config.getoption('--api-ai-project', default=None)

    dirs = []

    # 优先使用 --api-ai-flows
    if flows_option:
        import glob
        matched = glob.glob(flows_option)
        dirs.extend([Path(p).resolve() for p in matched if Path(p).is_dir()])

    # 其次使用 --api-ai-project
    elif project_option:
        project_dir = Path.cwd() / 'projects' / project_option / 'flows'
        if project_dir.exists():
            dirs.append(project_dir)

    return dirs


class ApiAiTestFile(pytest.File):
    """API AI 测试文件收集器"""

    def collect(self):
        """收集测试用例"""
        # 加载测试模块
        module_path = self.path
        module_name = module_path.stem

        # 导入模块
        spec = __import__('importlib').util.spec_from_file_location(
            module_name, module_path
        )
        module = __import__('importlib').util.module_from_spec(spec)

        try:
            spec.loader.exec_module(module)
        except Exception as e:
            # 如果模块加载失败，生成一个失败的测试用例
            yield ApiAiTestItem.from_parent(
                self,
                name=f"{module_name}_load_error",
                module=None,
                load_error=str(e)
            )
            return

        # 查找 main 函数
        if hasattr(module, 'main'):
            yield ApiAiTestItem.from_parent(
                self,
                name=module_name,
                module=module,
                load_error=None
            )


class ApiAiTestItem(pytest.Item):
    """API AI 测试项"""

    def __init__(self, name, parent, module, load_error):
        super().__init__(name, parent)
        self.module = module
        self.load_error = load_error
        self.add_marker('api_ai_test')

    def runtest(self):
        """执行测试"""
        if self.load_error:
            pytest.fail(f"模块加载失败: {self.load_error}")

        if self.module is None:
            pytest.fail("模块为空")

        if not hasattr(self.module, 'main'):
            pytest.fail("模块中没有找到 main 函数")

        # 执行 main 函数
        try:
            self.module.main()
        except SystemExit as e:
            if e.code != 0:
                pytest.fail(f"测试脚本退出码: {e.code}")
        except AssertionError as e:
            pytest.fail(str(e))
        except Exception as e:
            pytest.fail(f"测试执行失败: {e}")

    def repr_failure(self, excinfo):
        """失败信息表示"""
        return f"API AI 测试失败: {excinfo.value}"

    def reportinfo(self):
        """报告信息"""
        return self.path, None, f"api_ai_test: {self.name}"
