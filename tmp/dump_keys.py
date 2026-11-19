import json
from pathlib import Path

base = Path(r"D:\Mobile\API_AI_test\workspace\eis\kb\module_discovered")
ui = json.loads((base / "云主机-云主机_ui.json").read_text(encoding="utf-8"))
pb = json.loads((base / "云主机-云主机_playbook.json").read_text(encoding="utf-8"))

def dump_keys(obj, prefix="", depth=0, max_depth=3):
    if depth > max_depth or not isinstance(obj, dict):
        return
    for k, v in obj.items():
        t = type(v).__name__
        n = len(v) if isinstance(v, (list, dict)) else ""
        print(f"{prefix}{k} ({t}{f' ×{n}' if n else ''})")
        if isinstance(v, dict) and depth < max_depth:
            dump_keys(v, prefix + "  ", depth + 1, max_depth)
        elif isinstance(v, list) and v and depth < max_depth:
            print(f"{prefix}  [0] ({type(v[0]).__name__})")
            if isinstance(v[0], dict):
                dump_keys(v[0], prefix + "    ", depth + 1, max_depth)

print("=== ui.json 顶层结构 ===")
dump_keys(ui)
print()
print("=== playbook.json 顶层结构 ===")
dump_keys(pb)
