"""
field_classifier.py — 5 角色字段分类系统与值索引

从 analyze_flow.py 提取的字段分类引擎：
  - ValueIndex: 值 → 来源路径的索引
  - classify_fields / classify_fields_recursive: 5 角色分类 (name/mutable/context/generate/static)
  - build_value_index: 构建值索引
  - 辅助函数: is_noise_value, _analyze_value_pattern, _classify_value_type, _is_system_id_value,
              _get_nested_value, _flatten_body_for_match, _extract_by_path_generic
"""

import re
import json
import logging
from typing import Optional
from core.discovery import const

LOG = logging.getLogger("field_classifier")


def _analyze_value_pattern(field_name: str, value) -> dict:
    """根据值的特征推断字段角色（值模式分析）。

    主要数据驱动（看值本身的特征），对加密/不可读值用字段名辅助判断。
    用于区分「动态生成字段」和「系统固定值」。

    Args:
        field_name: 字段名（加密值时用作辅助信号）
        value: 字段值

    Returns:
        {"role": "generate"|"static", "pattern": "..."} 或 None
    """
    if not isinstance(value, str) or not value:
        return None

    fn_lower = field_name.lower()

    # 1. 长十六进制串（≥32 字符）→ hash/加密值，不可重放
    if re.fullmatch(r'[0-9a-fA-F]{32,}', value):
        return {"role": "generate", "pattern": "hex_hash"}

    # 2. 长 Base64 串（≥50 字符，含 +/=）→ 加密值，不可重放
    #    对加密值用字段名辅助判断应生成什么类型的测试值
    if len(value) >= 50 and re.search(r'[+/=]', value) and re.fullmatch(r'[A-Za-z0-9+/=]+', value):
        # 字段名含敏感信息（phone/email/password）→ 标记为 static，复用原始加密值
        # 原因：服务端要求加密后的值，我们无法生成有效的加密值（不知道公钥）
        if any(kw in fn_lower for kw in ('phone', 'mobile', 'cell', 'email', 'mail', 'password', 'passwd', 'pwd')):
            return {"role": "static", "pattern": "encrypted_sensitive"}
        return {"role": "generate", "pattern": "base64_encrypted"}

    # 3. 短可读字符串 — 进一步分析子模式
    if len(value) < 80:
        # 3a. 含 @ → 邮箱
        if '@' in value and '.' in value.split('@')[-1]:
            return {"role": "generate", "pattern": "email"}

        # 3b. 纯数字且长度 8-15 → 手机号
        if re.fullmatch(r'\+?\d{8,15}', value):
            return {"role": "generate", "pattern": "phone"}

        # 3c. 短可读字符串（含字母，长度 2-50）→ 名称/文本
        if 2 <= len(value) <= 50 and re.search(r'[a-zA-Z一-鿿]', value):
            return {"role": "generate", "pattern": "text"}

    # 4. 短固定格式值（如 "+86", "1", "ACTIVE"）→ 静态
    if len(value) <= 10:
        return {"role": "static"}

    return None


def _classify_value_type(value) -> str:
    """根据值本身特征分类（不看参数名，只看值的特征）。

    Returns:
        "hex_id"     — 32字符十六进制串 (如 "42ffdba38c58484f9be2bc1adf1672e6")
        "uuid"       — 标准 UUID 格式 (如 "550e8400-e29b-41d4-a716-446655440000")
        "numeric_id" — 8位以上纯数字
        "boolean"    — 布尔值
        "number"     — 普通数字
        "string"     — 其他字符串
    """
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if not isinstance(value, str) or not value:
        return "string"
    if re.fullmatch(r'[0-9a-fA-F]{32,}', value):
        return "hex_id"
    if len(value) == 36 and value.count('-') == 4 and re.fullmatch(r'[0-9a-fA-F\-]+', value):
        return "uuid"
    if value.isdigit() and len(value) >= 8:
        return "numeric_id"
    return "string"


def _is_system_id_value(value) -> bool:
    """判断值是否是系统 ID 类型（hex_id、uuid、长 numeric_id、长 alphanumeric ID）。

    这些类型的值在不同环境/时间点会变化，不能仅靠精确值匹配。
    当值匹配失败时，需要用字段名回退匹配。
    """
    vtype = _classify_value_type(value)
    if vtype in ("hex_id", "uuid", "numeric_id"):
        return True
    # 补充：长 alphanumeric ID（32+ 字符，混合字母和数字）
    # 真实系统中 ID 不一定只用 hex 字符，可能包含 g-z 等字母
    if isinstance(value, str) and len(value) >= 32 and re.fullmatch(r'[a-zA-Z0-9]+', value):
        has_digit = any(c.isdigit() for c in value)
        has_alpha = any(c.isalpha() for c in value)
        if has_digit and has_alpha:
            return True
    return False


# ========== 值索引与 5 角色分类引擎（v2.0） ==========


def is_noise_value(value) -> bool:
    """过滤过于常见、不能作为匹配依据的值。

    噪声值包括：布尔值、空值、短字符串、小数字、通用枚举值等。
    这些值在前置 API 响应中大量出现，不能作为字段来源的可靠匹配依据。

    Args:
        value: 任意类型的值

    Returns:
        True 表示是噪声值，不应参与值匹配
    """
    if value is None:
        return True
    if isinstance(value, bool):
        return True
    s = str(value).strip()
    if not s or len(s) < 3:
        return True
    s_lower = s.lower()
    # 通用枚举/固定值
    noise_literals = {
        "true", "false", "null", "none", "0", "1", "-1", "[]", "{}",
        "active", "enable", "disable", "disabled", "pending", "deleted",
        "success", "ok", "yes", "no", "on", "off", "male", "female",
    }
    if s_lower in noise_literals:
        return True
    # 纯数字且 < 100
    if s.isdigit() and int(s) < 100:
        return True
    return False


class ValueIndex:
    """值 → 来源路径的索引。

    扫描所有前置 API 响应 + create body + create 响应，构建扁平化的值到来源路径映射。
    支持多源匹配和评分排序。
    """

    def __init__(self):
        self.index: dict[str, list[dict]] = {}  # {value_str: [entry, ...]}

    def to_dict(self) -> dict:
        """序列化为可 JSON 存储的 dict。"""
        return {"index": self.index}

    @classmethod
    def from_dict(cls, data: dict) -> 'ValueIndex':
        """从 dict 反序列化。"""
        vi = cls()
        vi.index = data.get("index", {})
        return vi

    def add(self, value_str: str, source_api: str, source_path: str,
            field_leaf: str, path_depth: int,
            timestamp: float = 0.0, context: str = "",
            api_info: dict = None):
        """添加一个值到索引中。

        Args:
            value_str: 值的字符串形式
            source_api: 来源 API 标识（如 "current_user"）
            source_path: 完整来源路径（如 "current_user.entity_tenantId"）
            field_leaf: 路径叶子节点名（如 "tenantId"）
            path_depth: 路径深度
            timestamp: API 调用时间戳
            context: 所属时间窗口（如 "init", "replay:迁移"）
            api_info: 完整的 API 信息 dict（用于链式追踪）
        """
        if is_noise_value(value_str):
            return
        entry = {
            "value": value_str,
            "source_api": source_api,
            "source_path": source_path,
            "field_leaf": field_leaf.lower(),
            "path_depth": path_depth,
            "timestamp": timestamp,
            "context": context,
            "api_info": api_info,
        }
        self.index.setdefault(value_str, []).append(entry)

    def lookup(self, value, request_field_name: str = "",
               current_action: str = "",
               exclude_sources: set = None) -> Optional[dict]:
        """在索引中查找匹配项。

        多源匹配时的评分规则：
          1. 时序优先：同操作窗口内的 API (+100) > init (+50) > 其他 (+10)
          2. 字段名匹配：request body 字段名与响应路径叶子一致 (+30)
          3. 路径深度：浅优先 (-depth * 10)

        Args:
            value: 要查找的值（支持标量和列表，列表取第一个元素）
            request_field_name: 请求体中的字段名（用于消歧）
            current_action: 当前操作名（用于时序优先级）
            exclude_sources: 要排除的来源前缀集合（如 {"create_body"} 排除自引用）

        Returns:
            匹配的 entry dict 或 None
        """
        # 处理列表值
        if isinstance(value, list):
            if not value:
                return None
            value = value[0]
        value_str = str(value)

        entries = self.index.get(value_str)
        if not entries:
            return None

        # 过滤排除的来源
        if exclude_sources:
            entries = [e for e in entries if not any(e.get("source_path", "").startswith(prefix) for prefix in exclude_sources)]
            if not entries:
                return None

        if len(entries) == 1:
            return entries[0]

        def score(entry: dict) -> float:
            s = 0.0
            # 时序优先级
            ctx = entry.get("context", "")
            if current_action and ctx:
                ctx_action = ctx.replace("replay:", "")
                if ctx_action == current_action:
                    s += 100
                elif ctx == "init" or ctx == "":
                    s += 50
                else:
                    s += 10
            # 字段名匹配
            if request_field_name and entry.get("field_leaf") == request_field_name.lower():
                s += 30
            # 路径深度
            s -= entry.get("path_depth", 0) * 10
            return s

        return max(entries, key=score)

    def lookup_all(self, value, current_action: str = "") -> list:
        """返回某个值在索引中的所有匹配条目（不评分，不排序）。

        用于 _analyze_path_params 的 Step 1.5：检查 body 已有的 context source
        是否也能匹配当前 path 段值，优先复用已有 source 确保 pre-API 被收集。

        Args:
            value: 要查找的值
            current_action: 当前操作名（未使用，保留接口一致性）

        Returns:
            所有匹配的 entry dict 列表，找不到则返回空列表
        """
        if isinstance(value, list):
            if not value:
                return []
            value = value[0]
        return self.index.get(str(value), [])


def build_value_index(pre_api_candidates: list, create_body_sample: dict,
                      create_response_sample: dict = None,
                      context_fields: dict = None,
                      replay_windows: dict = None) -> ValueIndex:
    """构建值 → 来源路径的索引。

    扫描所有可用数据源，将响应中的值扁平化为 value_str → source_path 映射。

    数据源（按添加顺序）：
    1. pre_api_candidates 响应（前置 GET API）
    2. create 请求体本身
    3. create 响应体（entity 字段）

    Args:
        pre_api_candidates: 前置 API 候选列表
        create_body_sample: 创建步骤的请求体
        create_response_sample: 创建步骤的响应体 dict（已解析）
        context_fields: auth_profile 中声明的上下文字段
        replay_windows: 操作时间窗口（用于时序）

    Returns:
        ValueIndex 实例
    """
    vi = ValueIndex()
    replay_windows = replay_windows or {}

    # ── 来源 1: 前置 API 响应 ──
    for api in (pre_api_candidates or []):
        api_id = api.get("id", "")
        api_context = api.get("context", "init")
        api_timestamp = api.get("timestamp", 0.0)
        body_text = api.get("response_sample", {}).get("response_body", "")
        if not body_text:
            continue
        try:
            body = json.loads(body_text) if isinstance(body_text, str) else body_text
        except Exception:
            continue

        for field_info in api.get("extracted_fields", []):
            path = field_info.get("path", "")
            field_name = field_info.get("name", "")
            if not path or not field_name:
                continue
            value = _extract_by_path_generic(body, path)
            if value is None:
                continue
            # source_path 格式: "api_id.field_name"
            source_path = f"{api_id}.{field_name}"
            # field_leaf: 从 field_name 中提取叶子（如 "entity_0_children_0_id" → "id"）
            field_leaf = field_name.rsplit("_", 1)[-1] if "_" in field_name else field_name
            path_depth = path.count(".") + path.count("[")
            vi.add(
                value_str=str(value),
                source_api=api_id,
                source_path=source_path,
                field_leaf=field_leaf,
                path_depth=path_depth,
                timestamp=api_timestamp,
                context=api_context,
                api_info=api,
            )

    # ── 来源 2: create 请求体 ──
    if create_body_sample and isinstance(create_body_sample, dict):
        for field_name, value in create_body_sample.items():
            if isinstance(value, (str, int, float, bool)):
                vi.add(
                    value_str=str(value),
                    source_api="create_body",
                    source_path=f"create_body.{field_name}",
                    field_leaf=field_name,
                    path_depth=0,
                    timestamp=0,
                    context="create",
                )

    # ── 来源 3: create 响应体 ──
    if create_response_sample and isinstance(create_response_sample, dict):
        # 遍历 entity 下的所有字段
        entity = None
        for ek in const.ENVELOPE_KEY_CANDIDATES:
            if ek in create_response_sample and isinstance(create_response_sample[ek], dict):
                entity = create_response_sample[ek]
                break
        if entity is None:
            entity = create_response_sample
        if isinstance(entity, dict):
            for field_name, value in entity.items():
                if isinstance(value, (str, int, float, bool)):
                    vi.add(
                        value_str=str(value),
                        source_api="create",
                        source_path=f"create.{field_name}",
                        field_leaf=field_name,
                        path_depth=1,
                        timestamp=0,
                        context="create_response",
                    )

    LOG.info(f"  [ValueIndex] 构建完成: {len(vi.index)} 个唯一值")
    return vi


def classify_fields(body_sample: dict, value_index: ValueIndex,
                    create_body_sample: dict = None,
                    current_action: str = "",
                    exclude_sources: set = None) -> dict:
    """为请求体中每个字段标注角色（5 角色体系 v2.0）。

    一次分类完成，不再有两阶段覆盖。纯粹靠值匹配驱动 context 识别。

    分类优先级：
    Pass 1: name     → 字段名含 name/title/label + 值是短可读字符串
    Pass 2: mutable  → 字段名含 description/memo/remark/note/content
    Pass 3: context  → value_index.lookup(value, key, current_action) 精确值命中
    Pass 4: generate → _analyze_value_pattern 返回 generate 类型
    Pass 5: static   → 兜底

    Args:
        body_sample: 请求体样本 dict
        value_index: 全局值索引
        create_body_sample: create 步骤的请求体（用于 name 字段的交叉验证）
        current_action: 当前操作名（用于时序优先级）
        exclude_sources: 要排除的来源前缀集合（如 {"create_body"} 排除自引用）

    Returns:
        {field_name: {"role": ..., "source": ...}} 字典
    """
    if not body_sample:
        return {}

    roles = {}
    for key, value in body_sample.items():
        key_lower = key.lower()

        # ── Pass 1: name ──
        is_name_like = any(kw in key_lower for kw in const.NAME_FIELD_KEYWORDS)
        if is_name_like and isinstance(value, str) and 1 < len(value) < 50:
            roles[key] = {"role": "name"}
            continue

        # ── Pass 2: mutable ──
        is_mutable_like = any(kw in key_lower for kw in const.MUTABLE_FIELD_KEYWORDS)
        if is_mutable_like:
            roles[key] = {"role": "mutable"}
            continue

        # ── Pass 3a: context（精确值匹配） ──
        match = value_index.lookup(value, request_field_name=key,
                                   current_action=current_action,
                                   exclude_sources=exclude_sources)
        if match:
            roles[key] = {"role": "context", "source": match["source_path"]}
            continue

        # ── Pass 4: generate（值模式分析） ──
        if isinstance(value, str) and len(value) > 20:
            pattern = _analyze_value_pattern(key, value)
            if pattern and pattern.get("role") == "generate":
                roles[key] = {"role": "generate", "pattern": pattern["pattern"]}
                LOG.debug(f"    classify_fields: {key} → generate/{pattern['pattern']}")
                continue
            # encrypted_sensitive 返回 role="static" → 落入 Pass 5

        # ── Pass 5: static（兜底） ──
        roles[key] = {"role": "static"}

    return roles


def _get_nested_value(d, key):
    """用 dot-separated 路径从嵌套 dict 中取值。

    _get_nested_value({"a": {"b": 1}}, "a.b") → 1
    _get_nested_value({"x": 2}, "x") → 2
    _get_nested_value({"a": {"b": 1}}, "a.c") → None
    """
    if "." not in key:
        if isinstance(d, dict):
            return d.get(key)
        return None
    parts = key.split(".")
    current = d
    for part in parts:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
        if current is None:
            return None
    return current


def classify_fields_recursive(body_sample, value_index, create_body_sample=None,
                               current_action="", exclude_sources=None):
    """递归版本的 classify_fields：先分类顶层，再递归进入嵌套 dict。

    对请求体中每个字段（包括嵌套 dict 内的子字段）标注角色。
    嵌套字段使用 dot-prefixed key，如 "passwordPolicy.tenantId"。

    Args:
        body_sample: 请求体样本 dict
        value_index: 全局值索引
        create_body_sample: create 步骤的请求体
        current_action: 当前操作名
        exclude_sources: 要排除的来源前缀集合

    Returns:
        {field_name: {"role": ..., "source": ...}} 字典
        嵌套字段使用 dot-prefixed key
    """
    # Step 1: 顶层分类
    roles = classify_fields(body_sample, value_index, create_body_sample,
                            current_action, exclude_sources)

    # Step 2: 递归进入值为 dict 的字段
    if isinstance(body_sample, dict):
        for key, value in body_sample.items():
            if isinstance(value, dict):
                prefix = f"{key}."
                nested_roles = classify_fields_recursive(
                    value, value_index, create_body_sample,
                    current_action, exclude_sources
                )
                for nk, nv in nested_roles.items():
                    roles[f"{prefix}{nk}"] = nv

    return roles


def _flatten_body_for_match(body_sample, field_roles):
    """扁平化 body_sample，产出 (flat_key, primitive_value) 列表。

    只展开 field_roles 中有 dot-prefixed key 记录的嵌套 dict。

    输入: {"passwordPolicy": {"id": "abc"}, "userId": "def"}
          field_roles 有 "passwordPolicy.id" 的 key
    输出: [("userId", "def"), ("passwordPolicy.id", "abc")]
    """
    result = []
    if not isinstance(body_sample, dict):
        return result
    for key, value in body_sample.items():
        if isinstance(value, (str, int, float, bool)):
            result.append((key, value))
        elif isinstance(value, list):
            result.append((key, value))
        elif isinstance(value, dict):
            prefix = f"{key}."
            has_nested_roles = any(rk.startswith(prefix) for rk in field_roles)
            if has_nested_roles:
                for nk, nv in value.items():
                    if isinstance(nv, (str, int, float, bool)):
                        result.append((f"{prefix}{nk}", nv))
                    elif isinstance(nv, list):
                        result.append((f"{prefix}{nk}", nv))
    return result


def _extract_by_path_generic(obj: dict, path: str):
    """
    通用的路径提取函数（支持数组索引）。

    Args:
        obj: JSON 对象
        path: 路径字符串，例如 "entity.list[0].id"

    Returns:
        提取的值，或 None
    """
    if not path or obj is None:
        return None

    # 分割路径段
    parts = []
    current = ''
    for char in path:
        if char in '.[':
            if current:
                parts.append(current)
                current = ''
            if char == '[':
                parts.append('[')
        elif char == ']':
            if current:
                parts.append(current)
                current = ''
            parts.append(']')
        else:
            current += char
    if current:
        parts.append(current)

    # 遍历提取
    value = obj
    i = 0
    while i < len(parts):
        part = parts[i]

        if part == '[':
            # 下一个部分是索引
            i += 1
            if i < len(parts):
                idx_str = parts[i]
                if idx_str == '*':
                    # 通配符：取第一个元素（用于 [*] 路径）
                    if isinstance(value, list) and value:
                        value = value[0]
                    else:
                        return None
                else:
                    try:
                        index = int(idx_str)
                        value = value[index]
                    except (ValueError, IndexError, TypeError):
                        return None
            i += 1
            # 跳过 ']'
            if i < len(parts) and parts[i] == ']':
                i += 1
        else:
            # 普通字段访问
            if isinstance(value, dict):
                value = value.get(part)
            else:
                return None
            i += 1

    return value
