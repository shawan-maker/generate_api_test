"""
test_run_history.py — run_history.py 单元测试

测试目标：
- classify: 失败归因分类（env/product/script/flaky/unknown/override）
- check_expired_overrides: 过期 override 检查
- load_json: JSON 加载容错
"""
import json
from pathlib import Path
from datetime import datetime, timedelta
import pytest

from lib.runtime.run_history import classify, check_expired_overrides, load_json


class TestClassify:
    """测试 classify 失败分类函数"""

    def test_override_priority(self):
        """override 优先级最高"""
        overrides = {
            "case_001": {"category": "product", "note": "已确认是产品缺陷"}
        }
        # 即使错误信息匹配 env，也应该返回 override 的 category
        result = classify("配额不足", "case_001", overrides)
        assert result == "product"

    def test_env_classification(self):
        """环境类错误识别"""
        test_cases = [
            "配额不足，无法创建资源",
            "quota exceeded",
            "服务未开通",
            "资源不足",
            "欠费停机",
            "余额不足",
            "第二项目未开通",
            "可订购配额为 0",
            "无法下单",
        ]
        for msg in test_cases:
            result = classify(msg, "test_case", {})
            assert result == "env", f"Failed for: {msg}"

    def test_product_classification(self):
        """产品缺陷识别"""
        test_cases = [
            "服务端异常",
            "系统异常，请稍后重试",
            "接口报错：500 Internal Server Error",
            "业务异常：数据不存在",
            "服务内部错误",
            "网关错误 502",
            "panic: runtime error",
            "NullPointerException",
            "该服务暂不可用",
        ]
        for msg in test_cases:
            result = classify(msg, "test_case", {})
            assert result == "product", f"Failed for: {msg}"

    def test_script_classification(self):
        """脚本错误识别"""
        test_cases = [
            "Assignment to constant variable",
            "定位失败：元素未找到",
            "未出现删除按钮",
            "Cannot read property 'click' of null",
            "TypeError: undefined is not a function",
            "ReferenceError: x is not defined",
            "SyntaxError: Unexpected token",
            "选择器超时",
            "await timeout",
        ]
        for msg in test_cases:
            result = classify(msg, "test_case", {})
            assert result == "script", f"Failed for: {msg}"

    def test_flaky_classification(self):
        """偶发性错误识别"""
        test_cases = [
            "偶发性网络超时",
            "偶爾失败",
            "flaky test",
            "网络波动",
            "请求波动",
        ]
        for msg in test_cases:
            result = classify(msg, "test_case", {})
            assert result == "flaky", f"Failed for: {msg}"

    def test_unknown_classification(self):
        """无法分类时返回 unknown"""
        test_cases = [
            "some random error",
            "unknown issue",
            "",
            None,
        ]
        for msg in test_cases:
            result = classify(msg, "test_case", {})
            assert result == "unknown", f"Failed for: {msg}"

    def test_env_priority_over_product(self):
        """env 优先级高于 product"""
        # 同时包含 env 和 product 关键词
        msg = "文件存储服务异常，配额不足"
        result = classify(msg, "test_case", {})
        assert result == "env"

    def test_override_with_missing_category(self):
        """override 缺少 category 字段时回退到正则"""
        overrides = {
            "case_001": {"note": "no category field"}
        }
        msg = "配额不足"
        result = classify(msg, "case_001", overrides)
        assert result == "env"


class TestCheckExpiredOverrides:
    """测试 check_expired_overrides 函数"""

    def test_no_expired(self, tmp_path):
        """无过期 override"""
        config_dir = tmp_path / "output" / "config"
        config_dir.mkdir(parents=True)

        # 创建 30 天前的 override（未过期）
        overrides = {
            "case_001": {
                "category": "product",
                "createdAt": (datetime.now() - timedelta(days=30)).isoformat(),
                "note": "recent"
            }
        }
        (config_dir / "failure_overrides.json").write_text(
            json.dumps(overrides), encoding="utf-8"
        )

        expired = check_expired_overrides(tmp_path)
        assert len(expired) == 0

    def test_expired_overrides(self, tmp_path):
        """检测过期 override"""
        config_dir = tmp_path / "output" / "config"
        config_dir.mkdir(parents=True)

        # 创建 100 天前的 override（已过期）
        overrides = {
            "case_001": {
                "category": "product",
                "createdAt": (datetime.now() - timedelta(days=100)).isoformat(),
                "note": "old override"
            },
            "case_002": {
                "category": "env",
                "createdAt": (datetime.now() - timedelta(days=95)).isoformat(),
                "note": "another old"
            }
        }
        (config_dir / "failure_overrides.json").write_text(
            json.dumps(overrides), encoding="utf-8"
        )

        expired = check_expired_overrides(tmp_path)
        assert len(expired) == 2
        assert expired[0]["id"] in ["case_001", "case_002"]
        assert expired[0]["ageDays"] > 90

    def test_missing_created_at(self, tmp_path):
        """缺少 createdAt 字段时跳过"""
        config_dir = tmp_path / "output" / "config"
        config_dir.mkdir(parents=True)

        overrides = {
            "case_001": {
                "category": "product",
                "note": "no timestamp"
            }
        }
        (config_dir / "failure_overrides.json").write_text(
            json.dumps(overrides), encoding="utf-8"
        )

        expired = check_expired_overrides(tmp_path)
        assert len(expired) == 0

    def test_invalid_date_format(self, tmp_path):
        """无效日期格式时跳过"""
        config_dir = tmp_path / "output" / "config"
        config_dir.mkdir(parents=True)

        overrides = {
            "case_001": {
                "category": "product",
                "createdAt": "invalid-date",
                "note": "bad date"
            }
        }
        (config_dir / "failure_overrides.json").write_text(
            json.dumps(overrides), encoding="utf-8"
        )

        expired = check_expired_overrides(tmp_path)
        assert len(expired) == 0


class TestLoadJson:
    """测试 load_json 函数"""

    def test_load_existing_file(self, tmp_path):
        """加载已存在的 JSON 文件"""
        test_file = tmp_path / "test.json"
        data = {"key": "value"}
        test_file.write_text(json.dumps(data), encoding="utf-8")

        result = load_json(test_file)
        assert result == data

    def test_load_nonexistent_file(self, tmp_path):
        """加载不存在的文件返回默认值"""
        test_file = tmp_path / "nonexistent.json"

        # 默认值是空列表（因为文件名不含 ledger）
        result = load_json(test_file)
        assert result == []

    def test_load_nonexistent_ledger(self, tmp_path):
        """ledger 文件不存在时返回空字典"""
        test_file = tmp_path / "failure_ledger.json"

        result = load_json(test_file)
        assert result == {}

    def test_load_invalid_json(self, tmp_path):
        """无效 JSON 返回默认值"""
        test_file = tmp_path / "test.json"
        test_file.write_text("invalid json {{{", encoding="utf-8")

        result = load_json(test_file)
        assert result == []

    def test_custom_default(self, tmp_path):
        """自定义默认值"""
        test_file = tmp_path / "test.json"

        result = load_json(test_file, default={"custom": "default"})
        assert result == {"custom": "default"}
