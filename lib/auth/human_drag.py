"""
human_drag.py — 人类化拖拽模拟

模拟真人滑动验证码操作，规避滑块风控。
移植自 login-kit 的 humanDrag.js，被 EcsCloud 和 BCov 策略共用。

算法：ease-in-out 三次缓动 + Y 轴随机抖动 + 末端轻微过冲
"""

import asyncio
import random
import math


async def human_like_drag(page, from_x: float, from_y: float,
                          to_x: float, to_y: float, **opts):
    """人类化缓动拖拽

    Args:
        page: Playwright Page 对象
        from_x: 起点 X
        from_y: 起点 Y
        to_x: 终点 X
        to_y: 终点 Y
        **opts:
            steps (int): 步数，默认 30
            y_jitter (float): Y 轴抖动幅度，默认 1.5
            overshoot (float): 过冲距离，默认 0
            pre_delay (float): 鼠标按下前延迟（秒），默认 0.12
            post_delay (float): 鼠标抬起后延迟（秒），默认 0.12
            step_min (float): 步间最小延迟（秒），默认 0.007
            step_max (float): 步间最大延迟（秒），默认 0.015
    """
    steps = opts.get("steps", 30)
    y_jitter = opts.get("y_jitter", 1.5)
    overshoot = opts.get("overshoot", 0)
    pre_delay = opts.get("pre_delay", 0.12)
    post_delay = opts.get("post_delay", 0.12)
    step_min = opts.get("step_min", 0.007)
    step_max = opts.get("step_max", 0.015)

    # 移动到起点
    await page.mouse.move(from_x, from_y)
    await asyncio.sleep(pre_delay)

    # 按下鼠标
    await page.mouse.down()
    await asyncio.sleep(0.10 + random.random() * 0.08)

    # 缓动拖拽
    for i in range(1, steps + 1):
        t = i / steps
        # ease-in-out 三次缓动
        eased = 4 * t * t * t if t < 0.5 else 1 - ((-2 * t + 2) ** 3) / 2
        x = from_x + (to_x - from_x) * eased
        y = from_y + (to_y - from_y) * eased + (random.random() - 0.5) * y_jitter
        await page.mouse.move(x, y)
        await asyncio.sleep(step_min + random.random() * (step_max - step_min))

    # 过冲（模拟真人滑动惯性）
    if overshoot > 0:
        await page.mouse.move(
            to_x + overshoot,
            to_y + (random.random() - 0.5) * 1.2
        )
        await asyncio.sleep(0.07 + random.random() * 0.04)
        await page.mouse.move(
            to_x,
            to_y + (random.random() - 0.5) * 0.6
        )
        await asyncio.sleep(post_delay)

    # 抬起鼠标
    await page.mouse.up()


async def human_drag_v2(page, handle_box: dict, distance: float):
    """EcsCloud 风格的拟人化拖拽（移植自 slider.py）

    与 human_like_drag 的区别：
    - 从 handle_box 中心点开始
    - 使用 sin 曲线 Y 抖动（更自然）
    - 步间延迟根据进度动态调整（起步/结尾慢，中间快）
    - 固定过冲 1.5-3.0 px

    Args:
        page: Playwright Page 对象
        handle_box: 滑块手柄位置 {"x": ..., "y": ..., "w": ..., "h": ...}
        distance: 拖拽距离（像素）
    """
    cx = handle_box["x"] + handle_box["w"] / 2
    cy = handle_box["y"] + handle_box["h"] / 2

    # 移动到手柄中心
    await page.mouse.move(cx, cy, steps=6)
    await asyncio.sleep(0.18 + random.random() * 0.12)

    # 按下鼠标
    await page.mouse.down()
    await asyncio.sleep(0.12 + random.random() * 0.08)

    # 缓动拖拽（28-37 步）
    total = 28 + random.randint(0, 9)
    for s in range(1, total + 1):
        p = s / total
        # ease-in-out 三次缓动
        eased = p ** 3 * 4 if p < 0.5 else 1 - ((-2 * p + 2) ** 3) / 2
        # sin 曲线 Y 抖动
        y_jit = math.sin(p * math.pi * (2 + random.random())) * (1.2 + random.random() * 1.5)
        await page.mouse.move(cx + eased * distance, cy + y_jit)
        # 动态延迟：起步和结尾慢，中间快
        if p < 0.12:
            delay = 22 + random.random() * 14
        elif p > 0.88:
            delay = 26 + random.random() * 16
        else:
            delay = 7 + random.random() * 9
        await asyncio.sleep(delay / 1000.0)

    # 过冲
    overshoot = 1.5 + random.random() * 1.5
    await page.mouse.move(
        cx + distance + overshoot,
        cy + (random.random() - 0.5) * 1.2
    )
    await asyncio.sleep(0.07 + random.random() * 0.04)
    await page.mouse.move(
        cx + distance,
        cy + (random.random() - 0.5) * 0.6
    )
    await asyncio.sleep(0.14 + random.random() * 0.08)

    # 抬起鼠标
    await page.mouse.up()
