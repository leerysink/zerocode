"""平台保留区写保护（规格 01 §4.2 / 02 FR2-05）：app.json 与 deploy/** 平台唯一写入方。"""
from pathlib import Path

def is_protected(root: Path, rel: str) -> bool:
    if not (root / "app.json").is_file():
        return False
    norm = str(rel).replace("\\", "/").lstrip("/")
    return norm == "app.json" or norm.startswith("deploy/")
