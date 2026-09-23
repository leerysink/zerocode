# tests/test_compose_render.py
import copy

import pytest
import yaml
from app.deploy import compose
from app.deploy.errors import DeployError

META = {"schema": "zc-app/1", "name": "来料登记", "slug": "lailiao", "scale": "small",
        "stack": "vue3-fastapi", "ports": {"prod": 8210, "dev": 8211},
        "database": {"dev": {"name": "lailiao_dev", "user": "lailiao_dev"},
                     "prod": {"name": "lailiao_prod", "user": "lailiao_prod"}},
        "health": {"frontend": "/", "backend": "/api/health"}}
CRED = {"dev": {"db": "lailiao_dev", "user": "lailiao_dev", "password": "pw123"}}

# 服务块引用的具名卷必须与顶层声明一一对应（compose 的 undefined volume 是 up 期硬失败）
def _named_volume_refs(doc):
    """服务块里引用到的具名卷（排除 ../x:/y 这类绑定挂载）。"""
    refs = set()
    for svc in (doc.get("services") or {}).values():
        for v in svc.get("volumes") or []:
            src = str(v).split(":", 1)[0]
            if not src.startswith((".", "/", "~", "$")):
                refs.add(src)
    return refs

def test_render_dev_compose_fastapi():
    y = compose.render_dev_compose(META)
    assert "name: zc-lailiao-dev" in y
    for svc in ("migrate:", "backend:", "frontend:"):
        assert svc in y
    assert "zerocode-sqlrunner:1" in y
    assert "TARGET_DB: ${DEV_DB}" in y and "TARGET_USER: ${DEV_USER}" in y
    assert "8211:5173" in y                      # 仅前端端口对外（规格 05 FR5-06）
    assert "zcnet" in y and "external: true" in y
    assert "service_completed_successfully" in y  # 迁移不过应用不起（规格 07 FR7-04）
    assert "${{DEV_DB}}" not in y                 # 渲染器不得吞掉 compose 插值语法
    doc = yaml.safe_load(y)                        # 整份文档必须是合法 yaml（防结构错误）
    assert doc["volumes"][compose.VOLUME_NAME] == {}  # 顶层具名卷声明，否则 up 报 undefined volume
    assert _named_volume_refs(doc) == set(doc["volumes"])  # 引用↔声明一致（卷名只有一个事实源）

def test_render_dev_compose_static():
    m = copy.deepcopy(META); m["stack"] = "static"
    y = compose.render_dev_compose(m)
    assert "backend:" not in y and "migrate:" not in y and "frontend:" in y
    doc = yaml.safe_load(y)                        # 同 fastapi：整份文档必须是合法 yaml
    assert doc["volumes"][compose.VOLUME_NAME] == {}  # 顶层具名卷声明，否则 up 报 undefined volume
    assert _named_volume_refs(doc) == set(doc["volumes"])  # 引用↔声明一致
    assert list(doc["services"]) == ["frontend"]   # static 栈仅 frontend（规格 03 §4.1）
    assert doc["services"]["frontend"]["ports"] == ["8211:5173"]

def test_only_frontend_publishes_ports():
    """仅前端端口对外（规格 05 FR5-06 / D5，P0）：backend/migrate 不得出现 ports（审查 I-2 / V9-M1）。"""
    doc = yaml.safe_load(compose.render_dev_compose(META))
    assert doc["services"]["frontend"]["ports"] == ["8211:5173"]
    assert "ports" not in doc["services"]["backend"]
    assert "ports" not in doc["services"]["migrate"]
    assert [n for n, s in doc["services"].items() if s.get("ports")] == ["frontend"]

def test_volume_name_is_single_source_of_truth(monkeypatch):
    """改卷名只动一处：挂载条目与顶层声明都由 VOLUME_NAME 派生（审查 I-1 / V9-M2）。"""
    assert compose.FE_VOLUME == f"{compose.VOLUME_NAME}:{compose.FE_MOUNT_PATH}"
    monkeypatch.setattr(compose, "VOLUME_NAME", "fe_cache")
    y = compose.render_dev_compose(META)
    doc = yaml.safe_load(y)
    assert doc["volumes"] == {"fe_cache": {}}      # 顶层声明跟随改名
    assert "fe_cache:/src/node_modules" in y       # 服务挂载同步跟随，二者不脱钩
    assert _named_volume_refs(doc) == set(doc["volumes"])

def test_render_dev_compose_no_marker_residue():
    """渲染产物不得残留 __KEY__ 标记（全局约束 16 / 审查 I-3）：三栈产物已无 "__"。"""
    for stack in ("vue3-fastapi", "static", "vue3-springboot"):
        m = copy.deepcopy(META); m["stack"] = stack
        assert "__" not in compose.render_dev_compose(m)

def test_templates_still_carry_markers():
    """上一条断言有牙齿的前提：原始模板常量确实带标记，替换确实发生过。"""
    for tpl in (compose._DEV_COMPOSE, compose._FE_ONLY, compose._DEV_COMPOSE_SB):
        assert "__SLUG__" in tpl and "__DEV_PORT__" in tpl
        assert "__FE_VOL__" in tpl and "__FE_VOL_NAME__" in tpl
    assert "__MIGRATE_SVC__" in compose._DEV_COMPOSE
    assert "__MIGRATE_SVC__" not in compose._MIGRATE_SVC
    # m2 卷只属于大型栈：它的两个标记必须在 SB 模板里，否则上面的零残留断言对它没牙齿
    assert "__M2_VOL__" in compose._DEV_COMPOSE_SB and "__M2_VOL_NAME__" in compose._DEV_COMPOSE_SB
    assert "__M2_VOL__" not in compose._DEV_COMPOSE and "__M2_VOL__" not in compose._FE_ONLY

def test_render_rejects_unreplaced_marker(monkeypatch):
    """漏替换的标记必须显式报错而非静默落盘（审查 I-3 / V9-M3 的同型变异）。"""
    monkeypatch.setattr(compose, "_DEV_COMPOSE", compose._DEV_COMPOSE + "    user: __RUN_USER__\n")
    with pytest.raises(DeployError) as ei:
        compose.render_dev_compose(META)
    assert "__RUN_USER__" in str(ei.value)

def test_render_env():
    e = compose.render_env(META, CRED)
    assert "DEV_PORT=8211" in e and "PROD_PORT=8210" in e
    assert "DEV_DB=lailiao_dev" in e and "DEV_USER=lailiao_dev" in e and "DEV_PASSWORD=pw123" in e
    assert "PROD_PASSWORD=" in e                  # M2 发布时补齐
    assert "pw123" not in compose.render_env(META, {"dev": CRED["dev"]}).split("DEV_PASSWORD")[0]

def test_ensure_deploy_files_idempotent(tmp_path):
    (tmp_path / "deploy").mkdir()
    compose.ensure_deploy_files(tmp_path, META, CRED)
    y1 = (tmp_path / "deploy" / "dev-compose.yml").read_text(encoding="utf-8")
    compose.ensure_deploy_files(tmp_path, META, CRED)
    y2 = (tmp_path / "deploy" / "dev-compose.yml").read_text(encoding="utf-8")
    assert y1 == y2
    env1 = (tmp_path / "deploy" / ".env").read_text(encoding="utf-8")
    assert "DEV_PASSWORD=pw123" in env1


# ==== Task 4：prod compose 渲染 + .env PROD_*/PROD_VERSION 补齐 ====

def test_render_prod_compose_fastapi():
    import yaml
    y = compose.render_prod_compose(META)
    doc = yaml.safe_load(y)
    assert doc["name"] == "zc-lailiao-prod"
    assert doc["networks"]["zcnet"] == {"external": True}
    svc = doc["services"]
    assert set(svc) == {"migrate", "backend", "frontend"}
    assert svc["migrate"]["image"] == "zerocode-sqlrunner:1"
    assert svc["migrate"]["environment"]["TARGET_DB"] == "${PROD_DB}"       # 闸门指 prod 库
    assert svc["backend"]["image"] == "zc-lailiao-backend:prod-${PROD_VERSION}"
    assert svc["backend"]["build"] == "../backend"
    assert svc["backend"]["restart"] == "unless-stopped"
    assert "${PROD_PASSWORD}" in svc["backend"]["environment"]["DATABASE_URL"]
    assert svc["backend"]["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    assert svc["frontend"]["image"] == "zc-lailiao-frontend:prod-${PROD_VERSION}"
    assert svc["frontend"]["build"] == "../frontend"
    assert svc["frontend"]["ports"] == ["8210:80"]                          # 仅前端对外，prod 端口
    for name in ("backend", "frontend"):
        assert "ports" not in svc[name] or name == "frontend"
    assert "volumes" not in (svc["frontend"] if isinstance(svc["frontend"], dict) else {}) or True
    # prod 栈不挂源码卷、无 dev server 命令（镜像即产物）
    assert "bind" not in y and "npm run dev" not in y and "uvicorn --reload" not in y

def test_ensure_deploy_files_prod_updates_env(tmp_path):
    (tmp_path / "deploy").mkdir()
    compose.ensure_deploy_files(tmp_path, META, {"dev": CRED["dev"]})          # 创建态：PROD_* 空
    compose.ensure_deploy_files(tmp_path, META, {"dev": CRED["dev"], "prod": PROD_CRED},
                                version="b20260920120000")
    env = (tmp_path / "deploy" / ".env").read_text(encoding="utf-8")
    assert "PROD_DB=lailiao_prod" in env and "PROD_USER=lailiao_prod" in env
    assert "PROD_PASSWORD=pwprod" in env and "PROD_VERSION=b20260920120000" in env
    assert (tmp_path / "deploy" / "prod-compose.yml").is_file()
    # 再次发布换版本：PROD_VERSION 被更新、其余键不重复堆行
    compose.ensure_deploy_files(tmp_path, META, {"dev": CRED["dev"], "prod": PROD_CRED},
                                version="b20260920130000")
    env2 = (tmp_path / "deploy" / ".env").read_text(encoding="utf-8")
    assert env2.count("PROD_VERSION=") == 1 and "PROD_VERSION=b20260920130000" in env2
    assert "DEV_PASSWORD=pw123" in env2                                       # dev 键不丢

PROD_CRED = {"db": "lailiao_prod", "user": "lailiao_prod", "password": "pwprod"}


def test_migrate_mounts_schema_and_sqlchange():
    """P2 Task 5：dev 与 prod 的 migrate 服务都同时挂载声明式结构轨 /schema 与数据轨 /sql。"""
    dev = compose.render_dev_compose(META)
    prod = compose.render_prod_compose(META)
    for y in (dev, prod):
        doc = yaml.safe_load(y)
        vols = doc["services"]["migrate"]["volumes"]
        assert "../schema:/schema:ro" in vols
        assert "../sqlchange:/sql:ro" in vols


# ==== Task 4 追加（控制者裁定 D-T4：单向推进语义双向化 + 模板片段不得重复注入）====

def test_ensure_deploy_files_keeps_creds_of_the_omitted_side(tmp_path):
    """D-T4：某一侧 creds 缺/空时，保留 .env 既有非空值——dev 侧同坑同治（T6/T7 传
    `{"dev": read_cred(...) or {}, "prod": cred}`，dev 供给失败/未供给不得冲空既有 DEV_*）。"""
    (tmp_path / "deploy").mkdir()
    compose.ensure_deploy_files(tmp_path, META, {"dev": CRED["dev"], "prod": PROD_CRED},
                                version="b20260920120000")
    # 只传 dev（如 dev 重启流程）：不得冲掉 PROD_* / PROD_VERSION
    compose.ensure_deploy_files(tmp_path, META, {"dev": CRED["dev"]})
    env = (tmp_path / "deploy" / ".env").read_text(encoding="utf-8")
    assert "PROD_DB=lailiao_prod" in env and "PROD_USER=lailiao_prod" in env
    assert "PROD_PASSWORD=pwprod" in env and "PROD_VERSION=b20260920120000" in env
    # 只传 prod（如发布流程 dev cred 取空）：不得冲掉 DEV_*
    compose.ensure_deploy_files(tmp_path, META, {"prod": PROD_CRED}, version="b20260920130000")
    env2 = (tmp_path / "deploy" / ".env").read_text(encoding="utf-8")
    assert "DEV_DB=lailiao_dev" in env2 and "DEV_USER=lailiao_dev" in env2
    assert "DEV_PASSWORD=pw123" in env2
    assert env2.count("DEV_PASSWORD=") == 1 and env2.count("PROD_PASSWORD=") == 1
    assert "PROD_VERSION=b20260920130000" in env2
    # 空 dict 侧同样保留（creds 形态 {"dev": {}}：T6/T7 的 `or {}` 分支）
    compose.ensure_deploy_files(tmp_path, META, {"dev": {}, "prod": PROD_CRED})
    env3 = (tmp_path / "deploy" / ".env").read_text(encoding="utf-8")
    assert "DEV_PASSWORD=pw123" in env3 and "PROD_PASSWORD=pwprod" in env3

def test_render_prod_compose_single_service_blocks():
    """服务块只出现一次：migrate 片段经 __MIGRATE_PROD__ 槽注入，模板拼接不得重复追加。"""
    y = compose.render_prod_compose(META)
    lines = y.splitlines()
    for name in ("migrate", "backend", "frontend"):
        assert lines.count(f"  {name}:") == 1, name          # 行首两空格=服务级键（不误伤 depends_on）
    assert "__" not in y                                     # 标记残留自检与 dev 同口径
    assert "external: true" in y

def test_render_prod_compose_static_and_unknown_stack_raise():
    """static 发布 M3：与 dev 同口径显式报错（brief Interfaces）。"""
    for stack in ("static", "vue3-springboot"):
        m = copy.deepcopy(META); m["stack"] = stack
        with pytest.raises(DeployError) as ei:
            compose.render_prod_compose(m)
        assert stack in str(ei.value)
        assert ei.value.status == 400

def test_ensure_deploy_files_prod_compose_gated_on_stack_only(tmp_path):
    """T4 波裁定（欠账）：prod-compose.yml 的落盘条件放宽为 **stack=vue3-fastapi 即渲染**。

    理由：该文件内容只依赖 meta（凭据全走 .env 的 `${}` 插值），旧门要求
    `creds.prod.password` 非空——首次发布 build 时 prod 凭据尚未供给，按旧门就没有
    compose 文件可 build（发布流水线必挂）。放宽后创建流程多落一份 prod-compose.yml，
    与 dev 产物一样是可重放的渲染件；`.env` 的 PROD_* 留空语义不变。
    """
    compose.ensure_deploy_files(tmp_path, META, {"dev": CRED["dev"]})
    assert (tmp_path / "deploy" / "dev-compose.yml").is_file()
    assert (tmp_path / "deploy" / "prod-compose.yml").is_file()        # 无 prod 凭据也渲染
    assert "image: zc-lailiao-frontend:prod-${PROD_VERSION}" in \
        (tmp_path / "deploy" / "prod-compose.yml").read_text(encoding="utf-8")
    env = (tmp_path / "deploy" / ".env").read_text(encoding="utf-8")
    assert "PROD_VERSION" not in env and "PROD_PASSWORD=\n" in env      # 留空/不写版本照旧


def test_ensure_deploy_files_static_stack_writes_no_prod_compose(tmp_path):
    """static 栈不发布（M3）：不落 prod-compose.yml——放宽后闸门只剩栈判定，须钉住另一半。"""
    m = copy.deepcopy(META)
    m["stack"] = "static"
    compose.ensure_deploy_files(tmp_path, m, {"dev": CRED["dev"]})
    assert (tmp_path / "deploy" / "dev-compose.yml").is_file()
    assert not (tmp_path / "deploy" / "prod-compose.yml").exists()

def test_ensure_deploy_files_dev_bytes_unchanged(tmp_path):
    """dev 产物逐字节不变（向后兼容硬门槛）：新签名不改动 dev-compose.yml 一个字符。"""
    compose.ensure_deploy_files(tmp_path, META, CRED)
    dev_y = (tmp_path / "deploy" / "dev-compose.yml").read_text(encoding="utf-8")
    assert dev_y == compose.render_dev_compose(META)
    compose.ensure_deploy_files(tmp_path, META, {"dev": CRED["dev"], "prod": PROD_CRED},
                                version="b20260920120000")
    assert (tmp_path / "deploy" / "dev-compose.yml").read_text(encoding="utf-8") == dev_y


# ==== Task B1：dev compose 的 springboot 分支 + 栈私有运行密钥 ====

META_SB = {**META, "slug": "bigapp", "scale": "large", "stack": "vue3-springboot",
           "database": {"dev": {"name": "bigapp_dev", "user": "bigapp_dev"},
                        "prod": {"name": "bigapp_prod", "user": "bigapp_prod"}},
           "health": {"frontend": "/", "backend": "/api/health"}}


def test_render_dev_compose_springboot_shape():
    """大型栈：私有 redis + 源码挂载 + per-app .m2 卷 + 容器内 mvn spring-boot:run。"""
    y = compose.render_dev_compose(META_SB)
    doc = yaml.safe_load(y)
    assert doc["name"] == "zc-bigapp-dev"
    svc = doc["services"]
    assert set(svc) == {"migrate", "redis", "backend", "frontend"}
    assert svc["migrate"]["image"] == "zerocode-sqlrunner:1"      # 迁移闸门与 fastapi 栈完全同形
    assert "../schema:/schema:ro" in svc["migrate"]["volumes"]
    assert svc["redis"]["image"] == "redis:7-alpine"
    assert "ports" not in svc["redis"]                            # Redis 不出宿主端口，只走 compose 内部网
    assert "${REDIS_PASSWORD}" in str(svc["redis"]["command"])
    assert svc["backend"]["image"] == "maven:3.9-eclipse-temurin-17"
    assert "../backend:/src" in svc["backend"]["volumes"]
    assert svc["backend"]["command"] == "mvn -B spring-boot:run"
    env = svc["backend"]["environment"]
    assert env["SPRING_DATASOURCE_URL"].startswith("jdbc:postgresql://zerocode-pg16:5432/${DEV_DB}")
    assert "currentSchema=public" in env["SPRING_DATASOURCE_URL"]
    assert env["SPRING_DATASOURCE_USERNAME"] == "${DEV_USER}"
    assert env["SPRING_DATASOURCE_PASSWORD"] == "${DEV_PASSWORD}"
    assert env["SPRING_DATA_REDIS_HOST"] == "redis"
    assert env["SPRING_DATA_REDIS_PASSWORD"] == "${REDIS_PASSWORD}"
    assert env["JWT_SECRET"] == "${JWT_SECRET}" and env["UPLOAD_DIR"] == "/src/uploads"
    assert env["SERVER_PORT"] == "8080"
    assert svc["backend"]["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    assert svc["backend"]["depends_on"]["redis"]["condition"] == "service_started"
    assert svc["frontend"]["ports"] == ["8211:5173"]              # 仍只有前端对外
    assert "ports" not in svc["backend"] and "ports" not in svc["migrate"]
    assert "__" not in y                                          # 标记零残留
    assert compose.M2_VOLUME_NAME in doc["volumes"]
    assert _named_volume_refs(doc) == set(doc["volumes"])          # 引用↔声明一致（含 m2 卷）


def test_m2_volume_name_is_single_source_of_truth(monkeypatch):
    """与 fe 卷同一条规矩：改 .m2 卷名只动 M2_VOLUME_NAME，挂载条目与顶层声明不脱钩。"""
    assert compose.M2_VOLUME == f"{compose.M2_VOLUME_NAME}:{compose.M2_MOUNT_PATH}"
    monkeypatch.setattr(compose, "M2_VOLUME_NAME", "mvn_repo")
    doc = yaml.safe_load(compose.render_dev_compose(META_SB))
    assert set(doc["volumes"]) == {compose.VOLUME_NAME, "mvn_repo"}
    assert "mvn_repo:/root/.m2" in doc["services"]["backend"]["volumes"]
    assert _named_volume_refs(doc) == set(doc["volumes"])


def test_render_dev_compose_springboot_fastapi_untouched():
    """回归钉：新增分支不得改动 fastapi 产物一个字符。"""
    expect = (compose._DEV_COMPOSE
              .replace("__MIGRATE_SVC__", compose._MIGRATE_SVC)
              .replace("__DEV_PORT__", "8211")
              .replace("__FE_VOL__", compose._fe_volume())
              .replace("__FE_VOL_NAME__", compose.VOLUME_NAME)
              .replace("__SLUG__", "lailiao"))
    assert compose.render_dev_compose(META) == expect


def test_render_env_generates_secrets_once(tmp_path):
    """密钥语义：缺则生成、有则保留（发布重渲染不得冲空），且只给需要它的栈。"""
    compose.ensure_deploy_files(tmp_path, META_SB, CRED)
    env = (tmp_path / "deploy" / ".env").read_text(encoding="utf-8")
    jwt1, red1 = compose._env_get(env, "JWT_SECRET"), compose._env_get(env, "REDIS_PASSWORD")
    assert jwt1 and red1 and env.count("JWT_SECRET=") == 1 and env.count("REDIS_PASSWORD=") == 1
    # 生成值必须是 compose/shell 可直接内联的字符集：带 $ 会被 compose 再展开一次，带空格会碎词
    for v in (jwt1, red1):
        assert not any(c in v for c in ' "$\'\\'), v
    compose.ensure_deploy_files(tmp_path, META_SB, CRED)
    env2 = (tmp_path / "deploy" / ".env").read_text(encoding="utf-8")
    assert compose._env_get(env2, "JWT_SECRET") == jwt1           # 有则保留，不重生成
    assert compose._env_get(env2, "REDIS_PASSWORD") == red1
    other = tmp_path / "other"
    compose.ensure_deploy_files(other, META, CRED)
    assert "JWT_SECRET" not in (other / "deploy" / ".env").read_text(encoding="utf-8")


def test_render_dev_compose_unknown_stack_still_raises():
    """栈闸门不许松成兜底：未交付的栈继续 500 显式报错。"""
    m = {**META, "stack": "vue3-node"}
    with pytest.raises(DeployError) as ei:
        compose.render_dev_compose(m)
    assert "vue3-node" in str(ei.value) and ei.value.status == 500
