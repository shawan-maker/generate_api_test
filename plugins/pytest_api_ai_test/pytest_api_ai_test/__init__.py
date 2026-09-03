"""
pytest_api_ai_test - Pytest 插件，自动发现 scripts/ 下的 API 测试脚本并转为 pytest 用例

功能：
- 自动扫描 projects/*/scripts/**/*.py 脚本
- 将每个脚本转换为独立的 pytest 测试用例
- 支持 JUnit XML 输出（通过 pytest --junit-xml）
- 支持并行执行（通过 pytest-xdist）

用法：
  pytest --api-ai-scripts=projects/ecm-compute/scripts
  pytest --api-ai-scripts=projects/*/scripts --junit-xml=report.xml
"""

from .plugin import pytest_configure, pytest_collect_file

__version__ = "0.1.0"
__all__ = ["pytest_configure", "pytest_collect_file"]
