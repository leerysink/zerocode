# app/store/categories.py
"""商店分类字典（ADR-0061 §4）：分类的展示名、顺序与启停归运营，条目上仍只存 key。

为什么关系不改成外键：`app_release.category` 是 VARCHAR(20)，现存 5 个值的 key 就是中文 label。
改 `category_id` 要回填存量、动商店筛选与打包链路，换来的只有数据库层的引用完整性——
而这份完整性在写入处查一次就够（`remove` 与 `normalize` 就是那两次查询）。

「其他」是代码层兜底值：未设置、或指向已停用分类时都回落到它，所以它在字典里锁死——
不可停用、不可删除、不可改 key。
"""
from sqlalchemy import text
from app.deploy.errors import DeployError

DEFAULT_KEY = "其他"
# key 的宽度上限来自被引用列 app_release.category VARCHAR(20)，不是本表自己的选择
KEY_MAX = 20
LABEL_MAX = 40


async def all_rows(sf) -> list[dict]:
    """全量字典行（含停用）：管理侧要看到停用的分类还挂着多少存量条目。"""
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT c.key, c.label, c.sort, c.enabled, "
            "(SELECT count(*) FROM app_release r WHERE r.category = c.key) AS release_count, "
            "(SELECT count(*) FROM app_release r WHERE r.category = c.key "
            " AND r.status = 'published') AS published_count "
            "FROM store_category c ORDER BY c.sort, c.key"))).mappings().all()
    return [dict(r) for r in rows]


async def enabled_keys(sf) -> list[str]:
    async with sf() as s:
        return [r["key"] for r in (await s.execute(text(
            "SELECT key FROM store_category WHERE enabled ORDER BY sort, key"))).mappings().all()]


async def all_keys(sf) -> list[str]:
    async with sf() as s:
        return [r["key"] for r in (await s.execute(
            text("SELECT key FROM store_category"))).mappings().all()]


async def normalize_filter(sf, value: str | None) -> str:
    """发现页的分类过滤口径：停用分类也认。

    与 `normalize`（写入用，只认启用项）刻意不同——存量条目还挂着停用分类，
    用户从旧链接或书签进来不该吃 400。
    """
    cat = (value or "").strip()
    if not cat:
        return DEFAULT_KEY
    if cat not in set(await all_keys(sf)):
        raise DeployError(f"分类不存在：{cat}", status=400)
    return cat


async def normalize(sf, value: str | None, *, allow: str | None = None) -> str:
    """把用户给的值归一成可用 key：空值落「其他」，不在启用面内一律 400。

    `allow` 传条目当前已持有的 key：分类被停用后，存量条目仍该能被重新保存，
    否则运营一停用，那些条目的标题就再也改不动了。
    """
    cat = (value or "").strip()
    if not cat:
        return DEFAULT_KEY
    keys = set(await enabled_keys(sf))
    if allow:
        keys.add(allow)
    if cat not in keys:
        raise DeployError(f"分类不存在或已停用：{cat}（可选：{'/'.join(sorted(keys))}）", status=400)
    return cat


async def create(sf, *, key: str, label: str = "", sort: int = 0) -> dict:
    k = (key or "").strip()
    if not k:
        raise DeployError("分类标识不能为空", status=400)
    if len(k) > KEY_MAX:
        raise DeployError(f"分类标识最长 {KEY_MAX} 字（受 app_release.category 列宽约束）", status=400)
    lb = (label or "").strip() or k
    if len(lb) > LABEL_MAX:
        raise DeployError(f"分类名称最长 {LABEL_MAX} 字", status=400)
    async with sf() as s:
        try:
            row = (await s.execute(text(
                "INSERT INTO store_category (key, label, sort, enabled) "
                "VALUES (:k, :l, :o, true) "
                "RETURNING key, label, sort, enabled"),
                {"k": k, "l": lb, "o": int(sort or 0)})).mappings().first()
            await s.commit()
        except Exception as e:  # 主键冲突：撞「其他」或撞已存在的分类
            await s.rollback()
            if "duplicate key" in str(e).lower():
                raise DeployError(f"分类标识已存在：{k}", status=409) from None
            raise
    return dict(row)


async def update(sf, *, key: str, label: str | None = None, sort: int | None = None,
                 enabled: bool | None = None) -> dict:
    """改名称/排序/启停。key 本身不改：它是条目里存的值，改 key 等于批量改存量条目。"""
    async with sf() as s:
        row = (await s.execute(text(
            "SELECT key, label, enabled FROM store_category WHERE key = :k"),
            {"k": key})).mappings().first()
        if not row:
            raise DeployError(f"分类不存在：{key}", status=404)
        if key == DEFAULT_KEY and enabled is False:
            raise DeployError(f"「{DEFAULT_KEY}」是兜底分类，不能停用", status=400)
        patch, params = {}, {"k": key}
        if label is not None:
            lb = label.strip()
            if not lb:
                raise DeployError("分类名称不能为空", status=400)
            if len(lb) > LABEL_MAX:
                raise DeployError(f"分类名称最长 {LABEL_MAX} 字", status=400)
            patch["label"] = lb
            params["label"] = lb
        if sort is not None:
            patch["sort"] = int(sort)
            params["sort"] = int(sort)
        if enabled is not None:
            patch["enabled"] = bool(enabled)
            params["enabled"] = bool(enabled)
        if not patch:
            raise DeployError("没有要改的字段", status=400)
        sets = ", ".join(f"{c} = :{c}" for c in patch)  # 列名与绑定名都取自本函数的补丁表，非用户输入
        saved = (await s.execute(text(
            f"UPDATE store_category SET {sets} WHERE key = :k "
            "RETURNING key, label, sort, enabled"), params)).mappings().first()
        await s.commit()
    return dict(saved)


async def remove(sf, *, key: str) -> dict:
    """真删只放开给没有任何条目引用的分类；有引用只能停用（enabled=false）。"""
    if key == DEFAULT_KEY:
        raise DeployError(f"「{DEFAULT_KEY}」是兜底分类，不能删除", status=400)
    async with sf() as s:
        row = (await s.execute(text(
            "SELECT (SELECT count(*) FROM app_release r WHERE r.category = :k) AS n "
            "FROM store_category c WHERE c.key = :k"), {"k": key})).mappings().first()
        if not row:
            raise DeployError(f"分类不存在：{key}", status=404)
        if row["n"]:
            raise DeployError(
                f"「{key}」下还挂着 {row['n']} 个版本，不能删除；如要让它不再出现在上架选项里，"
                "请改为停用", status=409)
        await s.execute(text("DELETE FROM store_category WHERE key = :k"), {"k": key})
        await s.commit()
    return {"ok": True, "key": key}
