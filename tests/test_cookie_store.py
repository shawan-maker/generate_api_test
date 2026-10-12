"""
test_cookie_store.py — CookieStore 单元测试
"""

import json
import time
import tempfile
from pathlib import Path
import pytest

from lib.auth.cookie_store import CookieStore


class TestCookieStore:
    """CookieStore 测试"""

    def setup_method(self):
        """每个测试前创建临时目录"""
        self.temp_dir = Path(tempfile.mkdtemp())
        self.store = CookieStore()

    def teardown_method(self):
        """每个测试后清理临时目录"""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_save_and_load(self):
        """测试保存和加载 cookies"""
        cookies = [
            {"name": "session", "value": "abc123", "domain": ".example.com", "path": "/"},
            {"name": "token", "value": "xyz789", "domain": ".example.com", "path": "/"},
        ]

        self.store.save(self.temp_dir, cookies)
        loaded = self.store.load(self.temp_dir)

        assert len(loaded) == 2
        assert loaded[0]["name"] == "session"
        assert loaded[0]["value"] == "abc123"

    def test_load_missing_file(self):
        """测试加载不存在的文件"""
        loaded = self.store.load(self.temp_dir)
        assert loaded == []

    def test_is_fresh(self):
        """测试新鲜度检查"""
        cookies = [{"name": "session", "value": "test"}]
        self.store.save(self.temp_dir, cookies)

        # 刚保存的应该是新鲜的
        assert self.store.is_fresh(self.temp_dir, ttl_seconds=1800) is True

        # TTL 为 0 应该不新鲜
        assert self.store.is_fresh(self.temp_dir, ttl_seconds=0) is False

    def test_is_fresh_expired(self):
        """测试过期的 cookie"""
        # 手动创建过期的 meta
        config_dir = self.temp_dir / "output" / "config"
        config_dir.mkdir(parents=True)
        meta_path = config_dir / "cookies.meta.json"
        old_time = int(time.time() * 1000) - 2000000  # 2000 秒前
        meta_path.write_text(json.dumps({"savedAt": old_time, "count": 1}))

        # 应该不新鲜（TTL 1800 秒）
        assert self.store.is_fresh(self.temp_dir, ttl_seconds=1800) is False

    def test_filter_by_host(self):
        """测试域名过滤"""
        cookies = [
            {"name": "c1", "domain": ".example.com"},
            {"name": "c2", "domain": "api.example.com"},
            {"name": "c3", "domain": ".other.com"},
            {"name": "c4", "domain": "example.com"},
        ]

        # 过滤 example.com
        filtered = self.store.filter_by_host(cookies, "example.com")
        assert len(filtered) == 2  # c1, c4（c2 是子域，不匹配主域）

        # 过滤 api.example.com
        filtered = self.store.filter_by_host(cookies, "api.example.com")
        assert len(filtered) == 3  # c1, c2, c4（主域 cookie 匹配子域）

    def test_filter_by_host_with_port(self):
        """测试带端口的 host"""
        cookies = [
            {"name": "c1", "domain": "10.0.0.1"},
            {"name": "c2", "domain": "10.0.0.2"},
        ]

        # 带端口的 host 应该正确过滤
        filtered = self.store.filter_by_host(cookies, "10.0.0.1:8080")
        assert len(filtered) == 1
        assert filtered[0]["name"] == "c1"

    def test_localStorage(self):
        """测试 localStorage 保存和加载"""
        storage = {"key1": "value1", "key2": "value2"}

        self.store.save_localStorage(self.temp_dir, storage)
        loaded = self.store.load_localStorage(self.temp_dir)

        assert loaded == storage

    def test_sessionStorage(self):
        """测试 sessionStorage 保存和加载"""
        storage = {"token": "abc123"}

        self.store.save_sessionStorage(self.temp_dir, storage)
        loaded = self.store.load_sessionStorage(self.temp_dir)

        assert loaded == storage

    def test_domain_matches(self):
        """测试域名匹配"""
        # 完全匹配
        assert self.store._domain_matches("example.com", "example.com") is True

        # 子域匹配
        assert self.store._domain_matches(".example.com", "api.example.com") is True
        assert self.store._domain_matches("example.com", "api.example.com") is True

        # 不匹配
        assert self.store._domain_matches(".example.com", "other.com") is False
        assert self.store._domain_matches("example.com", "notexample.com") is False
