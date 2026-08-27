"""
kb_merger.py — 知识库合并器

职责：
- 合并来自不同模块的经验模式
- 冲突检测与解决
- 版本化与清理
- 置信度更新
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta

LOG = logging.getLogger("kb_merger")


class KBMerger:
    """知识库合并器：处理经验模式的合并、冲突检测、版本化"""

    def __init__(self, project_dir: Path):
        self.project_dir = project_dir
        self.selector_patterns_path = project_dir / "config" / "selector_patterns.json"
        self.operation_patterns_path = project_dir / "config" / "operation_patterns.json"

    def merge_selector_pattern(self, pattern_key: str, new_pattern: Dict) -> Dict:
        """合并选择器模式

        Args:
            pattern_key: 模式键（如 "table_row_selectors"）
            new_pattern: 新的模式数据

        Returns:
            合并结果
        """
        # 加载现有配置
        config = self._load_json(self.selector_patterns_path)
        if not config:
            config = self._create_empty_selector_config()

        patterns = config.get("patterns", {})
        existing = patterns.get(pattern_key, {})

        if not existing:
            # 新增模式
            patterns[pattern_key] = new_pattern
            result = {"action": "created", "pattern": pattern_key}
        else:
            # 合并模式
            merged = self._merge_single_pattern(existing, new_pattern)
            patterns[pattern_key] = merged
            result = {"action": "merged", "pattern": pattern_key}

        # 更新版本和元数据
        config["patterns"] = patterns
        config["version"] = self._increment_version(config.get("version", "0.0.0"))
        config["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        config["updated_by"] = new_pattern.get("module_source", ["unknown"])[0]

        # 添加 changelog
        changelog = config.get("changelog", [])
        changelog.append({
            "version": config["version"],
            "date": config["updated_at"],
            "module": config["updated_by"],
            "changes": f"{result['action']} pattern: {pattern_key}"
        })
        config["changelog"] = changelog

        # 保存
        self._save_json(self.selector_patterns_path, config)
        LOG.info(f"[KB合并] {result['action']} selector pattern: {pattern_key}")

        return result

    def merge_operation_pattern(self, operation_key: str, new_operation: Dict) -> Dict:
        """合并操作模式

        Args:
            operation_key: 操作键（如 "create_user_estack"）
            new_operation: 新的操作数据

        Returns:
            合并结果
        """
        # 加载现有配置
        config = self._load_json(self.operation_patterns_path)
        if not config:
            config = self._create_empty_operation_config()

        operations = config.get("operations", {})
        existing = operations.get(operation_key, {})

        if not existing:
            # 新增操作
            operations[operation_key] = new_operation
            result = {"action": "created", "operation": operation_key}
        else:
            # 合并操作
            merged = self._merge_single_operation(existing, new_operation)
            operations[operation_key] = merged
            result = {"action": "merged", "operation": operation_key}

        # 更新版本和元数据
        config["operations"] = operations
        config["version"] = self._increment_version(config.get("version", "0.0.0"))
        config["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        config["updated_by"] = new_operation.get("module_source", ["unknown"])[0]

        # 添加 changelog
        changelog = config.get("changelog", [])
        changelog.append({
            "version": config["version"],
            "date": config["updated_at"],
            "module": config["updated_by"],
            "changes": f"{result['action']} operation: {operation_key}"
        })
        config["changelog"] = changelog

        # 保存
        self._save_json(self.operation_patterns_path, config)
        LOG.info(f"[KB合并] {result['action']} operation pattern: {operation_key}")

        return result

    def detect_conflicts(self, pattern_key: str, new_selectors: List[Dict]) -> List[Dict]:
        """检测选择器冲突

        Args:
            pattern_key: 模式键
            new_selectors: 新的选择器列表

        Returns:
            冲突列表
        """
        config = self._load_json(self.selector_patterns_path)
        if not config:
            return []

        pattern = config.get("patterns", {}).get(pattern_key, {})
        if not pattern:
            return []

        existing_selectors = pattern.get("selectors", [])
        conflicts = []

        for new_sel in new_selectors:
            new_selector_str = new_sel.get("selector", "")
            new_name = new_sel.get("name", "")

            for existing_sel in existing_selectors:
                existing_selector_str = existing_sel.get("selector", "")
                existing_name = existing_sel.get("name", "")

                # 检测冲突条件：
                # 1. 相同 name 但不同 selector
                # 2. 相同 selector 但不同 name
                if new_name == existing_name and new_selector_str != existing_selector_str:
                    conflicts.append({
                        "type": "name_conflict",
                        "name": new_name,
                        "existing_selector": existing_selector_str,
                        "new_selector": new_selector_str,
                        "existing_source": existing_sel.get("module_source", []),
                        "new_source": new_sel.get("module_source", [])
                    })
                elif new_selector_str == existing_selector_str and new_name != existing_name:
                    conflicts.append({
                        "type": "selector_conflict",
                        "selector": new_selector_str,
                        "existing_name": existing_name,
                        "new_name": new_name,
                        "existing_source": existing_sel.get("module_source", []),
                        "new_source": new_sel.get("module_source", [])
                    })

        if conflicts:
            LOG.warning(f"[KB合并] 检测到 {len(conflicts)} 个冲突 in {pattern_key}")

        return conflicts

    def cleanup_deprecated_patterns(self, days_threshold: int = 30,
                                  min_usage: int = 3,
                                  min_confidence: float = 0.5) -> Dict:
        """清理过期和低质量模式

        Args:
            days_threshold: 过期天数阈值
            min_usage: 最小使用次数
            min_confidence: 最小置信度

        Returns:
            清理结果
        """
        result = {
            "selector_patterns": {"deprecated": [], "needs_verification": []},
            "operation_patterns": {"deprecated": [], "needs_verification": []}
        }

        # 清理 selector_patterns
        selector_config = self._load_json(self.selector_patterns_path)
        if selector_config:
            patterns = selector_config.get("patterns", {})
            cleanup_result = self._cleanup_patterns(patterns, days_threshold, min_usage, min_confidence)
            result["selector_patterns"] = cleanup_result
            selector_config["patterns"] = patterns
            self._save_json(self.selector_patterns_path, selector_config)

        # 清理 operation_patterns
        operation_config = self._load_json(self.operation_patterns_path)
        if operation_config:
            operations = operation_config.get("operations", {})
            cleanup_result = self._cleanup_patterns(operations, days_threshold, min_usage, min_confidence)
            result["operation_patterns"] = cleanup_result
            operation_config["operations"] = operations
            self._save_json(self.operation_patterns_path, operation_config)

        LOG.info(f"[KB清理] deprecated: {len(result['selector_patterns']['deprecated']) + len(result['operation_patterns']['deprecated'])}")
        return result

    def _merge_single_pattern(self, existing: Dict, new: Dict) -> Dict:
        """合并单个选择器模式"""
        merged = existing.copy()

        # 合并选择器列表
        existing_selectors = existing.get("selectors", [])
        new_selectors = new.get("selectors", [])

        # 按 name 去重
        selector_map = {s.get("name"): s for s in existing_selectors}
        for new_sel in new_selectors:
            name = new_sel.get("name")
            if name not in selector_map:
                selector_map[name] = new_sel

        merged["selectors"] = list(selector_map.values())

        # 合并模块来源
        existing_sources = set(existing.get("module_source", []))
        new_sources = set(new.get("module_source", []))
        merged["module_source"] = list(existing_sources | new_sources)

        # 更新置信度（取最大值）
        merged["confidence"] = max(existing.get("confidence", 0), new.get("confidence", 0))

        # 更新使用次数
        merged["usage_count"] = existing.get("usage_count", 0) + new.get("usage_count", 1)

        # 更新最后验证时间
        merged["last_verified"] = new.get("last_verified", existing.get("last_verified"))

        # 保持状态为 active
        merged["status"] = "active"

        return merged

    def _merge_single_operation(self, existing: Dict, new: Dict) -> Dict:
        """合并单个操作模式"""
        merged = existing.copy()

        # 合并模块来源
        existing_sources = set(existing.get("module_source", []))
        new_sources = set(new.get("module_source", []))
        merged["module_source"] = list(existing_sources | new_sources)

        # 更新成功/失败计数
        merged["success_count"] = existing.get("success_count", 0) + new.get("success_count", 0)
        merged["failure_count"] = existing.get("failure_count", 0) + new.get("failure_count", 0)

        # 更新置信度（基于成功率）
        total = merged["success_count"] + merged["failure_count"]
        if total > 0:
            merged["confidence"] = merged["success_count"] / total

        # 更新最后验证时间
        merged["last_verified"] = new.get("last_verified", existing.get("last_verified"))

        # 保持状态为 active
        merged["status"] = "active"

        return merged

    def _cleanup_patterns(self, patterns: Dict, days_threshold: int,
                          min_usage: int, min_confidence: float) -> Dict:
        """清理模式集合"""
        result = {"deprecated": [], "needs_verification": []}
        now = datetime.now()
        threshold_date = now - timedelta(days=days_threshold)

        for key, pattern in patterns.items():
            usage_count = pattern.get("usage_count", 0)
            confidence = pattern.get("confidence", 0)
            last_verified = pattern.get("last_verified")
            status = pattern.get("status", "active")

            # 检查是否需要标记为 deprecated
            if usage_count < min_usage and confidence < min_confidence:
                if status != "deprecated":
                    pattern["status"] = "deprecated"
                    result["deprecated"].append(key)
                    LOG.info(f"[KB清理] 标记 deprecated: {key} (usage={usage_count}, confidence={confidence})")

            # 检查是否需要验证
            elif last_verified:
                try:
                    verified_date = datetime.strptime(last_verified, "%Y-%m-%d")
                    if verified_date < threshold_date and status == "active":
                        pattern["status"] = "needs_verification"
                        result["needs_verification"].append(key)
                        LOG.info(f"[KB清理] 标记 needs_verification: {key} (last_verified={last_verified})")
                except ValueError:
                    # 日期格式错误，忽略
                    pass

        return result

    def _increment_version(self, version: str) -> str:
        """递增版本号"""
        try:
            parts = version.split(".")
            if len(parts) != 3:
                return "1.0.0"

            major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])
            patch += 1

            return f"{major}.{minor}.{patch}"
        except Exception:
            return "1.0.0"

    def _load_json(self, path: Path) -> Optional[Dict]:
        """加载 JSON 文件"""
        if not path.exists():
            return None

        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            LOG.error(f"[KB合并] 加载失败 {path}: {e}")
            return None

    def _save_json(self, path: Path, data: Dict):
        """保存 JSON 文件"""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def _create_empty_selector_config(self) -> Dict:
        """创建空的 selector_patterns 配置"""
        return {
            "version": "1.0.0",
            "updated_by": "system",
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "changelog": [],
            "patterns": {}
        }

    def _create_empty_operation_config(self) -> Dict:
        """创建空的 operation_patterns 配置"""
        return {
            "version": "1.0.0",
            "updated_by": "system",
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "changelog": [],
            "operations": {}
        }
