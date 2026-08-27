"""
pytest_api_ai_test 插件安装配置
"""

from setuptools import setup, find_packages

setup(
    name="pytest-api-ai-test",
    version="0.1.0",
    description="Pytest 插件：自动发现并执行 API AI Test 生成的测试脚本",
    author="API AI Test Team",
    packages=find_packages(),
    python_requires=">=3.8",
    install_requires=[
        "pytest>=7.0.0",
    ],
    entry_points={
        "pytest11": [
            "api_ai_test = pytest_api_ai_test.plugin",
        ],
    },
    classifiers=[
        "Framework :: Pytest",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
    ],
)
