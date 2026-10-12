"""
cookie_store.py — Cookie 存储管理

所有 cookie 读写的唯一接口。管理 cookies.json、meta.json、localStorage、sessionStorage。
沿用 login-kit 的设计：cookie 优先 → 新鲜度缓存 → 域名过滤。
"""

import json
import time
import logging
from pathlib import Path
from typing import List, Dict, Optional

LOG = logging.getLogger("cookie_store")


class CookieStore:
    """Cookie 存储管理 — 统一读写接口

    文件结构：
    - cookies.json: Playwright 原生 cookie 数组
    - cookies.meta.json: 元数据 {savedAt, count}
    - cookies.localStorage.json: localStorage 键值对
    - cookies.sessionStorage.json: sessionStorage 键值对
    """

    def __init__(self):
        self.cookie_file = "cookies.json"
        self.meta_file = "cookies.meta.json"
        self.localstorage_file = "cookies.localStorage.json"
        self.sessionstorage_file = "cookies.sessionStorage.json"

    def load(self, project_dir: Path) -> List[Dict]:
        """加载 cookies

        Args:
            project_dir: 项目目录（workspace/<project>/）

        Returns:
            list: Playwright 格式的 cookie 数组，不存在返回 []
        """
        cookie_path = project_dir / "output" / "config" / self.cookie_file
        if not cookie_path.exists():
            return []
        try:
            data = json.loads(cookie_path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception as e:
            LOG.warning(f"Cookie 加载失败: {e}")
            return []

    def save(self, project_dir: Path, cookies: List[Dict]):
        """保存 cookies + meta.json

        Args:
            project_dir: 项目目录
            cookies: Playwright 格式的 cookie 数组
        """
        config_dir = project_dir / "output" / "config"
        config_dir.mkdir(parents=True, exist_ok=True)

        cookie_path = config_dir / self.cookie_file
        cookie_path.write_text(
            json.dumps(cookies, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

        meta_path = config_dir / self.meta_file
        meta = {
            "savedAt": int(time.time() * 1000),
            "count": len(cookies)
        }
        meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        LOG.info(f"✅ Cookie 已保存: {cookie_path} ({len(cookies)} 条)")

    def is_fresh(self, project_dir: Path, ttl_seconds: int) -> bool:
        """检查 cookie 新鲜度（在 TTL 内视为有效）

        Args:
            project_dir: 项目目录
            ttl_seconds: TTL 秒数

        Returns:
            True 表示 cookie 在 TTL 内
        """
        meta_path = project_dir / "output" / "config" / self.meta_file
        if not meta_path.exists():
            return False

        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            saved_at = meta.get("savedAt", 0)
            age_ms = int(time.time() * 1000) - saved_at
            return age_ms < (ttl_seconds * 1000)
        except Exception as e:
            LOG.warning(f"Meta 读取失败: {e}")
            return False

    def filter_by_host(self, cookies: List[Dict], host: str) -> List[Dict]:
        """域名过滤（RFC 6265）— 只保留匹配当前 host 的 cookie

        防止跨地址污染（如从旧 IP 迁移到新 IP 后仍用旧 cookie）。

        Args:
            cookies: cookie 数组
            host: 当前 host（不含端口）

        Returns:
            过滤后的 cookie 数组
        """
        host = host.split(":")[0]  # 去除端口
        return [c for c in cookies if self._domain_matches(c.get("domain", ""), host)]

    def _domain_matches(self, cookie_domain: str, host: str) -> bool:
        """检查 cookie domain 是否匹配 host（RFC 6265）

        Args:
            cookie_domain: cookie 的 domain 字段
            host: 当前 host

        Returns:
            True 表示匹配
        """
        cookie_domain = cookie_domain.lstrip(".")
        if cookie_domain == host:
            return True
        if host.endswith("." + cookie_domain):
            return True
        return False

    # ========== Storage 持久化（localStorage / sessionStorage）==========

    def save_localStorage(self, project_dir: Path, storage: Dict[str, str]):
        """保存 localStorage

        Args:
            project_dir: 项目目录
            storage: localStorage 键值对
        """
        if not storage:
            return
        config_dir = project_dir / "output" / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        path = config_dir / self.localstorage_file
        path.write_text(
            json.dumps(storage, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        LOG.info(f"✅ localStorage 已保存: {len(storage)} 项")

    def load_localStorage(self, project_dir: Path) -> Dict[str, str]:
        """加载 localStorage

        Args:
            project_dir: 项目目录

        Returns:
            dict: localStorage 键值对
        """
        path = project_dir / "output" / "config" / self.localstorage_file
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def save_sessionStorage(self, project_dir: Path, storage: Dict[str, str]):
        """保存 sessionStorage

        Args:
            project_dir: 项目目录
            storage: sessionStorage 键值对
        """
        if not storage:
            return
        config_dir = project_dir / "output" / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        path = config_dir / self.sessionstorage_file
        path.write_text(
            json.dumps(storage, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        LOG.info(f"✅ sessionStorage 已保存: {len(storage)} 项")

    def load_sessionStorage(self, project_dir: Path) -> Dict[str, str]:
        """加载 sessionStorage

        Args:
            project_dir: 项目目录

        Returns:
            dict: sessionStorage 键值对
        """
        path = project_dir / "output" / "config" / self.sessionstorage_file
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
