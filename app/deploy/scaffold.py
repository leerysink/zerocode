# app/deploy/scaffold.py
"""脚手架铺底（规格 01 §4.5 / 02）：复制模板 → 替换 {{KEY}} → 生成初始迁移 → 残留自检。"""
import datetime, re, shutil
from pathlib import Path
from app.deploy.errors import DeployError

PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
INIT_SQL = """-- V{TODAY}_01_init.sql
-- 日期：{TODAY_DASH}  用途：示例表 demo_notes（AI 可按需求替换或追加新文件）  影响：demo_notes

CREATE TABLE IF NOT EXISTS demo_notes (
    id         BIGSERIAL PRIMARY KEY,
    title      VARCHAR(200) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO demo_notes (title) VALUES ('欢迎使用 {APP_NAME}') ON CONFLICT DO NOTHING;
"""

def render(template: Path, dst: Path, mapping: dict) -> None:
    required = {"SLUG", "APP_NAME", "DEV_PORT", "PROD_PORT", "DEV_DB", "PROD_DB", "TODAY"}
    missing = required - set(mapping)
    if missing:
        raise DeployError(f"模板渲染缺少占位值: {sorted(missing)}")
    dst.mkdir(parents=True, exist_ok=True)
    for src in sorted(template.rglob("*")):
        rel = src.relative_to(template)
        target = dst / rel
        if src.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        text = src.read_text(encoding="utf-8")
        text = PLACEHOLDER.sub(lambda m: str(mapping[m.group(1)]), text)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="")
    today = mapping["TODAY"]
    init = dst / "sqlchange" / f"V{today}_01_init.sql"
    init.parent.mkdir(parents=True, exist_ok=True)
    init.write_text(INIT_SQL.format(
        TODAY=today,
        TODAY_DASH=f"{today[:4]}-{today[4:6]}-{today[6:]}",
        APP_NAME=mapping["APP_NAME"]), encoding="utf-8", newline="")
    # 残留自检：任何文件不得残留 {{
    for p in dst.rglob("*"):
        if p.is_file() and "{{" in p.read_text(encoding="utf-8", errors="replace"):
            raise DeployError(f"占位符渲染残留: {p.relative_to(dst)}")
