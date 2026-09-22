#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
对比两个 UI 测试报告的截图
"""
import base64
import sys
import os
from pathlib import Path
from bs4 import BeautifulSoup

# 设置 UTF-8 编码
if sys.platform == 'win32':
    os.environ['PYTHONIOENCODING'] = 'utf-8'

def extract_screenshots(html_path):
    """从 HTML 报告中提取所有 base64 截图"""
    with open(html_path, 'r', encoding='utf-8') as f:
        soup = BeautifulSoup(f, 'html.parser')

    images = soup.find_all('img')
    screenshots = []

    for img in images:
        src = img.get('src', '')
        if src.startswith('data:image'):
            # 提取 base64 数据
            b64_data = src.split(',', 1)[1]
            screenshots.append(b64_data)

    return screenshots

def decode_png_dimensions(b64_data):
    """解码 PNG 图片的尺寸"""
    try:
        img_bytes = base64.b64decode(b64_data)
        # PNG 头部包含尺寸信息 (bytes 16-19: width, 20-23: height)
        if len(img_bytes) >= 24 and img_bytes[:8] == b'\x89PNG\r\n\x1a\n':
            width = int.from_bytes(img_bytes[16:20], 'big')
            height = int.from_bytes(img_bytes[20:24], 'big')
            return width, height
    except:
        pass
    return None, None

def compare_reports(current_path, reference_path):
    """对比两个报告的截图"""
    print("=" * 70)
    print("UI 测试报告截图对比")
    print("=" * 70)

    # 提取当前报告的截图
    print(f"\n[当前报告] {Path(current_path).name}")
    current_screenshots = extract_screenshots(current_path)
    print(f"   截图数量: {len(current_screenshots)}")

    # 提取参考报告的截图
    print(f"\n[参考报告] {Path(reference_path).name}")
    reference_screenshots = extract_screenshots(reference_path)
    print(f"   截图数量: {len(reference_screenshots)}")

    print("\n" + "=" * 70)
    print("详细对比")
    print("=" * 70)

    # 对比截图数量
    if len(current_screenshots) != len(reference_screenshots):
        print(f"\n[警告] 截图数量不一致: 当前 {len(current_screenshots)} vs 参考 {len(reference_screenshots)}")

    # 对比每张截图
    max_count = max(len(current_screenshots), len(reference_screenshots))

    for i in range(max_count):
        print(f"\n[截图 #{i+1}]")
        print("-" * 70)

        # 当前报告
        if i < len(current_screenshots):
            curr_b64 = current_screenshots[i]
            curr_size = len(base64.b64decode(curr_b64)) / 1024  # KB
            curr_w, curr_h = decode_png_dimensions(curr_b64)
            print(f"   当前: {curr_size:.1f} KB", end="")
            if curr_w and curr_h:
                print(f", 尺寸: {curr_w}x{curr_h}")
            else:
                print()
        else:
            print("   当前: [无此截图]")

        # 参考报告
        if i < len(reference_screenshots):
            ref_b64 = reference_screenshots[i]
            ref_size = len(base64.b64decode(ref_b64)) / 1024  # KB
            ref_w, ref_h = decode_png_dimensions(ref_b64)
            print(f"   参考: {ref_size:.1f} KB", end="")
            if ref_w and ref_h:
                print(f", 尺寸: {ref_w}x{ref_h}")
            else:
                print()
        else:
            print("   参考: [无此截图]")

        # 对比
        if i < len(current_screenshots) and i < len(reference_screenshots):
            curr_b64 = current_screenshots[i]
            ref_b64 = reference_screenshots[i]

            if curr_b64 == ref_b64:
                print("   状态: [完全一致]")
            else:
                curr_size = len(base64.b64decode(curr_b64))
                ref_size = len(base64.b64decode(ref_b64))
                size_diff = abs(curr_size - ref_size) / ref_size * 100
                print(f"   状态: [内容不同] (大小差异: {size_diff:.1f}%)")

    print("\n" + "=" * 70)

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("用法: python compare_screenshots.py <当前报告.html> <参考报告.html>")
        sys.exit(1)

    current_path = sys.argv[1]
    reference_path = sys.argv[2]

    compare_reports(current_path, reference_path)
