"""
orphan_cleaner.py — 资源命名前缀 + 孤儿清理器。
对应方案设计 §11.3。
"""
import json
import re
import httpx
from pathlib import Path
from typing import Optional


class OrphanCleaner:
    """
    清理残留的测试资源(由 P0 巡游/API 调用创建但未清理的孤儿)。
    所有自动创建的资源统一前缀 `AT_`，便于识别和清理。
    """

    def __init__(self, base_url: str, api_base: str, token: str = "",
                 prefix: str = "AT_", fixed_headers: dict = None):
        self.base_url = base_url.rstrip("/")
        self.api_base = api_base.rstrip("/")
        self.prefix = prefix
        headers = dict(fixed_headers or {})
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.client = httpx.Client(base_url=self.base_url + self.api_base,
                                   headers=headers, verify=False, timeout=30)

    def scan_list_response(self, response_body: dict, resource_label: str = "name") -> list:
        """
        扫描 list 接口的响应, 找出名字前缀匹配的资源 ID。
        entity/list/rows 等数组字段中匹配 resource_label 字段。
        """
        orphans = []
        data = response_body or {}
        if not isinstance(data, dict):
            return orphans
        # estack 常见响应结构 {entity: [...]} 或 {data: [...]} 或 {rows: [...]}
        items = (data.get("entity") or data.get("data") or data.get("rows") or [])
        if isinstance(items, dict):
            # 也可能是 entity: {list: [...]}
            items = items.get("list") or items.get("records") or list(items.values())
        if not isinstance(items, list):
            return orphans
        for item in items:
            if not isinstance(item, dict):
                continue
            name = item.get(resource_label) or item.get("name") or item.get("resourceName") or ""
            rid = item.get("id") or item.get("resourceId") or ""
            if isinstance(name, str) and name.startswith(self.prefix) and rid:
                orphans.append({"id": rid, "name": name, "source": item})
        return orphans

    def dry_run(self, list_endpoints: list, resource_label: str = "name") -> dict:
        """
        dry-run 扫描: 不执行删除, 只报告哪些资源会被清理。
        list_endpoints: [(method, path), ...]
        """
        report = {"orphans": [], "errors": []}
        for method, path in list_endpoints:
            try:
                resp = self.client.request(method, path)
                if resp.status_code >= 300:
                    report["errors"].append(f"{method} {path}: HTTP {resp.status_code}")
                    continue
                try:
                    body = resp.json()
                except Exception:
                    continue
                found = self.scan_list_response(body, resource_label)
                if found:
                    report["orphans"].extend(found)
            except Exception as e:
                report["errors"].append(f"{method} {path}: {e}")
        report["count"] = len(report["orphans"])
        return report

    def clean_orphans(self, orphans: list, delete_endpoint: tuple = None,
                      id_field: str = "id") -> dict:
        """
        批量清理孤儿资源。
        delete_endpoint: (method, path_template) 如 ("DELETE", "/users/{id}")
        id_field: 路径参数名, 默认 "id"
        """
        if not delete_endpoint:
            return {"deleted": 0, "errors": ["no delete endpoint provided"]}
        method, tmpl = delete_endpoint
        results = {"deleted": 0, "skipped": 0, "errors": []}
        for orphan in orphans:
            rid = orphan.get("id")
            if not rid:
                results["skipped"] += 1
                continue
            path = tmpl.replace("{id}", rid).replace("{resourceId}", rid)
            try:
                resp = self.client.request(method, path)
                if resp.status_code < 300:
                    results["deleted"] += 1
                else:
                    results["errors"].append(f"{method} {path}: HTTP {resp.status_code}")
            except Exception as e:
                results["errors"].append(f"{method} {path}: {e}")
        return results
