"""
ai_debug_assistant.py — Vision API 辅助分析

当 DOM 扫描无法找到目标元素时，使用多模态 LLM 分析页面截图。
支持自动发现 API key（Claude Code / WorkBuddy / 环境变量）。

核心功能：
1. _discover_api_config() — 自动获取 API key、base_url、model
2. _compute_page_state_key() — 计算页面状态 key（URL + 弹窗标题 hash）
3. _vision_cache — session 级缓存，同页面状态只调用一次 Vision API
4. ai_assisted_analysis() — 主入口，截图 + Vision 分析

设计原则：
- 同页面状态（URL + 弹窗组合）只截图一次
- 一次分析返回所有可见元素，多个 error 共享结果
- 无 API key 时自动降级，不阻断流程
"""

import json
import logging
import hashlib
import base64
import httpx
from pathlib import Path
from typing import Optional

LOG = logging.getLogger("ai_debug_assistant")

# Session 级缓存：page_state_key -> vision_result
_vision_cache: dict = {}


def _discover_api_config() -> tuple[Optional[str], Optional[str], Optional[str]]:
    """自动发现 API key、base_url、model。

    优先级：
    1. ~/.claude/settings.json -> env.ANTHROPIC_AUTH_TOKEN + ANTHROPIC_BASE_URL + ANTHROPIC_MODEL
    2. ~/.workbuddy/models.json -> [0].apiKey + [0].url + [0].id
    3. 环境变量 ANTHROPIC_API_KEY / OPENAI_API_KEY
    4. 都没有 -> 返回 (None, None, None)

    Returns:
        (api_key, base_url, model) 或 (None, None, None)
    """
    # 1. Claude Code
    claude_settings = Path.home() / ".claude" / "settings.json"
    if claude_settings.exists():
        try:
            data = json.loads(claude_settings.read_text(encoding="utf-8"))
            env = data.get("env", {})
            api_key = env.get("ANTHROPIC_AUTH_TOKEN")
            base_url = env.get("ANTHROPIC_BASE_URL")
            model = env.get("ANTHROPIC_MODEL")
            if api_key and base_url:
                LOG.debug(f"  API config from Claude Code: {base_url}")
                return api_key, base_url, model
        except Exception as e:
            LOG.debug(f"  读取 Claude Code 配置失败: {e}")

    # 2. WorkBuddy
    workbuddy_models = Path.home() / ".workbuddy" / "models.json"
    if workbuddy_models.exists():
        try:
            data = json.loads(workbuddy_models.read_text(encoding="utf-8"))
            if data and isinstance(data, list) and len(data) > 0:
                first = data[0]
                api_key = first.get("apiKey")
                base_url = first.get("url")
                model = first.get("id")
                if api_key and base_url:
                    LOG.debug(f"  API config from WorkBuddy: {base_url}")
                    return api_key, base_url, model
        except Exception as e:
            LOG.debug(f"  读取 WorkBuddy 配置失败: {e}")

    # 3. 环境变量
    import os
    api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if api_key:
        base_url = "https://api.anthropic.com"
        model = "claude-3-5-sonnet-20241022"
        LOG.debug(f"  API config from environment variable")
        return api_key, base_url, model

    # 4. 都没有
    return None, None, None


async def _compute_page_state_key(page) -> str:
    """计算页面状态 key（URL + 弹窗标题 hash）。

    同一个 URL + 同一个弹窗组合 = 同一个 key，避免重复截图分析。

    Args:
        page: Playwright Page 对象

    Returns:
        str: 页面状态 key
    """
    url = page.url

    # 获取当前可见弹窗标题
    try:
        dialog_titles = await page.evaluate("""() => {
            const dialogs = document.querySelectorAll(
                '.el-dialog__wrapper, .el-drawer, .ant-modal-wrap');
            const visible = Array.from(dialogs).filter(
                d => d.style.display !== 'none' && d.offsetWidth > 0);
            return visible.map(d => {
                const title = d.querySelector(
                    '.el-dialog__title, .el-drawer__header, .ant-modal-title');
                return title ? title.textContent.trim() : '';
            }).filter(t => t);
        }""")
    except Exception:
        dialog_titles = []

    # 组合 URL + 弹窗标题，计算 hash
    state_str = f"{url}||{','.join(sorted(dialog_titles))}"
    state_hash = hashlib.md5(state_str.encode()).hexdigest()[:12]

    return f"{url[:80]}_{state_hash}"


async def _take_screenshot_base64(page) -> str:
    """截图并转为 base64 字符串。

    Args:
        page: Playwright Page 对象

    Returns:
        str: base64 编码的 PNG 图片
    """
    screenshot_bytes = await page.screenshot(type="png")
    return base64.b64encode(screenshot_bytes).decode("utf-8")


async def _call_vision_api(
    api_key: str,
    base_url: str,
    model: str,
    screenshot_b64: str,
    prompt: str
) -> dict:
    """调用 Vision API 分析截图。

    支持 OpenAI 兼容格式（大多数 API 网关）和 Anthropic 原生格式。

    Args:
        api_key: API key
        base_url: API base URL
        model: 模型名称
        screenshot_b64: base64 编码的 PNG 图片
        prompt: 分析提示词

    Returns:
        dict: Vision API 返回的分析结果
    """
    # 判断是 OpenAI 兼容格式还是 Anthropic 格式
    is_openai_format = "openai" in base_url.lower() or "/v1" in base_url

    if is_openai_format:
        # OpenAI 兼容格式（包括大多数 API 网关）
        url = f"{base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": prompt
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{screenshot_b64}"
                            }
                        }
                    ]
                }
            ],
            "max_tokens": 2000,
        }
    else:
        # Anthropic 原生格式
        url = f"{base_url}/v1/messages"
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "max_tokens": 2000,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": screenshot_b64,
                            }
                        },
                        {
                            "type": "text",
                            "text": prompt
                        }
                    ]
                }
            ]
        }

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        result = response.json()
        LOG.debug(f"  Vision API 原始响应: {json.dumps(result, ensure_ascii=False)[:1000]}")

    # 解析返回内容
    if is_openai_format:
        content = result["choices"][0]["message"]["content"]
    else:
        content = result.get("content", [{}])[0].get("text", "")

    # 空内容检查
    if not content or not content.strip():
        LOG.warning(f"  Vision API 返回空内容，模型可能未生成响应")
        LOG.debug(f"  完整响应结构: {json.dumps(result, ensure_ascii=False)[:500]}")
        return {"buttons": [], "form_fields": [], "page_state": {}, "anomalies": [], "diagnosis": "empty_response"}

    # 尝试解析 JSON
    try:
        # 提取 JSON 部分（可能被 markdown code block 包裹）
        json_str = content
        if "```json" in content:
            json_str = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            json_str = content.split("```")[1].split("```")[0].strip()

        # 如果内容看起来不像 JSON，尝试提取第一个 { 到最后一个 } 之间的内容
        if not json_str.startswith('{'):
            import re
            match = re.search(r'\{.*\}', json_str, re.DOTALL)
            if match:
                json_str = match.group(0)

        return json.loads(json_str)
    except Exception as e:
        LOG.warning(f"  Vision API 返回内容解析失败: {e}")
        LOG.warning(f"  原始内容（前 500 字符）: {content[:500]}")
        return {"buttons": [], "form_fields": [], "page_state": {}, "anomalies": [], "diagnosis": "parse_failed"}


async def ai_assisted_analysis(page, missing_elements: list, expected_context: Optional[str] = None) -> dict:
    """AI 辅助分析（Vision API 版本）。

    当 DOM 扫描无法找到目标元素时，使用多模态 LLM 分析页面截图。

    Args:
        page: Playwright Page 对象
        missing_elements: 缺失元素列表
        expected_context: 预期上下文描述（如 "预期打开'添加用户'对话框"）

    Returns:
        dict: {
            "found": bool,
            "elements": list,
            "source": str,
            "diagnosis": str
        }
    """
    # 1. 计算页面状态 key
    page_state_key = await _compute_page_state_key(page)

    # 2. 检查缓存
    if page_state_key in _vision_cache:
        LOG.info(f"  Vision 缓存命中: {page_state_key[:60]}")
        cached = _vision_cache[page_state_key]
        return {
            "found": cached.get("found", False),
            "elements": cached.get("elements", []),
            "source": "vision_cache",
            "diagnosis": cached.get("diagnosis", "cached")
        }

    # 3. 检查 API 配置
    api_key, base_url, model = _discover_api_config()
    if not api_key:
        LOG.warning("  Vision API 不可用：未找到 API key，降级为纯日志")
        return {
            "found": False,
            "elements": [],
            "source": "no_api_key",
            "diagnosis": "skipped"
        }

    LOG.info(f"  Vision API 分析: {page_state_key[:60]}")

    # 4. 截图
    try:
        screenshot_b64 = await _take_screenshot_base64(page)
    except Exception as e:
        LOG.error(f"  截图失败: {e}")
        return {
            "found": False,
            "elements": [],
            "source": "screenshot_failed",
            "diagnosis": "error"
        }

    # 5. 构建 prompt
    missing_desc = ", ".join([
        f"{e.get('type', 'button')}[{e.get('action', 'unknown')}]"
        for e in missing_elements[:10]
    ])

    prompt = f"""分析这张网页截图，完成以下任务：

1. **页面状态判断**：
   - 当前是否有弹窗/抽屉打开？标题是什么？
   - 页面是否处于正常状态（非加载中、非错误页）？

2. **交互元素识别**：
   - 列出所有可见的按钮（文本、大致位置：工具栏/行操作/弹窗内）
   - 列出所有可见的表单字段（标签文本、类型：输入框/下拉框/日期选择器/单选/复选）

3. **异常检测**：
   - 是否有遮罩层阻挡操作？
   - 是否有错误提示信息？
   - 是否有加载中的 spinner？

**特别关注**：{missing_desc}
**预期状态**：{expected_context or "无特定期望"}

请以 JSON 格式返回，结构如下：
```json
{{
  "page_state": {{
    "has_dialog": true/false,
    "dialog_title": "对话框标题（如有）",
    "page_status": "normal/loading/error"
  }},
  "buttons": [
    {{"text": "按钮文本", "location": "toolbar/row_action/dialog", "area": "位置描述"}}
  ],
  "form_fields": [
    {{"label": "字段标签", "type": "input/select/checkbox/radio/date"}}
  ],
  "anomalies": ["异常描述1", "异常描述2"],
  "diagnosis": "element_found/element_absent/page_state_mismatch"
}}
```

只返回 JSON，不要其他文字。"""

    # 6. 调用 Vision API
    try:
        vision_result = await _call_vision_api(api_key, base_url, model, screenshot_b64, prompt)
    except Exception as e:
        LOG.error(f"  Vision API 调用失败: {e}")
        return {
            "found": False,
            "elements": [],
            "source": "api_error",
            "diagnosis": "error"
        }

    # 7. 解析结果
    buttons = vision_result.get("buttons", [])
    form_fields = vision_result.get("form_fields", [])
    diagnosis = vision_result.get("diagnosis", "unknown")

    # 转换为 ui_result 格式
    elements = []
    for btn in buttons:
        elements.append({
            "text": btn.get("text", ""),
            "location": btn.get("location", "toolbar"),
            "source": "vision_api"
        })

    found = len(elements) > 0 or diagnosis == "element_found"

    # 8. 缓存结果
    cache_data = {
        "found": found,
        "elements": elements,
        "form_fields": form_fields,
        "diagnosis": diagnosis,
        "raw": vision_result
    }
    _vision_cache[page_state_key] = cache_data

    LOG.info(f"  Vision 分析完成: found={found}, elements={len(elements)}, diagnosis={diagnosis}")

    return {
        "found": found,
        "elements": elements,
        "source": "vision_api",
        "diagnosis": diagnosis
    }


def clear_vision_cache():
    """清空 Vision 缓存（用于测试）。"""
    global _vision_cache
    _vision_cache.clear()
