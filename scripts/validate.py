#!/usr/bin/env python3
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    sys.exit(1)


def main() -> None:
    main_py = ROOT / "plugin" / "main.py"
    ast.parse(main_py.read_text(encoding="utf-8"))
    print("OK main.py parses")

    prompts = sorted((ROOT / "plugin" / "prompts").glob("*.json"))
    if not prompts:
        fail("no prompt json files found")
    names = set()
    for path in prompts:
        data = json.loads(path.read_text(encoding="utf-8"))
        for key in ("style_name", "description", "system_prompt"):
            if not data.get(key):
                fail(f"{path.name} missing {key}")
        if data["style_name"] in names:
            fail(f"duplicate style_name: {data['style_name']}")
        names.add(data["style_name"])
    print(f"OK {len(prompts)} prompt files")

    example = json.loads((ROOT / "plugin" / "config_local.example.json").read_text(encoding="utf-8"))
    if "llm_api_url" not in example or "llm_model" not in example:
        fail("config example missing llm aliases")
    print("OK config example")

    metadata = (ROOT / "plugin" / "metadata.yaml").read_text(encoding="utf-8")
    meta_version = re.search(r"version:\s*([0-9.]+)", metadata)
    register_version = re.search(r'@register\([^)]*,\s*"([0-9.]+)"\)', main_py.read_text(encoding="utf-8"))
    if meta_version and register_version and meta_version.group(1) != register_version.group(1):
        fail(f"metadata version {meta_version.group(1)} != register version {register_version.group(1)}")
    print("OK metadata version")


if __name__ == "__main__":
    main()
