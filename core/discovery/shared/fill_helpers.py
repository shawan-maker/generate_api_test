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

# ★ 批六修复8：数字字段 label 关键词（小写匹配；覆盖 inputType 缺失的兜底层——
#   el-input-number 容器判据已在扫描侧修为 inputType='number' 优先生效）。
#   单源：generate_fill_value / _infer_fill_rule 共用。
_NUMBER_LABEL_KEYWORDS = (
    "数量", "cpu", "内存", "容量", "规格", "盘", "核心", "大小",
    "count", "memory", "ram", "disk", "size", "spec",
)
# 数字词命中排除（「磁盘名称」「云盘名」类含盘的名称字段仍是文本——名/称
# 在 label 中即视为名称字段，词表兜底让位 name_pattern，防误伤）
_NUMBER_LABEL_EXCLUDES = ("名称", "名字", "别名", "标签", "name", "title", "label")
# ★ 批六修复8：登录名/用户名类字段词表（→ username_pattern，prefix 维持 test
#   与原 name_pattern 默认值等值——r9 回归面最小化，仅规则名更准确）
_USERNAME_LABEL_KEYWORDS = ("登录名", "用户名", "账号名", "username", "login name")


def _is_numeric_text(text: str) -> bool:
    """纯数字文本判定（el-input-number 默认值合法性，批六修复8）。"""
    try:
        s = str(text).strip()
        return bool(s) and s.replace(".", "", 1).isdigit()
    except Exception:
        return False


def _is_number_label(label_lower: str) -> bool:
    """数字词 label 判定（命中数字词且非名称类字段，批六修复8 单源）。"""
    if not any(kw in label_lower for kw in _NUMBER_LABEL_KEYWORDS):
        return False
    return not any(ex in label_lower for ex in _NUMBER_LABEL_EXCLUDES)


def generate_fill_value(field_type: str, input_type: str,
                        username: str = "test", label: str = "",
                        placeholder: str = "", current_value: str = "") -> tuple:
    """生成测试数据值和对应的 fill_rule 元数据。

    Stage 1 探测时使用此函数生成测试数据，同时返回 fill_rule 元数据
    供 Stage 2 回放时通过 apply_fill_rule() 重新生成新值。

    Args:
        field_type: 字段类型 ("input", "textarea", "select", "radio")
        input_type: HTML input type ("text", "number", "email", "tel", "password", "url")
        username: 用户名（用于名称字段前缀）
        label: 字段标签（用于关键词推断）
        placeholder: 输入框 placeholder（批五 F3：引号包裹确认词提取，
            如「请输入 "DELETE" 确认删除」→ 填 DELETE——危险操作确认输入框
            的值生成判据，优先级最高）

    Returns:
        tuple: (value, fill_rule)
            - value: 生成的具体值
            - fill_rule: {"rule": str, "params": dict} 格式的规则元数据
    """
    # ★ 批五 F3：placeholder 确认词提取（优先级最高——先于 input_type 分支）。
    #   判据即「placeholder 含引号包裹词」（const 单源正则），不依赖 label
    #   含「确认」的启发式。实证根因：EIS 删除弹窗「删除确认」输入框落
    #   name_pattern 被填随机值（r7b test500861），placeholder 明明写着
    #   「请输入 "DELETE" 确认删除」。fill_rule 落 placeholder_hint，
    #   Stage 2 回放经 apply_fill_rule 的 placeholder_confirm 分支从同弹窗
    #   placeholder 重建同一词（三阶段一致）。
    if placeholder:
        from .. import const as _const
        word = _const.extract_placeholder_confirm_word(placeholder)
        if word:
            return word, {
                "rule": "placeholder_confirm",
                "params": {"placeholder_hint": placeholder[:200]},
            }

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
        # ★ 批六修复8：el-input-number 优先保留表单默认值（EIS 实证数字字段
        #   带默认值且随机数可能超业务范围致校验拒绝；fixed_value 三阶段一致）。
        #   无默认值才回退 number_pattern 随机 1-100（apply_fill_rule 契约不变）。
        if _is_numeric_text(current_value):
            return current_value, {
                "rule": "fixed_value", "params": {"value": current_value}
            }
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

    # ★ 批六修复8：数字词 label 兜底（数量/CPU/内存/容量/盘/规格——E11 实证
    #   这些字段被 name_pattern 填文本 test569722）；默认值保留策略同 number 分支
    if _is_number_label(label_lower):
        if _is_numeric_text(current_value):
            return current_value, {
                "rule": "fixed_value", "params": {"value": current_value}
            }
        v = random.randint(1, 100)
        return str(v), {
            "rule": "number_pattern", "params": {"min": 1, "max": 100}
        }

    # ★ 批六修复8：登录名/用户名 → username_pattern（值与原 name_pattern 默认
    #   等值 test{ts}，仅规则名更准确，Stage 2 apply_fill_rule 走 username 分支）
    if any(kw in label_lower for kw in _USERNAME_LABEL_KEYWORDS):
        return f"{username.lower()}{ts}", {
            "rule": "username_pattern", "params": {"prefix": username.lower()}
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

    # 默认文本（名称类标识符保守字符集：小写字母+数字，云平台名称规则
    # 普遍为「小写字母/数字/中划线、小写字母开头」，大写 AT_ 前缀与下划线
    # 被实证拒绝（EIS create 表单校验））
    return f"{username.lower()}{ts}", {
        "rule": "name_pattern", "params": {"prefix": username.lower()}
    }


# 确认字段的语义标记词（label/placeholder 命中即视为「X 的确认字段」）
_CONFIRM_FIELD_MARKERS = (
    "确认", "再次", "重复", "校验", "重输",
    "confirm", "re-enter", "reenter", "retype", "repeat", "verify",
)
# 确认字段剥离标记词后需一并剥离的客套填充词
_CONFIRM_FILLER_WORDS = ("请", "输入", "键入", "please", "enter", "type")


def _strip_confirm_markers(text: str) -> str:
    """剥离确认语义标记词/客套填充词/空白与标点，返回剩余部分（原字段名候选）。

    归一化为小写无分隔符形态：「确认登录密码」→「登录密码」；
    「请再次输入密码」→「密码」；"Confirm Password"→"password"。
    """
    t = str(text or "").lower()
    for marker in _CONFIRM_FIELD_MARKERS:
        t = t.replace(marker, " ")
    for filler in _CONFIRM_FILLER_WORDS:
        t = t.replace(filler, " ")
    return "".join(ch for ch in t if not ch.isspace() and ch not in ":：,，/、-").strip()


def _pair_confirmation_field_values(fields: list, fill_data: dict) -> dict:
    """识别「确认 X」字段并复制「X」字段的值（方案批三 3.3）。

    背景（EIS 实证）：重置密码/重装/克隆弹窗的「密码」与「确认密码」
    各生成各的随机值 → 表单校验「两次输入密码不一致」拒绝。生成阶段
    持有全字段清单，全局配对可靠；Stage 2 replay 侧同语义已由
    replay_engine 的 password_cache（同规则复用同值）覆盖。

    配对规则：
    - 探测文本优先 label，label 无标记词时看 placeholder（「请再次输入密码」类）
    - 剩余文本与某非确认字段 label 的归一化形态完全相等，或互为包含
      （「确认登录密码」剩余「登录密码」可配对 label「登录密码」或「密码」）
    - 配对源自身不得也是确认字段（防止两个确认字段互相配对）

    Returns:
        {确认字段label: 源字段label的值} 覆盖表（无配对返回空 dict）
    """
    # ★ 批五 F3：placeholder 确认词判据单源（const.extract_placeholder_confirm_word）
    from .. import const as _const

    # 非确认字段归一化 label 索引（精确配对）+ 全量列表（包含配对）
    source_by_norm = {}
    source_list = []
    for field in fields:
        raw_label = field.get("label", "")
        if not raw_label:
            continue
        if any(m in raw_label.lower() for m in _CONFIRM_FIELD_MARKERS):
            continue
        norm = _strip_confirm_markers(raw_label)
        if norm:
            source_by_norm.setdefault(norm, raw_label)
            source_list.append((norm, raw_label))

    overrides = {}
    for field in fields:
        raw_label = field.get("label", "")
        if not raw_label or raw_label not in fill_data:
            continue
        # ★ 批五 F3：placeholder 命中引号包裹确认词的字段跳过配对复制——
        # 「请输入 "DELETE" 确认删除」语义是「输入指定确认词」（placeholder_confirm
        # 已生成 DELETE），不是「重复 X 字段的值」；防配对复制把确认词覆盖掉。
        if _const.extract_placeholder_confirm_word(field.get("placeholder", "")):
            continue
        # 探测文本：label 优先，label 无标记词时 placeholder 兜底
        probe = ""
        if any(m in raw_label.lower() for m in _CONFIRM_FIELD_MARKERS):
            probe = raw_label
        else:
            placeholder = field.get("placeholder", "")
            if placeholder and any(m in placeholder.lower() for m in _CONFIRM_FIELD_MARKERS):
                probe = placeholder
        if not probe:
            continue
        remainder = _strip_confirm_markers(probe)
        if not remainder:
            continue
        # 优先精确归一化相等；其次包含关系（双向）
        target_label = source_by_norm.get(remainder)
        if not target_label:
            for norm_src, raw_src in source_list:
                if norm_src in remainder or remainder in norm_src:
                    target_label = raw_src
                    break
        if target_label and target_label != raw_label and target_label in fill_data:
            overrides[raw_label] = fill_data[target_label]
            LOG.info(f"确认字段联动: '{raw_label}' 复用 '{target_label}' 的值")
    return overrides


def generate_fill_data(fields: list, username: str = "test") -> dict:
    """根据表单字段列表生成填充数据。

    遍历字段列表，对每个可输入字段调用 generate_fill_value()，
    返回 {label: value} 字典。

    Args:
        fields: scan_form_fields_v2() 返回的字段列表
        username: 用户名（用于名称字段前缀）

    Returns:
        {label: value} 字典
    """
    # 延迟导入避免循环依赖
    from .. import const

    MULTI_STEP_SKIP = set(const.MULTI_STEP_TYPES) | {
        "date-range-picker", "el-tree-select", "form-checkbox",
        "radio", "el-radio", "el-switch", "el-checkbox-group",
    }

    fill_data = {}
    for field in fields:
        label = field.get("label", "")
        if not label:
            continue

        field_type = field.get("type", "input")
        input_type = field.get("inputType", "text")
        kb_category = field.get("kb_category", "")

        # 跳过非输入型组件（多步交互组件由 fill_multi_step_fields 处理）
        if kb_category in MULTI_STEP_SKIP:
            continue

        value, _rule = generate_fill_value(
            field_type, input_type, username, label,
            placeholder=field.get("placeholder", ""),
            current_value=field.get("currentValue", ""))
        fill_data[label] = value

    # ★ 确认字段联动（方案批三 3.3）：「确认 X / 再次 X / Confirm X」字段的值
    # 直接复制「X」字段的值（两次输入不一致校验实证根因）
    fill_data.update(_pair_confirmation_field_values(fields, fill_data))

    return fill_data


def generate_fill_rules(fields: list) -> dict:
    """根据表单字段生成填充规则（而非具体值）。

    Stage 1 保存"规则"，Stage 2 根据规则生成"新值"，避免值重复。

    规则类型：
    - username_pattern: 生成唯一用户名
    - email_pattern: 生成测试邮箱
    - phone_pattern: 生成手机号
    - password_fixed: 固定测试密码
    - code_pattern: 生成编码
    - description_pattern: 生成描述
    - name_pattern: 生成名称
    - fixed_value: 固定值（不需要变化的字段）
    - ip_v4_pattern: 生成 IPv4 地址（4 段）
    - ip_v4_range_pattern: 生成 IPv4 地址区间（8 段）
    - ip_v6_pattern: 生成 IPv6 地址（8 段）
    - ip_v6_range_pattern: 生成 IPv6 地址区间（16 段）

    Args:
        fields: scan_form_fields_v2() 返回的字段列表

    Returns:
        {label: {"rule": str, "params": dict}} 字典
    """
    # ★ 批五 F3：placeholder 确认词规则判据与 generate_fill_value 同源
    from .. import const as _const

    rules = {}

    for field in fields:
        label = field.get("label", "")
        if not label:
            continue

        field_type = field.get("type", "input")
        kb_category = field.get("kb_category", "")
        input_type = field.get("inputType", "text")
        required = field.get("required", False)

        # 跳过 radio 字段（现在使用 click_radio 步骤，不需要 fill_rules）
        if kb_category == "radio":
            continue

        # 特殊处理 IP 输入字段
        if kb_category == "ip-input":
            input_count = field.get("inputCount", 4)
            ip_version = field.get("ipVersion", "v4")

            if ip_version == "v4":
                if input_count == 4:
                    # IPv4 单地址：10.0.0.1
                    rules[label] = {"rule": "ip_v4_pattern", "params": {
                        "base": "10.0.0",
                        "last_octet": 1 + len(rules)  # 避免重复
                    }}
                elif input_count == 8:
                    # IPv4 区间：10.0.0.1 - 10.0.0.10
                    rules[label] = {"rule": "ip_v4_range_pattern", "params": {
                        "base": "10.0.0",
                        "start": 1 + len(rules),
                        "end": 10 + len(rules)
                    }}
            elif ip_version == "v6":
                if input_count == 8:
                    # IPv6 单地址：2001:0db8:0000:0000:0000:0000:0000:0001
                    rules[label] = {"rule": "ip_v6_pattern", "params": {
                        "prefix": "2001:db8",
                        "suffix": 1 + len(rules)
                    }}
                elif input_count == 16:
                    # IPv6 区间
                    rules[label] = {"rule": "ip_v6_range_pattern", "params": {
                        "prefix": "2001:db8",
                        "start": 1 + len(rules),
                        "end": 10 + len(rules)
                    }}
            continue

        # ★ 批五 F3：placeholder 确认词规则（与 generate_fill_value 同判据，
        #   保证 Stage 1 落盘的 fill_rule 与实际值生成方式一致）
        _ph_word = _const.extract_placeholder_confirm_word(field.get("placeholder", ""))
        if _ph_word:
            rules[label] = {
                "rule": "placeholder_confirm",
                "params": {"placeholder_hint": field.get("placeholder", "")[:200]},
            }
            continue

        # 根据字段标签和类型推断规则
        rule = _infer_fill_rule(label, field_type, input_type, required,
                                current_value=field.get("currentValue", ""))
        rules[label] = rule

    return rules


def _infer_fill_rule(label: str, field_type: str, input_type: str, required: bool,
                     current_value: str = "") -> dict:
    """推断字段的填充规则。

    基于字段类型、标签关键词和通用模式推断，不硬编码模块特定字段名。
    """
    label_lower = label.lower()

    # 基于 inputType 推断（优先级最高）
    if input_type == "email":
        return {"rule": "email_pattern", "params": {"domain": "test.com"}}
    if input_type == "tel":
        return {"rule": "phone_pattern", "params": {"prefix": "138"}}
    if input_type == "password":
        return {"rule": "password_pattern", "params": {"prefix": "Test@"}}
    if input_type == "number":
        # ★ 批六修复8：el-input-number 默认值保留（与 generate_fill_value 同源）
        if _is_numeric_text(current_value):
            return {"rule": "fixed_value", "params": {"value": current_value}}
        return {"rule": "number_pattern", "params": {"min": 1, "max": 100}}

    # 基于标签关键词推断（覆盖 inputType 缺失的情况）
    phone_keywords = ("手机", "电话", "phone", "mobile", "tel")
    if any(kw in label_lower for kw in phone_keywords):
        return {"rule": "phone_pattern", "params": {"prefix": "138"}}
    email_keywords = ("邮箱", "email", "mail")
    if any(kw in label_lower for kw in email_keywords):
        return {"rule": "email_pattern", "params": {"domain": "test.com"}}
    password_keywords = ("密码", "password", "pwd")
    if any(kw in label_lower for kw in password_keywords):
        return {"rule": "password_pattern", "params": {"prefix": "Test@"}}

    # ★ 批六修复8：数字词 label 兜底（与 generate_fill_value 同源词表同策略）
    if _is_number_label(label_lower):
        if _is_numeric_text(current_value):
            return {"rule": "fixed_value", "params": {"value": current_value}}
        return {"rule": "number_pattern", "params": {"min": 1, "max": 100}}

    # ★ 批六修复8：登录名/用户名 → username_pattern（与 generate_fill_value 同源）
    if any(kw in label_lower for kw in _USERNAME_LABEL_KEYWORDS):
        return {"rule": "username_pattern", "params": {"prefix": "test"}}

    # 基于字段类型推断
    if field_type == "textarea":
        return {"rule": "description_pattern", "params": {"prefix": "auto"}}

    # 基于标签关键词推断（通用模式）
    if any(kw in label_lower for kw in ["code", "编码", "编号"]):
        return {"rule": "code_pattern", "params": {"prefix": "code"}}
    if any(kw in label_lower for kw in ["描述", "备注", "说明", "remark", "description", "memo"]):
        return {"rule": "description_pattern", "params": {"prefix": "auto"}}

    # 默认规则
    return {"rule": "name_pattern", "params": {"prefix": "test"}}


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

    # ★ 批五 F3：placeholder 确认词回放重建——从同弹窗 placeholder 重新提取
    #   （弹窗同源必得同一词，三阶段一致）；hint 丢失时回退静默空串由
    #   调用方的空值兜底处理（不再落到 name_pattern 随机值）。
    if rule_type == "placeholder_confirm":
        from .. import const as _const
        hint = params.get("placeholder_hint", "")
        word = _const.extract_placeholder_confirm_word(hint)
        if word:
            return word
        LOG.warning(f"    [apply_fill_rule] placeholder_confirm 规则未能从 hint "
                    f"重建确认词（hint={hint[:60]}），返回空串由调用方兜底")
        return ""

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
        # 名称类标识符保守字符集：小写字母+数字（k8s 风格安全子集）。
        # 云平台名称规则普遍为「小写字母/数字/中划线、小写字母开头」，
        # 禁用大写前缀 AT_ 与下划线分隔符（EIS create 表单校验实证拒绝）。
        prefix = params.get("prefix", "test")
        return f"{prefix}{timestamp}"

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
