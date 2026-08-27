"""
test_utils.py — utils.py 单元测试

测试目标：
- safe_write: 文件写入、重试机制、Windows 权限防护
- safe_write_json: JSON 序列化与写入
- generate_session_id: 格式校验、唯一性
- safe_read_json: 读取、容错、默认值
"""
import json
import time
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import pytest

from lib.utils import safe_write, safe_write_json, generate_session_id, safe_read_json


class TestSafeWrite:
    """测试 safe_write 函数"""

    def test_basic_write(self, tmp_path):
        """基本写入功能"""
        test_file = tmp_path / "test.txt"
        safe_write(test_file, "hello world")

        assert test_file.exists()
        assert test_file.read_text() == "hello world"

    def test_creates_parent_dirs(self, tmp_path):
        """自动创建父目录"""
        test_file = tmp_path / "subdir" / "nested" / "test.txt"
        safe_write(test_file, "data")

        assert test_file.exists()
        assert test_file.read_text() == "data"

    def test_overwrites_existing_file(self, tmp_path):
        """覆盖已存在文件"""
        test_file = tmp_path / "test.txt"
        test_file.write_text("old content")

        safe_write(test_file, "new content")

        assert test_file.read_text() == "new content"

    def test_unicode_content(self, tmp_path):
        """支持 Unicode 内容"""
        test_file = tmp_path / "test.txt"
        content = "中文测试 🚀 emoji"
        safe_write(test_file, content)

        # Windows 需要显式指定 UTF-8 编码读取
        assert test_file.read_text(encoding="utf-8") == content

    def test_rename_fallback(self, tmp_path):
        """rename 失败时直接写入目标文件"""
        test_file = tmp_path / "test.txt"

        with patch.object(Path, 'rename', side_effect=OSError("Permission denied")):
            safe_write(test_file, "fallback write")

        assert test_file.exists()
        assert test_file.read_text() == "fallback write"

    def test_retry_on_write_failure(self, tmp_path):
        """写入失败时重试"""
        test_file = tmp_path / "test.txt"
        call_count = [0]

        original_write_text = Path.write_text

        def mock_write_text(self, data, encoding=None):
            call_count[0] += 1
            if call_count[0] < 3:
                raise OSError("Write failed")
            return original_write_text(self, data, encoding)

        with patch.object(Path, 'write_text', mock_write_text):
            safe_write(test_file, "retry success")

        assert test_file.exists()
        assert test_file.read_text() == "retry success"
        assert call_count[0] >= 3


class TestSafeWriteJson:
    """测试 safe_write_json 函数"""

    def test_basic_json_write(self, tmp_path):
        """基本 JSON 写入"""
        test_file = tmp_path / "test.json"
        data = {"key": "value", "number": 42}

        safe_write_json(test_file, data)

        assert test_file.exists()
        loaded = json.loads(test_file.read_text())
        assert loaded == data

    def test_json_indentation(self, tmp_path):
        """JSON 缩进格式"""
        test_file = tmp_path / "test.json"
        data = {"a": 1}

        safe_write_json(test_file, data, indent=2)

        content = test_file.read_text()
        assert "\n  " in content  # 有缩进

    def test_non_ascii_handling(self, tmp_path):
        """非 ASCII 字符处理"""
        test_file = tmp_path / "test.json"
        data = {"message": "中文测试"}

        # ensure_ascii=False (默认) 保留中文
        safe_write_json(test_file, data, ensure_ascii=False)
        # Windows 需要显式指定 UTF-8 编码读取
        assert "中文测试" in test_file.read_text(encoding="utf-8")

        # ensure_ascii=True 转义为 Unicode
        safe_write_json(test_file, data, ensure_ascii=True)
        assert "\\u" in test_file.read_text(encoding="utf-8")

    def test_nested_structures(self, tmp_path):
        """嵌套数据结构"""
        test_file = tmp_path / "test.json"
        data = {
            "level1": {
                "level2": {
                    "items": [1, 2, 3],
                    "nested": {"key": "value"}
                }
            }
        }

        safe_write_json(test_file, data)

        loaded = json.loads(test_file.read_text())
        assert loaded == data


class TestGenerateSessionId:
    """测试 generate_session_id 函数"""

    def test_format(self):
        """格式校验：YYYYMMDDThhmmss_XXXX"""
        session_id = generate_session_id()

        # 格式：14位时间戳 + T + 6位时间 + _ + 4位随机
        assert len(session_id) == 20
        assert session_id[8] == "T"
        assert session_id[15] == "_"

        # 时间戳部分为数字
        assert session_id[:8].isdigit()
        assert session_id[9:15].isdigit()

        # 随机后缀为字母数字
        suffix = session_id[16:]
        assert len(suffix) == 4
        assert suffix.isalnum()

    def test_uniqueness(self):
        """唯一性：连续生成不同 ID"""
        ids = [generate_session_id() for _ in range(10)]
        unique_ids = set(ids)

        # 随机后缀保证唯一性
        assert len(unique_ids) == 10

    def test_time_progression(self):
        """时间戳递进"""
        id1 = generate_session_id()
        time.sleep(1.1)  # 等待超过 1 秒
        id2 = generate_session_id()

        ts1 = id1[:15]  # YYYYMMDDThhmmss
        ts2 = id2[:15]

        # 第二个时间戳应该大于等于第一个
        assert ts2 >= ts1


class TestSafeReadJson:
    """测试 safe_read_json 函数"""

    def test_basic_read(self, tmp_path):
        """基本读取"""
        test_file = tmp_path / "test.json"
        data = {"key": "value"}
        test_file.write_text(json.dumps(data))

        result = safe_read_json(test_file)
        assert result == data

    def test_nonexistent_file(self, tmp_path):
        """文件不存在返回默认值"""
        test_file = tmp_path / "nonexistent.json"

        result = safe_read_json(test_file, default={"fallback": True})
        assert result == {"fallback": True}

    def test_invalid_json(self, tmp_path):
        """无效 JSON 返回默认值"""
        test_file = tmp_path / "test.json"
        test_file.write_text("not valid json {{{")

        result = safe_read_json(test_file, default=None)
        assert result is None

    def test_default_value_types(self, tmp_path):
        """默认值类型支持"""
        test_file = tmp_path / "test.json"

        # None
        assert safe_read_json(test_file, default=None) is None

        # 空字典
        assert safe_read_json(test_file, default={}) == {}

        # 列表
        assert safe_read_json(test_file, default=[]) == []

        # 自定义对象
        custom = {"custom": "default"}
        assert safe_read_json(test_file, default=custom) == custom

    def test_complex_nested_data(self, tmp_path):
        """复杂嵌套数据结构"""
        test_file = tmp_path / "test.json"
        data = {
            "users": [
                {"id": 1, "name": "Alice", "tags": ["admin", "user"]},
                {"id": 2, "name": "Bob", "tags": ["user"]}
            ],
            "metadata": {
                "created": "2026-08-25",
                "version": 1
            }
        }
        test_file.write_text(json.dumps(data))

        result = safe_read_json(test_file)
        assert result == data
        assert len(result["users"]) == 2
        assert result["metadata"]["version"] == 1
