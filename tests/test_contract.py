import json
from pathlib import Path
from app.deploy.contract import build_app_contract
from app.agent.prompts import build_system_prompt

def test_contract_present(tmp_path):
    meta = {"schema": "zc-app/1", "name": "来料登记", "slug": "lailiao", "scale": "small",
            "stack": "vue3-fastapi", "ports": {"prod": 8210, "dev": 8211},
            "database": {"dev": {"name": "lailiao_dev", "user": "lailiao_dev"},
                         "prod": {"name": "lailiao_prod", "user": "lailiao_prod"}},
            "health": {"frontend": "/", "backend": "/api/health"}}
    (tmp_path / "app.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    c = build_app_contract(tmp_path)
    assert c and "lailiao" in c and "8211" in c and "sqlchange" in c and "deploy/" in c
    p = build_system_prompt(str(tmp_path), "2026-09-18", app_contract=c)
    assert "【应用契约】" in p

def test_contract_absent(tmp_path):
    assert build_app_contract(tmp_path) is None
    p = build_system_prompt(str(tmp_path), "2026-09-18")
    assert "【应用契约】" not in p

def test_contract_broken_json(tmp_path):
    (tmp_path / "app.json").write_text("{oops", encoding="utf-8")
    assert build_app_contract(tmp_path) is None   # 损坏不注入，不抛异常

def test_contract_malformed_forms_never_raise(tmp_path):
    """异常形态一律静默：合法 JSON 但顶层非对象 / 空文件 / 非法 JSON → None，不抛。"""
    for bad in ("[1,2]", "", "{oops"):
        (tmp_path / "app.json").write_text(bad, encoding="utf-8")
        assert build_app_contract(tmp_path) is None   # 任一形态都不向聊天链路冒泡异常

def test_contract_lists_databases(tmp_path):
    """契约正文补齐库事实：dev/prod 库名与用户（app.json 本无密码）。"""
    meta = {"schema": "zc-app/1", "name": "来料登记", "slug": "lailiao", "scale": "small",
            "stack": "vue3-fastapi", "ports": {"prod": 8210, "dev": 8211},
            "database": {"dev": {"name": "lailiao_dev", "user": "lailiao_dev"},
                         "prod": {"name": "lailiao_prod", "user": "lailiao_prod"}},
            "health": {"frontend": "/", "backend": "/api/health"}}
    (tmp_path / "app.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    c = build_app_contract(tmp_path)
    assert c and "lailiao_dev" in c and "lailiao_prod" in c
    assert "用户 lailiao_dev" in c and "用户 lailiao_prod" in c

VALID_META = {"schema": "zc-app/1", "name": "来料登记", "slug": "lailiao", "scale": "small",
              "stack": "vue3-fastapi", "ports": {"prod": 8210, "dev": 8211},
              "database": {"dev": {"name": "lailiao_dev", "user": "lailiao_dev"},
                           "prod": {"name": "lailiao_prod", "user": "lailiao_prod"}},
              "health": {"frontend": "/", "backend": "/api/health"}}

def test_contract_gate_requires_zc_app_manifest(tmp_path):
    """M-1 准入闸门收紧：非 zc-app/1 清单（含外来 app.json）或缺 slug → 不注入，杜绝占位文本。"""
    foreign = {"name": "小程序", "pages": ["pages/index"]}   # 小程序/Electron 同名配置，无 schema
    (tmp_path / "app.json").write_text(json.dumps(foreign, ensure_ascii=False), encoding="utf-8")
    assert build_app_contract(tmp_path) is None
    for tampered in ({**VALID_META, "schema": "zc-app/2"},      # 版本漂移不盲渲染
                     {k: v for k, v in VALID_META.items() if k != "schema"},   # 缺清单标识
                     {**VALID_META, "slug": ""}):               # slug 空 → 不再出「」（，…）占位
        (tmp_path / "app.json").write_text(json.dumps(tampered, ensure_ascii=False), encoding="utf-8")
        assert build_app_contract(tmp_path) is None
    (tmp_path / "app.json").write_text(json.dumps(VALID_META, ensure_ascii=False), encoding="utf-8")
    assert build_app_contract(tmp_path)          # 对照：合法清单照常注入，闸门不误伤

def test_contract_sql_and_db_fact_bans(tmp_path):
    """I-2/M-2 文本锚 + P2 双轨口径：第 3 条含单事务建索引禁令；库事实段含库名/用户引用禁令。"""
    (tmp_path / "app.json").write_text(json.dumps(VALID_META, ensure_ascii=False), encoding="utf-8")
    c = build_app_contract(tmp_path)
    assert "单事务内执行" in c and "CREATE INDEX IF NOT EXISTS" in c \
        and "不得使用 CREATE INDEX CONCURRENTLY" in c
    assert "仅供理解，代码与 SQL 不得引用库名/用户名" in c


def test_contract_states_dual_track_model(tmp_path):
    """P2 双轨口径写入契约：结构真相走 schema/schema.sql 目标态、数据变更走 sqlchange/D*。"""
    (tmp_path / "app.json").write_text(json.dumps(VALID_META, ensure_ascii=False), encoding="utf-8")
    c = build_app_contract(tmp_path)
    assert "schema/schema.sql" in c and "sqlchange/D" in c
    assert "数据库变更走双轨" in c and "破坏性" in c


def test_contract_states_three_segment_data_track(tmp_path):
    """ADR-0040：数据轨三段 + 参考/演示划分 + 写 prod 段前先查线上，都得进契约。

    三段是本需求的执行口径，AI 是唯一的写入方——契约里没写就等于「靠人记得」。
    """
    (tmp_path / "app.json").write_text(json.dumps(VALID_META, ensure_ascii=False), encoding="utf-8")
    c = build_app_contract(tmp_path)
    for kw in ("sqlchange/dev/", "sqlchange/prod/", "公共段", "参考数据", "演示数据", "db_query"):
        assert kw in c, kw
    assert "同名合法" in c and "记账" in c      # 记账键改成相对路径后，两段同名不再被判篡改
    assert "线上必须有" in c                  # 参考数据的强约束不能被写成建议


META_SB = {**VALID_META, "scale": "large", "stack": "vue3-springboot",
           "database": {"dev": {"name": "bigapp_dev", "user": "bigapp_dev"},
                        "prod": {"name": "bigapp_prod", "user": "bigapp_prod"}}}


def _contract(tmp_path, meta):
    (tmp_path / "app.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return build_app_contract(tmp_path)


def test_contract_large_stack_carries_baseplate_rules(tmp_path):
    """大型栈多出 8)–11)：三件套 / 不重写 /api / 基座不可重写 / sys_* 不可改形。"""
    c = _contract(tmp_path, META_SB)
    assert c and "8)" in c and "11)" in c
    assert "三件套" in c and "sys_role_menu" in c
    assert "菜单是参考数据" in c                    # ADR-0040：栈专属口径——菜单走顶层公共段，两库同得
    assert "不要再手写一次 /api 前缀" in c
    assert "基座类" in c and "只增不改" in c
    assert "sys_* 表不可改名" in c and "GENERATED BY DEFAULT AS IDENTITY" in c
    assert "admin 口令哈希" in c and "admin123" in c
    assert "SPRING_DATASOURCE_URL" in c            # ENV_HINT 仍指对方向
    assert "Redis 与 JWT_SECRET、UPLOAD_DIR 已由平台注入" in c   # ENV_HINT 新句正向钉
    assert "@PreAuthorize" in c and "@RequiresPermission" not in c  # 10) 指真实存在的注解，不教 AI 写编译不过的符号
    assert "只对「新登录」生效" in c and "退出重登" in c  # 10) 补：数据轨权限不刷旧会话，堵住「为绕 403 去改 SecurityConfig」的失败路径
    assert "\n9)" in c and "\n10)" in c and "\n11)" in c          # 8)–11) 各自成行，与 1)–7) 同层级


def test_contract_small_stack_text_byte_identical(tmp_path):
    """回归钉：fastapi 栈的契约文本不得多出半个字（新段只跟大型栈走）。"""
    c = _contract(tmp_path, VALID_META)
    assert c and "8)" not in c and "三件套" not in c and "sys_menu" not in c
    assert c.rstrip().endswith("正式使用请点「发布」。")     # 1)–7) 尾行未变
    assert "已由平台注入，代码不得自行拼接或硬编码" not in c   # 负向 pin 用真实子串（springboot 专属那句不得漏进小栈）
    assert "退出重登" not in c                                  # 10) 补的 LoginUser 缓存句只跟大型栈走


META_MOBILE = {**META_SB, "stack": "vue3-springboot-mobile"}


def test_mobile_contract_is_the_large_contract_plus_the_mobile_slice(tmp_path):
    """移动栈 = 大栈文案逐字节 + 尾部追加的移动端段（规格 §5：大栈文案不变）。

    除栈标签这一处替换外，两段文本必须完全相等：往共用段或大栈段里顺手塞一句，
    会同时改变所有已生成应用的提示词，而症状只会以「Agent 行为变了」的形式出现。
    """
    from app.deploy import contract
    large = _contract(tmp_path, META_SB)
    mob = _contract(tmp_path, META_MOBILE)
    extra = contract.STACK_EXTRA[contract.MOBILE_STACK]
    base = contract.STACK_EXTRA["vue3-springboot"]
    assert extra.startswith(base)                                       # 基座 8)–11) 一条不少
    mobile_only = extra[len(base):]
    assert mob.endswith(mobile_only)                                     # 移动端段只在尾部追加
    assert mob[:-len(mobile_only)] == large.replace(
        "Vue3 + Spring Boot 3，", "Vue3 + Spring Boot 3 + uni-app 移动端，")


def test_contract_mobile_stack_states_the_uni_app_conventions(tmp_path):
    """US-04 的判据：Agent 读到的契约里必须写清 uni-app 的四个约定与账号口径。"""
    c = _contract(tmp_path, META_MOBILE)
    for anchor in ("frontend/mobile/", "`uni.*` API", "条件编译", "rpx", "相对 `/api`",
                   "/h5/", "h5.router.base", "pages.json", "不要为移动端新增发布端口",
                   "移动端用户", "role_key 为 `mobile`", "POST /auth/register",
                   "后台用户列表默认就按这个角色过滤"):
        assert anchor in c, anchor
    assert "\n12)" in c and "\n13)" in c and "\n14)" in c
    assert c.count("Vue3 + Spring Boot 3 + uni-app 移动端") == 1


def test_mobile_stack_key_agrees_with_the_registry():
    """契约里的栈 key 是字面量：与 compose 登记、栈注册表必须是同一个字符串。"""
    from app.deploy import compose, contract
    from app.services import app_service
    assert contract.MOBILE_STACK == compose.MOBILE_STACK == app_service.MOBILE_STACK
