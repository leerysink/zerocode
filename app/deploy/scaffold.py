# app/deploy/scaffold.py
"""脚手架铺底（规格 01 §4.5 / 02）：复制模板 → 替换 {{KEY}} → 生成初始迁移 → 残留自检。"""
import re
from pathlib import Path
from app.deploy.errors import DeployError

PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
INIT_SQL = """-- D{TODAY}_01_seed.sql —— 数据轨种子（幂等，可重放）
-- 日期：{TODAY_DASH}  用途：demo_notes 欢迎行（AI 可替换或追加新 D 文件）  影响：demo_notes
INSERT INTO demo_notes (title) VALUES ('欢迎使用 {APP_NAME}') ON CONFLICT DO NOTHING;
"""
# 结构真相在 schema/schema.sql（模板自带、声明式快照）；脚手架只补数据轨种子。

def render(template: Path, dst: Path, mapping: dict) -> None:
    """把 template 目录渲染到 dst：占位符替换 + 生成初始迁移 + 残留自检。

    dst 已存在时覆盖合并（调用方需自行保证为空目录）。
    """
    if not template.is_dir():
        raise DeployError(f"栈模板不存在: {template}", status=500)
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
    init = dst / "sqlchange" / f"D{today}_01_seed.sql"
    init.parent.mkdir(parents=True, exist_ok=True)
    # M-2②（终审 F-03）：APP_NAME 进 SQL 字面量前做 PG 标准转义 `'`→`''`（对照探针：含引号名称
    # 原值渲染出引号不闭合的 INSERT，psql --single-transaction 整文件回滚 → 首预览必红且 M1 无删除入口）。
    init.write_text(INIT_SQL.format(
        TODAY=today,
        TODAY_DASH=f"{today[:4]}-{today[4:6]}-{today[6:]}",
        APP_NAME=str(mapping["APP_NAME"]).replace("'", "''")), encoding="utf-8", newline="")
    # 残留自检：任何文件不得残留 {{
    for p in dst.rglob("*"):
        if p.is_file() and "{{" in p.read_text(encoding="utf-8", errors="replace"):
            raise DeployError(f"占位符渲染残留: {p.relative_to(dst)}")
