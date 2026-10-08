"""
path_utils.py — 路径提取工具

提供嵌套对象的路径提取功能，支持数组索引路径。
"""

from typing import Optional, Any


def extract_by_path(obj: Any, path: str) -> Optional[Any]:
    """从嵌套对象中提取值，支持数组索引路径。

    支持路径格式:
      - "entity.id" -> obj["entity"]["id"]
      - "entity.list[0].id" -> obj["entity"]["list"][0]["id"]
      - "entity.list_0.id" -> obj["entity"]["list"][0]["id"] (下划线索引)

    Args:
        obj: 嵌套字典/列表对象
        path: 点分隔路径，可包含数组索引 [n] 或 _n 后缀

    Returns:
        提取的值，失败返回 None
    """
    if not path or obj is None:
        return None

    # 分割路径段
    segments = []
    current = ""

    i = 0
    while i < len(path):
        char = path[i]

        if char == '.':
            if current:
                segments.append(current)
                current = ""
        elif char == '[':
            if current:
                segments.append(current)
                current = ""
            # 解析数组索引
            j = i + 1
            while j < len(path) and path[j] != ']':
                j += 1
            if j < len(path):
                index_str = path[i+1:j]
                segments.append(f"[{index_str}]")
                i = j
        else:
            current += char

        i += 1

    if current:
        segments.append(current)

    # 遍历路径段提取值
    value = obj
    for segment in segments:
        if value is None:
            return None

        # 数组索引段 [n]
        if segment.startswith("[") and segment.endswith("]"):
            try:
                index = int(segment[1:-1])
                if isinstance(value, list) and 0 <= index < len(value):
                    value = value[index]
                else:
                    return None
            except (ValueError, IndexError):
                return None
        # 字典字段段（检查是否含下划线索引，如 list_0）
        elif isinstance(value, dict):
            # 先尝试直接匹配
            if segment in value:
                value = value[segment]
            else:
                # 尝试解析下划线索引：field_N -> field[N]
                import re
                m = re.match(r'^(.+)_(\d+)$', segment)
                if m:
                    field_name, index_str = m.groups()
                    index = int(index_str)
                    field_value = value.get(field_name)
                    if isinstance(field_value, list) and 0 <= index < len(field_value):
                        value = field_value[index]
                    else:
                        return None
                else:
                    return None
        else:
            return None

    return value
