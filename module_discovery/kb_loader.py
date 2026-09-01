"""
kb_loader.py - 知识库加载器

加载 probe_knowledge.json，展开 XPath 模板中的占位符。
支持 Element UI 和 Ant Design 两种框架变体。
"""

import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

from . import const

LOG = logging.getLogger(__name__)


class ProbeKB:
    """探针知识库加载器 + 模板展开器"""

    def __init__(self, kb_path: Optional[Path] = None):
        """初始化知识库加载器

        Args:
            kb_path: 知识库文件路径，默认按优先级查找：
                     1. module_discovery/kb/probe_knowledge.json
                     2. docs/design/probe_knowledge.json
        """
        if kb_path is None:
            # 默认路径列表（按优先级）
            candidates = [
                Path(__file__).parent / "kb" / "probe_knowledge.json",
                Path(__file__).parent.parent / "docs" / "design" / "probe_knowledge.json",
            ]
            for p in candidates:
                if p.exists():
                    kb_path = p
                    break

        self.kb_path = kb_path
        self._kb_data: Optional[Dict] = None
        self._load_kb()

    def _load_kb(self) -> None:
        """加载知识库 JSON 文件"""
        if self.kb_path and self.kb_path.exists():
            try:
                with open(self.kb_path, "r", encoding="utf-8") as f:
                    self._kb_data = json.load(f)
                LOG.debug(f"知识库已加载: {self.kb_path}")
            except Exception as e:
                LOG.warning(f"加载知识库失败 {self.kb_path}: {e}")
                self._kb_data = {}
        else:
            LOG.debug("知识库文件不存在，使用空知识库")
            self._kb_data = {}

    def get_patterns(
        self, category: str, framework: str = "element-ui"
    ) -> List[str]:
        """获取指定类别的 XPath 模板列表

        Args:
            category: 模板类别（如 "button", "search-button", "dropdown-menu"）
            framework: UI 框架 ("element-ui" 或 "ant-design")

        Returns:
            XPath 模板字符串列表
        """
        if not self._kb_data:
            return []

        # 按层级查找：single_step -> composite -> multi_step
        for section in ["single_step", "composite", "multi_step"]:
            categories = self._kb_data.get(section, {}).get("categories", {})
            if category in categories:
                cat_data = categories[category]

                # 检查是否有 framework_variants
                if framework == "ant-design" and "framework_variants" in cat_data:
                    variants = cat_data["framework_variants"]
                    if "ant-design" in variants:
                        ant_data = variants["ant-design"]
                        # 多步操作：提取所有 step 的 patterns
                        if "steps" in ant_data:
                            patterns = []
                            for step_data in ant_data["steps"].values():
                                patterns.extend(step_data.get("patterns", []))
                            return patterns
                        # 单步操作：直接返回 patterns
                        return ant_data.get("patterns", [])

                # 默认框架或无变体
                if "steps" in cat_data:
                    # 多步操作：提取所有 step 的 patterns
                    patterns = []
                    for step_data in cat_data["steps"].values():
                        patterns.extend(step_data.get("patterns", []))
                    return patterns
                return cat_data.get("patterns", [])

        return []

    def expand(
        self, category: str, label: str, framework: str = "element-ui"
    ) -> List[str]:
        """展开 XPath 模板中的占位符

        支持的占位符：
        - {label}: 原始标签文本
        - {chars_all}: 每个字符的 contains() 用 and 连接
        - {char1}, {char2}: 前两个字符（用于双字按钮容错）

        Args:
            category: 模板类别
            label: 按钮/元素标签文本
            framework: UI 框架

        Returns:
            展开后的 XPath 字符串列表

        Example:
            expand("button", "确定") -> [
                "//button[contains(.,'确') and contains(.,'定')]",
                "//button[contains(.,'确定')]",
                ...
            ]
        """
        patterns = self.get_patterns(category, framework)
        if not patterns:
            return []

        # 计算占位符值
        chars = list(label)
        chars_all = " and ".join(f"contains(.,'{c}')" for c in chars)
        char1 = chars[0] if len(chars) > 0 else ""
        char2 = chars[1] if len(chars) > 1 else ""

        # 展开每个模板
        expanded = []
        for pattern in patterns:
            try:
                xpath = pattern.replace("{label}", label)
                xpath = xpath.replace("{chars_all}", chars_all)
                xpath = xpath.replace("{char1}", char1)
                xpath = xpath.replace("{char2}", char2)
                expanded.append(xpath)
            except Exception as e:
                LOG.debug(f"展开模板失败: {pattern}, 错误: {e}")

        return expanded

    def _find_category(self, category: str, framework: str = "element-ui") -> dict:
        """在 single_step/composite/multi_step 中查找 category（内部方法）。

        Returns:
            类别数据字典，未找到返回空 dict
        """
        if not self._kb_data:
            return {}
        for section in ["single_step", "composite", "multi_step"]:
            categories = self._kb_data.get(section, {}).get("categories", {})
            if category in categories:
                return categories[category]
        return {}

    def get_steps(self, category: str, framework: str = "element-ui") -> dict:
        """获取 multi_step 类别的步骤结构（不展平）。

        Args:
            category: 模板类别（如 "el-select", "el-cascader", "date-picker"）
            framework: UI 框架

        Returns:
            {step_name: {"desc": str, "patterns": [str]}} 字典
            如 {"expand": {"desc": "点击展开", "patterns": [...]},
                "fill":   {"desc": "输入搜索", "patterns": [...]}, ...}
        """
        cat_data = self._find_category(category, framework)
        if not cat_data:
            return {}

        # 检查 framework_variants
        if framework == "ant-design" and "framework_variants" in cat_data:
            variant = cat_data["framework_variants"].get("ant-design", {})
            if "steps" in variant:
                return variant["steps"]

        return cat_data.get("steps", {})

    def get_conditional_branch(self, category: str, framework: str = "element-ui") -> dict:
        """获取条件分支配置。

        Returns:
            {"condition_locator": str, "if_visible": str,
             "if_not_visible": str, "timeout": int}
            如果无 conditional_branch 则返回空 dict
        """
        cat_data = self._find_category(category, framework)
        if not cat_data:
            return {}
        return cat_data.get("conditional_branch", {})

    def expand_step(self, pattern: str, label: str = "", option_text: str = "",
                    field_text: str = "", last_text: str = "",
                    idx: int = 0, framework: str = None,
                    apply_hidden: bool = False, **kwargs) -> str:
        """展开单个步骤的 XPath 模板，支持所有占位符。

        支持的占位符：
        - {label}, {chars_all}, {char1}, {char2}  （已有）
        - {option_text}    下拉选项/级联选项文本
        - {field_text}     表单字段标签文本（form-checkbox）
        - {last_text}      级联最后一级文本
        - {idx}            行索引
        - **kwargs         其他自定义占位符

        Args:
            apply_hidden: 是否追加隐藏过滤器（交互操作时传 True）
            framework: UI 框架（决定使用哪套过滤器）
        """
        chars = list(label) if label else []
        chars_all = " and ".join(f"contains(.,'{c}')" for c in chars) if chars else ""

        result = pattern
        result = result.replace("{label}", label)
        result = result.replace("{chars_all}", chars_all)
        result = result.replace("{char1}", chars[0] if chars else "")
        result = result.replace("{char2}", chars[1] if len(chars) > 1 else "")
        result = result.replace("{option_text}", option_text)
        result = result.replace("{field_text}", field_text)
        result = result.replace("{last_text}", last_text)
        result = result.replace("{idx}", str(idx))

        for key, val in kwargs.items():
            result = result.replace(f"{{{key}}}", str(val))

        # 追加隐藏过滤器
        if apply_hidden and framework:
            hf = const.HIDDEN_FILTERS.get(framework, const.HIDDEN_FILTERS['_universal'])
            result = _append_hidden_filter(result, hf)

        return result

    def get_fallback_strategies(self) -> list:
        """获取 KB 兜底策略列表。

        Returns:
            [{"name": str, "description": str, "patterns": [str], "trusted": bool}]
        """
        return self._kb_data.get("fallback_strategies", {}).get("strategies", [])

    def detect_framework_sync(self, html: str) -> str:
        """通过 HTML 内容检测 UI 框架（同步版本）

        Args:
            html: 页面 HTML 内容

        Returns:
            "element-ui" 或 "ant-design"
        """
        # 简单的关键词检测
        if any(kw in html for kw in ["ant-layout", "ant-btn", "ant-table"]):
            return "ant-design"
        return "element-ui"


# ============================================================
# 模块级工具函数 — Locator 增强
# ============================================================

def _append_hidden_filter(xpath: str, hidden_filter: str) -> str:
    """在 XPath 最后一个节点追加隐藏过滤谓词。

    三种模式：
    A. 有谓词:   //button[contains(.,'确定')]       → //button[contains(.,'确定')][FILTER]
    B. 无谓词:   //button                            → //button[FILTER]
    C. 括号包裹: (//button[contains(.,'确定')])[1]   → (//button[contains(.,'确定')][FILTER])[1]
    """
    if not xpath or not xpath.startswith(('//', '(')):
        return xpath

    # C: 括号包裹模式 (XPath)[N] → 在 ) 之前插入 [FILTER]
    m = re.match(r'^(.*)\)(\[\d+\])$', xpath)
    if m and xpath.startswith('('):
        inner = m.group(1)      # (//button[contains(.,'OK')]
        suffix = m.group(2)     # [1]
        return f"{inner}[{hidden_filter}]){suffix}"

    # A/B: 普通模式 → 直接追加
    return xpath + f"[{hidden_filter}]"


def apply_overlay_scope(xpath: str, overlay_prefix: str) -> str:
    """在 XPath 前追加覆盖层容器定位。

    模式 A: 普通 XPath
        //button[contains(.,'确定')] → //div[...]//button[contains(.,'确定')]

    模式 B: 括号包裹 (XPath)[N]
        (//button[...])[1] → (//div[...]//button[...])[1]
    """
    if not xpath or not xpath.startswith(('//', '(')):
        return xpath

    # B: 括号包裹模式 → 在括号内第一个 // 前插入前缀
    if xpath.startswith('('):
        # 找到括号内的 XPath（去掉开头的 (）
        inner = xpath[1:]
        if inner.startswith('//'):
            return f"({overlay_prefix}{inner}"
        return xpath

    # A: 普通模式 → 直接插入前缀
    body = xpath[2:]  # 去掉开头的 //
    return f"{overlay_prefix}//{body}"


def detect_active_overlay_js(framework: str) -> str:
    """生成检测活跃覆盖层的 JS 脚本。

    Returns:
        JS 字符串，返回覆盖层类型名或空串
    """
    if framework == "ant-design":
        return """() => {
            if (document.querySelector('.ant-modal-wrap:not([style*="display: none"]) .ant-modal-content')) return 'ant-modal';
            if (document.querySelector('.ant-drawer-open .ant-drawer-content')) return 'ant-drawer';
            return '';
        }"""
    else:
        return """() => {
            if (document.querySelector('.el-message-box__wrapper:not([style*="display: none"])')) return 'el-message-box';
            if (document.querySelector('.el-drawer:not([style*="display: none"])')) return 'el-drawer';
            const dialogs = document.querySelectorAll('.el-dialog__wrapper');
            for (const d of dialogs) {
                if (d.style.display !== 'none' && d.querySelector('.el-dialog')) return 'el-dialog';
            }
            return '';
        }"""


def get_overlay_prefix(overlay_type: str, framework: str) -> str:
    """根据覆盖层类型名获取对应的 XPath 前缀。

    Args:
        overlay_type: 如 'el-dialog', 'el-drawer', 'ant-modal'
        framework: UI 框架

    Returns:
        XPath 前缀字符串，未匹配时返回空串
    """
    if not overlay_type:
        return ""
    selectors = const.OVERLAY_SELECTORS.get(framework, [])
    for name, prefix in selectors:
        if name == overlay_type:
            return prefix
    return ""


# 全局实例（延迟加载）
_kb_instance: Optional[ProbeKB] = None


def get_kb() -> ProbeKB:
    """获取全局知识库实例"""
    global _kb_instance
    if _kb_instance is None:
        _kb_instance = ProbeKB()
    return _kb_instance
