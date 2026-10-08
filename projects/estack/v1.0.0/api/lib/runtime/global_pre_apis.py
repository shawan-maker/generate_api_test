"""
global_pre_apis.py - 全局前置 API 执行器

职责：
- 执行项目级前置 API（如 /current-user, /policies）
- 提取字段并持久化到 .shared_context.json
- 提供 TTL 检查，避免过期上下文
"""

import json
import time
import logging
from pathlib import Path
from typing import Dict, Optional

LOG = logging.getLogger("global_pre_apis")


class GlobalPreApiExecutor:
    """执行项目级前置 API 并持久化到共享上下文文件"""

    def __init__(self, config_path: Path, base_url: str, session):
        """
        Args:
            config_path: pre_apis_config.json 路径
            base_url: 目标系统基础 URL
            session: requests.Session 对象（已认证）
        """
        self.config = json.loads(config_path.read_text(encoding="utf-8"))
        self.base_url = base_url
        self.session = session
        self.context = {}
        self.api_results = {}

    def execute_all(self) -> Dict:
        """按依赖顺序执行所有前置 API，返回上下文字典"""
        pre_apis = self.config.get("pre_apis", [])
        LOG.info(f"[GlobalPreAPI] 开始执行 {len(pre_apis)} 个前置 API")

        for pre_api in pre_apis:
            self._execute_one(pre_api)

        LOG.info(f"[GlobalPreAPI] 执行完成，提取 {len(self.context)} 个字段")
        return self.context

    def _execute_one(self, pre_api: Dict):
        """执行单个前置 API 并提取字段"""
        api_id = pre_api.get("id", "unknown")
        name = pre_api.get("name", api_id)
        method = pre_api.get("method", "GET").upper()
        pathname = pre_api.get("pathname", "")
        extracts = pre_api.get("extracts", [])

        url = self.base_url + pathname
        LOG.info(f"  [{api_id}] {method} {pathname}")

        try:
            if method == "GET":
                resp = self.session.get(url, timeout=10)
            else:
                body = pre_api.get("body_template", {})
                resp = self.session.request(method, url, json=body, timeout=10)

            if resp.status_code >= 300:
                LOG.warning(f"    ⚠️ HTTP {resp.status_code}")
                return

            resp_json = resp.json()
            extracted = {}

            for extract in extracts:
                field_name = extract.get("name", "")
                field_path = extract.get("path", "")
                value = self._extract_by_path(resp_json, field_path)

                if value is not None:
                    self.context[field_name] = value
                    extracted[field_name] = value
                    LOG.debug(f"    ✅ {field_name}: {value}")

            self.api_results[api_id] = {
                "status": resp.status_code,
                "extracted": extracted
            }

        except Exception as e:
            LOG.warning(f"    ❌ 执行失败: {e}")

    def _extract_by_path(self, obj, path: str):
        """
        从 JSON 对象中按路径提取值。
        支持：entity.tenantId, entity.list[0].id
        """
        if not path:
            return None

        parts = path.replace("[", ".").replace("]", "").split(".")
        current = obj

        for part in parts:
            if not part:
                continue

            if isinstance(current, dict):
                current = current.get(part)
            elif isinstance(current, list):
                try:
                    idx = int(part)
                    current = current[idx] if 0 <= idx < len(current) else None
                except (ValueError, IndexError):
                    return None
            else:
                return None

            if current is None:
                return None

        return current

    def save_context(self, output_path: Path):
        """写入 .shared_context.json，包含时间戳和 TTL"""
        data = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "ttl_seconds": 1800,
            "context": self.context,
            "api_results": self.api_results
        }
        output_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
        LOG.info(f"[GlobalPreAPI] 上下文已保存到 {output_path.name}")

    @staticmethod
    def load_context(context_path: Path) -> Dict:
        """
        加载共享上下文，检查 TTL。
        过期或不存在时返回空字典。
        """
        if not context_path.exists():
            return {}

        try:
            data = json.loads(context_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

        # 检查 TTL
        timestamp_str = data.get("timestamp", "")
        ttl = data.get("ttl_seconds", 1800)

        try:
            from datetime import datetime
            saved_at = datetime.strptime(timestamp_str, "%Y-%m-%dT%H:%M:%S")
            age_seconds = (datetime.now() - saved_at).total_seconds()

            if age_seconds > ttl:
                LOG.info(f"[GlobalPreAPI] 上下文已过期 (age={age_seconds:.0f}s, ttl={ttl}s)")
                return {}

        except Exception:
            pass

        context = data.get("context", {})
        if context:
            LOG.info(f"[GlobalPreAPI] 加载共享上下文: {list(context.keys())}")

        return context
