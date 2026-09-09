"""
pre_api_merger.py — 跨模块前置 API 合并

将多个模块独立发现的前置 API 去重合并为项目级共享配置。

输入: 各模块 manifest 中的 pre_apis 数组
输出:
  - kb/pre_apis_discovered.json  (原始发现结果)
  - scripts/.../pre_apis_config.json  (运行时最终配置)

去重策略: 相同 method + pathname = 同一个前置 API
字段合并: 所有模块提取字段的并集
"""

import json
import logging
from pathlib import Path
from typing import List, Dict, Optional

LOG = logging.getLogger("pre_api_merger")


def merge_pre_apis_across_modules(project_dir: Path, module_names: list,
                                   version: str = "v1.0.0") -> dict:
    """
    合并多个模块的前置 API，生成项目级共享配置。

    Args:
        project_dir: 项目目录 (如 projects/ecm-compute/)
        module_names: 模块名称列表
        version: 脚本版本号

    Returns:
        合并后的配置 dict，同时写入磁盘文件
    """
    kb_dir = project_dir / "kb" / "module_discovered"
    all_pre_apis = []  # 收集所有模块的 pre_apis
    source_map = {}     # (method, pathname) -> [module_names]

    for module_name in module_names:
        manifest_path = kb_dir / f"{module_name}_manifest.json"
        if not manifest_path.exists():
            LOG.debug(f"  {module_name}: manifest 不存在，跳过")
            continue

        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as e:
            LOG.warning(f"  {module_name}: manifest 读取失败: {e}")
            continue

        pre_apis = manifest.get("pre_apis", [])
        if not pre_apis:
            LOG.debug(f"  {module_name}: 无前置 API")
            continue

        LOG.info(f"  {module_name}: 发现 {len(pre_apis)} 个前置 API")

        for pre_api in pre_apis:
            method = pre_api.get("method", "GET")
            pathname = pre_api.get("pathname", "")
            key = (method, pathname)

            # 记录来源
            if key not in source_map:
                source_map[key] = []
            source_map[key].append(module_name)

            all_pre_apis.append(pre_api)

    if not all_pre_apis:
        LOG.info("[Merger] 所有模块均无前置 API，跳过合并")
        return {}

    # 去重合并
    merged = _deduplicate_pre_apis(all_pre_apis, source_map)

    LOG.info(f"[Merger] 合并完成: {len(all_pre_apis)} 个 → {len(merged)} 个去重后的前置 API")

    # 构建输出
    import time
    discovered = {
        "discovery_time": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source_modules": module_names,
        "pre_apis": merged,
        "global_context_fields": _collect_global_fields(merged),
    }

    # 写入 kb/pre_apis_discovered.json
    kb_base = project_dir / "kb"
    kb_base.mkdir(parents=True, exist_ok=True)
    discovered_path = kb_base / "pre_apis_discovered.json"
    discovered_path.write_text(
        json.dumps(discovered, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
    LOG.info(f"[Merger] 已写入 {discovered_path}")

    # 生成运行时配置 (精简版，不含 response_sample)
    runtime_config = _build_runtime_config(discovered)

    # 尝试合并人工配置
    yaml_path = project_dir / "pre_apis.yaml"
    if yaml_path.exists():
        manual = _load_yaml_pre_apis(yaml_path)
        if manual:
            runtime_config = _merge_configs(runtime_config, manual)
            LOG.info(f"[Merger] 已合并人工配置 {yaml_path.name}")

    # 写入 scripts/.../pre_apis_config.json
    scripts_dir = project_dir / "scripts" / version / "api"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    config_path = scripts_dir / "pre_apis_config.json"
    config_path.write_text(
        json.dumps(runtime_config, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
    LOG.info(f"[Merger] 已写入 {config_path}")

    return discovered


def _deduplicate_pre_apis(all_pre_apis: list, source_map: dict) -> list:
    """
    按 (method, pathname) 去重，合并 extracts 字段。

    同一个 API 可能被多个模块发现，各自提取了不同的字段。
    合并策略：取所有模块提取字段的并集。
    """
    seen = {}  # (method, pathname) -> merged pre_api dict

    for pre_api in all_pre_apis:
        method = pre_api.get("method", "GET")
        pathname = pre_api.get("pathname", "")
        key = (method, pathname)

        if key in seen:
            # 合并 extracts（按 name 去重）
            existing_names = {e["name"] for e in seen[key].get("extracts", [])}
            for extract in pre_api.get("extracts", []):
                if extract.get("name") not in existing_names:
                    seen[key].setdefault("extracts", []).append(extract)
                    existing_names.add(extract.get("name"))

            # 合并 discovered_from
            modules = source_map.get(key, [])
            seen[key]["discovered_from"] = list(set(
                seen[key].get("discovered_from", []) + modules
            ))
        else:
            merged = {
                "id": pre_api.get("id", ""),
                "name": pre_api.get("name", ""),
                "method": method,
                "pathname": pathname,
                "extracts": pre_api.get("extracts", []),
                "body_template": pre_api.get("body_template", {}),
                "query_params": pre_api.get("query_params", {}),
                "depends_on": pre_api.get("depends_on", []),
                "discovered_from": source_map.get(key, []),
            }
            seen[key] = merged

    return list(seen.values())


def _collect_global_fields(merged_apis: list) -> list:
    """收集所有被多个模块使用的全局字段名"""
    field_usage = {}  # field_name -> count of modules using it

    for pre_api in merged_apis:
        for extract in pre_api.get("extracts", []):
            name = extract.get("name", "")
            used_by = extract.get("used_by", [])
            if used_by:
                # used_by 是 step actions，粗略按模块计数
                field_usage[name] = field_usage.get(name, 0) + 1
            else:
                field_usage[name] = field_usage.get(name, 0) + 1

    # 被 2+ 个前置 API 提取的字段视为全局
    return [name for name, count in field_usage.items() if count >= 1]


def _build_runtime_config(discovered: dict) -> dict:
    """从发现结果构建运行时配置（精简版，不含 response_sample）"""
    runtime_apis = []
    for pre_api in discovered.get("pre_apis", []):
        runtime_apis.append({
            "id": pre_api["id"],
            "name": pre_api["name"],
            "method": pre_api["method"],
            "pathname": pre_api["pathname"],
            "extracts": pre_api.get("extracts", []),
            "body_template": pre_api.get("body_template", {}),
            "query_params": pre_api.get("query_params", {}),
            "depends_on": pre_api.get("depends_on", []),
        })

    return {
        "version": "1.0",
        "pre_apis": runtime_apis,
        "global_context_fields": discovered.get("global_context_fields", []),
    }


def _load_yaml_pre_apis(yaml_path: Path) -> Optional[dict]:
    """加载人工编辑的 pre_apis.yaml"""
    try:
        import yaml
        with open(yaml_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except ImportError:
        LOG.warning("[Merger] PyYAML 未安装，跳过人工配置")
        return None
    except Exception as e:
        LOG.warning(f"[Merger] pre_apis.yaml 解析失败: {e}")
        return None


def _merge_configs(auto: dict, manual: dict) -> dict:
    """
    合并自动发现和人工配置。

    人工配置优先级更高：
    - 人工定义的 pre_api 覆盖自动发现的同名 API
    - 人工定义的 extracts 覆盖自动发现的
    - 人工定义的 global_context_fields 追加到自动发现的
    """
    if not manual:
        return auto

    manual_apis = {api["id"]: api for api in manual.get("pre_apis", [])}
    auto_apis = {api["id"]: api for api in auto.get("pre_apis", [])}

    # 人工覆盖自动发现
    for api_id, api in manual_apis.items():
        auto_apis[api_id] = api

    merged_apis = list(auto_apis.values())

    # 合并 global_context_fields
    auto_fields = set(auto.get("global_context_fields", []))
    manual_fields = set(manual.get("global_context_fields", []))
    merged_fields = list(auto_fields | manual_fields)

    return {
        "version": auto.get("version", "1.0"),
        "pre_apis": merged_apis,
        "global_context_fields": merged_fields,
    }
