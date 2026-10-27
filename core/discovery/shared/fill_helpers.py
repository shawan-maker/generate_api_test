"""
core.discovery.shared.fill_helpers — Stage 1/2 共享的数据填充工具

设计原则：
1. 数据生成逻辑只定义一次，Stage 1/2 同时调用
2. 修改处理方法时两边同时生效
3. fill_rule 元数据与值生成绑定，保证一致性

提供：
- generate_fill_value(): 生成测试数据值 + 对应的 fill_rule 元数据
- apply_fill_rule(): 根据 fill_rule 元数据生成具体值（Stage 2 使用）
- evaluate_skip_rules(): 评估跳过规则（tree-select 等组件使用）
"""

import time
import random
import logging

LOG = logging.getLogger(__name__)


def generate_fill_value(field_type: str, input_type: str,
                        username: str = "test", label: str = "") -> tuple:
    """生成测试数据值和对应的 fill_rule 元数据。

    Stage 1 探测时使用此函数生成测试数据，同时返回 fill_rule 元数据
    供 Stage 2 回放时通过 apply_fill_rule() 重新生成新值。

    Args:
        field_type: 字段类型 ("input", "textarea", "select", "radio")
        input_type: HTML input type ("text", "number", "email", "tel", "password", "url")
        username: 用户名（用于名称字段前缀）
        label: 字段标签（用于关键词推断）

    Returns:
        tuple: (value, fill_rule)
            - value: 生成的具体值
            - fill_rule: {"rule": str, "params": dict} 格式的规则元数据
    """
    ts = str(int(time.time()))[-6:]
    label_lower = label.lower() if label else ""

    # 基于 inputType 生成（优先级最高）
    if input_type == "email":
        return f"at_{ts}@test.com", {
            "rule": "email_pattern", "params": {"domain": "test.com"}
        }
    if input_type == "tel":
        suffix = random.randint(10000000, 99999999)
        return f"138{suffix}", {
            "rule": "phone_pattern", "params": {"prefix": "138"}
        }
    if input_type == "password":
        suffix = random.randint(100000, 999999)
        return f"Test@{suffix}", {
            "rule": "password_pattern", "params": {"prefix": "Test@"}
        }
    if input_type == "number":
        v = random.randint(1, 100)
        return str(v), {
            "rule": "number_pattern", "params": {"min": 1, "max": 100}
        }
    if input_type == "url":
        return f"https://test-{ts}.example.com", {
            "rule": "url_pattern", "params": {"prefix": "https://test-"}
        }

    # 基于标签关键词推断（覆盖 inputType 缺失的情况）
    phone_keywords = ("手机", "电话", "phone", "mobile", "tel")
    if any(kw in label_lower for kw in phone_keywords):
        suffix = random.randint(10000000, 99999999)
        return f"138{suffix}", {
            "rule": "phone_pattern", "params": {"prefix": "138"}
        }
    email_keywords = ("邮箱", "email", "mail")
    if any(kw in label_lower for kw in email_keywords):
        return f"at_{ts}@test.com", {
            "rule": "email_pattern", "params": {"domain": "test.com"}
        }
    password_keywords = ("密码", "password", "pwd")
    if any(kw in label_lower for kw in password_keywords):
        suffix = random.randint(100000, 999999)
        return f"Test@{suffix}", {
            "rule": "password_pattern", "params": {"prefix": "Test@"}
        }

    # 基于字段类型
    if field_type == "textarea":
        return f"auto_test_{ts}", {
            "rule": "description_pattern", "params": {"prefix": "auto_test"}
        }

    # 基于标签关键词
    if any(kw in label_lower for kw in ["code", "编码", "编号"]):
        return f"code_{ts}", {
            "rule": "code_pattern", "params": {"prefix": "code"}
        }
    if any(kw in label_lower for kw in ["描述", "备注", "说明", "remark", "description", "memo"]):
        return f"auto_desc_{ts}", {
            "rule": "description_pattern", "params": {"prefix": "auto"}
        }

    # 默认文本
    return f"AT_{username}_{ts}", {
        "rule": "name_pattern", "params": {"prefix": f"AT_{username}"}
    }


def apply_fill_rule(rule: dict, timestamp: str = None) -> str:
    """根据 fill_rule 元数据生成具体的填充值。

    Stage 2 回放时调用，根据 Stage 1 记录的规则生成新值。

    Args:
        rule: {"rule": str, "params": dict} 格式的规则
        timestamp: 时间戳（可选，用于生成唯一值）

    Returns:
        生成的具体值
    """
    if not rule:
        return ""

    if not timestamp:
        timestamp = str(int(time.time()))[-6:]

    rule_type = rule.get("rule", "name_pattern")
    params = rule.get("params", {})

    if rule_type == "username_pattern":
        prefix = params.get("prefix", "autotest")
        return f"{prefix}{timestamp}"

    if rule_type == "email_pattern":
        domain = params.get("domain", "test.com")
        return f"at_{timestamp}@{domain}"

    if rule_type == "phone_pattern":
        prefix = params.get("prefix", "138")
        return f"{prefix}{random.randint(10000000, 99999999)}"

    if rule_type == "password_fixed":
        return params.get("value", "Test@123456")

    if rule_type == "password_pattern":
        prefix = params.get("prefix", "Test@")
        return f"{prefix}{random.randint(100000, 999999)}"

    if rule_type == "code_pattern":
        prefix = params.get("prefix", "code")
        return f"{prefix}_{timestamp}"

    if rule_type == "description_pattern":
        prefix = params.get("prefix", "auto")
        return f"{prefix}_desc_{timestamp}"

    if rule_type == "name_pattern":
        prefix = params.get("prefix", "test")
        return f"AT_{prefix}_{timestamp}"

    if rule_type == "number_pattern":
        min_val = params.get("min", 1)
        max_val = params.get("max", 100)
        num = int(timestamp) % (max_val - min_val + 1) + min_val
        return str(num)

    if rule_type == "url_pattern":
        prefix = params.get("prefix", "https://test-")
        return f"{prefix}{timestamp}.example.com"

    if rule_type == "fixed_value":
        return params.get("value", "")

    # IP 地址生成规则
    if rule_type == "ip_v4_pattern":
        base = params.get("base", "10.0.0")
        last_octet = params.get("last_octet", 1)
        last_val = (int(timestamp) % 254) + 1 if last_octet == 1 else last_octet
        return f"{base}.{last_val}"

    if rule_type == "ip_v4_range_pattern":
        base = params.get("base", "10.0.0")
        start = params.get("start", 1)
        end = params.get("end", 10)
        start_val = (int(timestamp) % 240) + 1 if start == 1 else start
        end_val = start_val + 9 if end == 10 else end
        return f"{base}.{start_val}-{base}.{end_val}"

    if rule_type == "ip_v6_pattern":
        prefix = params.get("prefix", "2001:db8")
        suffix = params.get("suffix", 1)
        suffix_val = (int(timestamp) % 65534) + 1 if suffix == 1 else suffix
        prefix_parts = prefix.split(":")
        while len(prefix_parts) < 7:
            prefix_parts.append("0000")
        prefix_parts.append(f"{suffix_val:04x}")
        return ":".join(prefix_parts)

    if rule_type == "ip_v6_range_pattern":
        prefix = params.get("prefix", "2001:db8")
        start = params.get("start", 1)
        end = params.get("end", 10)
        start_val = (int(timestamp) % 65520) + 1 if start == 1 else start
        end_val = start_val + 9 if end == 10 else end
        prefix_parts = prefix.split(":")
        while len(prefix_parts) < 7:
            prefix_parts.append("0000")
        start_ip = ":".join(prefix_parts + [f"{start_val:04x}"])
        end_ip = ":".join(prefix_parts + [f"{end_val:04x}"])
        return f"{start_ip}-{end_ip}"

    # 未知规则，返回默认值
    return f"test_{timestamp}"


def evaluate_skip_rules(node_info: dict, skip_rules: list,
                        current_value: str = "") -> bool:
    """评估节点是否应该被跳过。

    用于 tree-select 等组件的节点选择逻辑。

    Args:
        node_info: 节点信息 {"text": str, "is_checked": bool, "is_current": bool, ...}
        skip_rules: 跳过规则列表 [{"condition": str, "reason": str}, ...]
        current_value: 当前已选中的值（用于文本匹配跳过）

    Returns:
        True 表示应该跳过该节点，False 表示可以选择
    """
    for rule in skip_rules:
        condition = rule.get("condition", "")
        if condition == "is-checked" and node_info.get("is_checked"):
            return True
        if condition == "is-current" and node_info.get("is_current"):
            return True
        if condition == "is-disabled" and node_info.get("is_disabled"):
            return True
        if condition == "text_match_current" and current_value:
            if node_info.get("text", "") == current_value:
                return True
    return False


# tree-select 默认跳过规则
TREE_SELECT_SKIP_RULES = [
    {"condition": "is-checked", "reason": "avoid_already_selected"},
    {"condition": "is-current", "reason": "avoid_current_selection"},
    {"condition": "text_match_current", "reason": "avoid_same_value"},
]

# el-select 默认跳过规则
SELECT_SKIP_RULES = [
    {"condition": "is-disabled", "reason": "skip_disabled_options"},
]
