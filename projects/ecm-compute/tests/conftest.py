"""
pytest conftest — 自动配置 client。鉴权 token/cookie 由 auth 注入(运行前准备)。
"""
import pytest
from lib.api_client import get_client


@pytest.fixture(scope="session")
def client():
    """session 级 client。需先准备好鉴权上下文(见 lib/auth.py)。"""
    from pathlib import Path
    proj_dir = Path(__file__).resolve().parents[2]
    return get_client(proj_dir)
