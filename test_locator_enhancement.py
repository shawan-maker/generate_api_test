"""
Test locator enhancement: hidden filters, overlay scope, expand_step integration.
"""
import pytest
from module_discovery.kb_loader import (
    _append_hidden_filter,
    apply_overlay_scope,
    detect_active_overlay_js,
    get_overlay_prefix,
    ProbeKB,
)
from module_discovery import const


class TestAppendHiddenFilter:
    """Test _append_hidden_filter() — three XPath modes."""

    def test_mode_a_with_predicate(self):
        """A: has [] predicate -> append new []"""
        xpath = "//button[contains(.,'OK')]"
        hf = "not(@disabled)"
        result = _append_hidden_filter(xpath, hf)
        assert result == "//button[contains(.,'OK')][not(@disabled)]"

    def test_mode_b_no_predicate(self):
        """B: no [] predicate -> append []"""
        xpath = "//button"
        hf = "not(@disabled)"
        result = _append_hidden_filter(xpath, hf)
        assert result == "//button[not(@disabled)]"

    def test_mode_c_parenthesized(self):
        """C: (XPath)[N] -> inject inside parens"""
        xpath = "(//button[contains(.,'OK')])[1]"
        hf = "not(@disabled)"
        result = _append_hidden_filter(xpath, hf)
        assert result == "(//button[contains(.,'OK')][not(@disabled)])[1]"

    def test_mode_c_multiple_predicates(self):
        """C: (XPath with multiple []) [N] -> inject inside parens"""
        xpath = "(//div[@class='el-dialog']//button[contains(.,'OK')])[2]"
        hf = "not(@disabled)"
        result = _append_hidden_filter(xpath, hf)
        assert result == "(//div[@class='el-dialog']//button[contains(.,'OK')][not(@disabled)])[2]"

    def test_attribute_predicate(self):
        """B: attribute predicate like [@class='x'] — still treated as has-predicate"""
        xpath = "//i[@class='el-icon-search']"
        hf = "not(@disabled)"
        result = _append_hidden_filter(xpath, hf)
        assert result == "//i[@class='el-icon-search'][not(@disabled)]"

    def test_empty_xpath(self):
        """Empty string -> unchanged"""
        assert _append_hidden_filter("", "hf") == ""

    def test_none_xpath(self):
        """None -> unchanged"""
        assert _append_hidden_filter(None, "hf") is None

    def test_non_xpath(self):
        """Non-XPath string -> unchanged"""
        assert _append_hidden_filter("role=button", "hf") == "role=button"

    def test_complex_element_ui_filter(self):
        """Full element-ui HIDDEN_FILTERS applied to button pattern"""
        xpath = "//button[contains(.,'delete')]"
        hf = const.HIDDEN_FILTERS['element-ui']
        result = _append_hidden_filter(xpath, hf)
        assert "is-hidden" in result
        assert "display: none" in result
        assert "@disabled" in result
        assert "is-disabled" in result
        # Must start with original xpath
        assert result.startswith("//button[contains(.,'delete')]")

    def test_complex_ant_design_filter(self):
        """Full ant-design HIDDEN_FILTERS applied to button pattern"""
        xpath = "//button[contains(@class,'ant-btn') and contains(.,'delete')]"
        hf = const.HIDDEN_FILTERS['ant-design']
        result = _append_hidden_filter(xpath, hf)
        assert "ant-drawer-hidden" in result
        assert "ant-modal-hidden" in result
        assert "aria-hidden" in result
        assert result.startswith("//button[contains(@class,'ant-btn')")


class TestApplyOverlayScope:
    """Test apply_overlay_scope() — prefix application."""

    def test_basic_scope(self):
        """Basic: prepend overlay XPath"""
        xpath = "//button[contains(.,'OK')]"
        prefix = "//div[contains(@class,'el-dialog')]"
        result = apply_overlay_scope(xpath, prefix)
        assert result == "//div[contains(@class,'el-dialog')]//button[contains(.,'OK')]"

    def test_no_prefix(self):
        """Empty prefix -> unchanged"""
        xpath = "//button[contains(.,'OK')]"
        result = apply_overlay_scope(xpath, "")
        assert result == xpath

    def test_empty_xpath(self):
        """Empty xpath -> unchanged"""
        assert apply_overlay_scope("", "//div") == ""

    def test_none_xpath(self):
        """None -> unchanged"""
        assert apply_overlay_scope(None, "//div") is None

    def test_non_xpath(self):
        """Non-XPath -> unchanged"""
        assert apply_overlay_scope("role=button", "//div") == "role=button"

    def test_parenthesized_xpath(self):
        """Parenthesized XPath: prefix goes inside parens before //"""
        xpath = "(//button[contains(.,'OK')])[1]"
        prefix = "//div[contains(@class,'el-dialog')]"
        result = apply_overlay_scope(xpath, prefix)
        # Prefix is injected inside the parens, before the button //
        assert result == "(//div[contains(@class,'el-dialog')]//button[contains(.,'OK')])[1]"

    def test_drawer_prefix(self):
        """Drawer prefix produces correct scope"""
        xpath = "//input[@class='el-input__inner']"
        prefix = "//div[contains(@class,'el-drawer') and not(contains(@style,'display: none'))]"
        result = apply_overlay_scope(xpath, prefix)
        assert "el-drawer" in result
        assert result.endswith("//input[@class='el-input__inner']")


class TestExpandStepWithHiddenFilter:
    """Test expand_step(apply_hidden=True) integration."""

    def test_expand_without_filter(self):
        """apply_hidden=False -> no filter appended"""
        kb = ProbeKB()
        result = kb.expand_step(
            "//button[contains(.,'{label}')]",
            label="OK",
            apply_hidden=False
        )
        assert result == "//button[contains(.,'OK')]"
        assert "is-hidden" not in result

    def test_expand_with_element_ui_filter(self):
        """apply_hidden=True + element-ui -> hidden filter appended"""
        kb = ProbeKB()
        result = kb.expand_step(
            "//button[contains(.,'{label}')]",
            label="OK",
            framework="element-ui",
            apply_hidden=True
        )
        assert "is-hidden" in result
        assert "display: none" in result
        assert "is-disabled" in result
        assert "OK" in result

    def test_expand_with_ant_design_filter(self):
        """apply_hidden=True + ant-design -> ant filter appended"""
        kb = ProbeKB()
        result = kb.expand_step(
            "//button[contains(@class,'ant-btn') and contains(.,'{label}')]",
            label="delete",
            framework="ant-design",
            apply_hidden=True
        )
        assert "ant-drawer-hidden" in result
        assert "ant-modal-hidden" in result
        assert "aria-hidden" in result

    def test_expand_with_placeholder_and_filter(self):
        """Both placeholders and hidden filter work together"""
        kb = ProbeKB()
        result = kb.expand_step(
            "//button[contains(.,'{label}')]",
            label="confirm",
            framework="element-ui",
            apply_hidden=True
        )
        assert "confirm" in result
        assert "is-hidden" in result

    def test_expand_hidden_no_framework(self):
        """apply_hidden=True but no framework -> no filter"""
        kb = ProbeKB()
        result = kb.expand_step(
            "//button[contains(.,'{label}')]",
            label="OK",
            apply_hidden=True
        )
        assert result == "//button[contains(.,'OK')]"

    def test_expand_idx_with_filter(self):
        """idx placeholder + hidden filter"""
        kb = ProbeKB()
        result = kb.expand_step(
            "//tbody/tr[{idx}]//span[contains(.,'{label}')]",
            label="edit",
            idx=2,
            framework="element-ui",
            apply_hidden=True
        )
        assert "tr[2]" in result
        assert "edit" in result
        assert "is-hidden" in result


class TestDetectActiveOverlayJS:
    """Test detect_active_overlay_js() generates correct JS."""

    def test_element_ui_js(self):
        js = detect_active_overlay_js("element-ui")
        assert "el-message-box" in js
        assert "el-drawer" in js
        assert "el-dialog" in js
        assert "el-dialog__wrapper" in js

    def test_ant_design_js(self):
        js = detect_active_overlay_js("ant-design")
        assert "ant-modal" in js
        assert "ant-drawer" in js

    def test_default_framework(self):
        """Unknown framework -> element-ui JS"""
        js = detect_active_overlay_js("unknown")
        assert "el-dialog" in js


class TestGetOverlayPrefix:
    """Test get_overlay_prefix() returns correct XPath prefix."""

    def test_element_ui_dialog(self):
        prefix = get_overlay_prefix("el-dialog", "element-ui")
        assert "el-dialog" in prefix
        assert prefix.startswith("//div")

    def test_element_ui_drawer(self):
        prefix = get_overlay_prefix("el-drawer", "element-ui")
        assert "el-drawer" in prefix

    def test_element_ui_message_box(self):
        prefix = get_overlay_prefix("el-message-box", "element-ui")
        assert "el-message-box" in prefix

    def test_ant_design_modal(self):
        prefix = get_overlay_prefix("ant-modal", "ant-design")
        assert "ant-modal" in prefix

    def test_ant_design_drawer(self):
        prefix = get_overlay_prefix("ant-drawer", "ant-design")
        assert "ant-drawer" in prefix

    def test_empty_type(self):
        assert get_overlay_prefix("", "element-ui") == ""

    def test_none_type(self):
        assert get_overlay_prefix(None, "element-ui") == ""

    def test_unknown_type(self):
        assert get_overlay_prefix("unknown-type", "element-ui") == ""


class TestHiddenFilterConstants:
    """Test const.HIDDEN_FILTERS structure."""

    def test_element_ui_keys(self):
        hf = const.HIDDEN_FILTERS['element-ui']
        assert "is-hidden" in hf
        assert "display: none" in hf
        assert "@disabled" in hf
        assert "is-disabled" in hf

    def test_ant_design_keys(self):
        hf = const.HIDDEN_FILTERS['ant-design']
        assert "ant-drawer-hidden" in hf
        assert "ant-modal-hidden" in hf
        assert "aria-hidden" in hf
        assert "ant-btn-disabled" in hf
        assert "ant-select-disabled" in hf

    def test_universal_keys(self):
        hf = const.HIDDEN_FILTERS['_universal']
        assert "display: none" in hf
        assert "@disabled" in hf


class TestOverlaySelectorsConstants:
    """Test const.OVERLAY_SELECTORS structure."""

    def test_element_ui_entries(self):
        entries = const.OVERLAY_SELECTORS['element-ui']
        names = [name for name, _ in entries]
        assert "el-dialog" in names
        assert "el-drawer" in names
        assert "el-message-box" in names

    def test_ant_design_entries(self):
        entries = const.OVERLAY_SELECTORS['ant-design']
        names = [name for name, _ in entries]
        assert "ant-modal" in names
        assert "ant-drawer" in names

    def test_prefix_format(self):
        """All prefixes should be valid XPath starting with //"""
        for fw, entries in const.OVERLAY_SELECTORS.items():
            for name, prefix in entries:
                assert prefix.startswith("//"), f"{fw}/{name}: prefix must start with //"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
