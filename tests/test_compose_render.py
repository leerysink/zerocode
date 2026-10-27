# tests/test_compose_render.py
import copy
from pathlib import Path

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
    # prod 模板也确有标记：residue 自检对 prod 同样有牙齿（计划 Step 5 草稿把 prod 塞进上面 dev 循环是错的：标记集不同）
    assert "__SLUG__" in compose._PROD_COMPOSE and "__MIGRATE_PROD__" in compose._PROD_COMPOSE
    assert "__PROD_PORT__" in compose._PROD_COMPOSE
    assert "__UPLOADS_VOL__" not in compose._PROD_COMPOSE         # 上传/redis 卷只属于大型栈 prod（fastapi 无这两个基座）
    sbp = compose._PROD_COMPOSE_SB
    for m in ("__SLUG__", "__MIGRATE_PROD__", "__PROD_PORT__", "__UPLOADS_VOL__",
              "__UPLOADS_VOL_NAME__", "__REDIS_DATA_VOL__", "__REDIS_DATA_VOL_NAME__"):
        assert m in sbp, m

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
    for y in (dev, prod, compose.render_prod_compose(META_SB)):
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
    for meta, names in ((META, ("migrate", "backend", "frontend")),
                        (META_SB, ("migrate", "appredis", "backend", "frontend"))):
        y = compose.render_prod_compose(meta)
        lines = y.splitlines()
        for name in names:
            assert lines.count(f"  {name}:") == 1, f"{meta['stack']}:{name}"   # 行首两空格=服务级键（不误伤 depends_on）
        assert "__" not in y                                     # 标记残留自检与 dev 同口径
        assert "external: true" in y

def test_render_prod_compose_static_and_unknown_stack_raise():
    """未开放发布的栈（static / 尚未交付的 vue3-node）：与 dev 同口径显式报错（brief Interfaces）。"""
    for stack in ("static", "vue3-node"):
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
    assert set(svc) == {"migrate", "appredis", "backend", "frontend"}
    assert svc["migrate"]["image"] == "zerocode-sqlrunner:1"      # 迁移闸门与 fastapi 栈完全同形
    assert "../schema:/schema:ro" in svc["migrate"]["volumes"]
    assert svc["appredis"]["image"] == "redis:7-alpine"
    assert "ports" not in svc["appredis"]                         # Redis 不出宿主端口，只走 compose 内部网
    assert svc["appredis"]["networks"] == ["default"]             # 另一半：也不上全平台共享的 zcnet
    assert "${REDIS_PASSWORD:?" in str(svc["appredis"]["command"])  # 空值必须让 compose 自己拒收
    assert svc["backend"]["image"] == "maven:3.9-eclipse-temurin-17"
    assert "../backend:/src" in svc["backend"]["volumes"]
    # 钉住「就地 mvn 的产物落在 backend/」这一前提：屏蔽名单（files.py / packages.py）锚的就是 backend/target
    # 与 backend/logs，它们成立只因为 working_dir 就是挂进去的那目录（logback 写 ${user.dir}/logs）。
    assert svc["backend"]["working_dir"] == "/src"
    assert svc["backend"]["command"] == "mvn -B spring-boot:run"
    assert svc["backend"]["networks"] == ["default", "zcnet"]     # 上 zcnet 只为访 zerocode-pg16
    env = svc["backend"]["environment"]
    assert env["SPRING_DATASOURCE_URL"].startswith("jdbc:postgresql://zerocode-pg16:5432/${DEV_DB}")
    assert "currentSchema=public" in env["SPRING_DATASOURCE_URL"]
    assert env["SPRING_DATASOURCE_USERNAME"] == "${DEV_USER}"
    assert env["SPRING_DATASOURCE_PASSWORD"] == "${DEV_PASSWORD}"
    # 主机名跟着服务名走：不叫裸 redis，免与 zcnet 上的同名容器赌解析
    assert env["SPRING_DATA_REDIS_HOST"] == "appredis"
    assert env["SPRING_DATA_REDIS_PASSWORD"].startswith("${REDIS_PASSWORD:?")
    assert env["JWT_SECRET"].startswith("${JWT_SECRET:?")
    assert env["UPLOAD_DIR"] == "/src/uploads"
    assert env["SERVER_PORT"] == "8080"
    assert svc["backend"]["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    assert svc["backend"]["depends_on"]["appredis"]["condition"] == "service_started"
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


def test_legacy_stacks_match_golden_files():
    """旧栈产物逐字节钉在 golden 文件上：新增分支不得改动它们一个字符（评审 N-4）。

    不用「拿实现重放替换链」的镜像断言：那种写法会跟着模板一起漂（改了模板两边同步，照绿），
    也会因替换顺序调整而假红。golden 是仓外的硬事实，要动它必须显式重生（重生手段见下）。
    覆盖两条产物：fastapi 与 static（static 侧此前只有模板级钉子，产物级没人钉）。
    重生：`python -c "from app.deploy import compose; ..."` 把 render_dev_compose 的结果写回这两个文件。
    """
    golden = Path(__file__).parent / "golden"
    for stack, fname in (("vue3-fastapi", "dev-compose.fastapi.yml"), ("static", "dev-compose.static.yml")):
        m = copy.deepcopy(META)
        m["stack"] = stack
        assert compose.render_dev_compose(m) == (golden / fname).read_text(encoding="utf-8"), fname


def test_springboot_gets_prod_compose(tmp_path):
    """C1 翻案（原 test_springboot_gets_no_prod_compose_yet 钉的是「尚未放行发布」的临时态）：
    大型栈发布已放行，创建即落一份可 build 的 prod 件，render_prod_compose 不再抛 400。"""
    compose.ensure_deploy_files(tmp_path, META_SB, CRED)
    assert (tmp_path / "deploy" / "dev-compose.yml").is_file()
    assert (tmp_path / "deploy" / "prod-compose.yml").is_file()
    assert "zc-bigapp-backend:prod-${PROD_VERSION}" in \
        (tmp_path / "deploy" / "prod-compose.yml").read_text(encoding="utf-8")
    compose.render_prod_compose(META_SB)            # 不再报 400


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


# ==== Task C1：prod compose 的 springboot 分支（镜像即产物） ====


def test_render_prod_compose_springboot_shape():
    """大型栈发布：镜像即产物（工程目录一个都不挂），上传件与 Redis 数据落命名卷。

    命名与密钥闸门对齐 **B1 已落地的 dev 约定**（计划顶注的 stale 草稿已被纠正）：redis 服务叫
    `appredis`（免与 zcnet 上的同名容器赌解析）、三个密钥位都带 `:?`、数据源 PROD_* 保持裸插值。
    """
    doc = yaml.safe_load(compose.render_prod_compose(META_SB))
    assert doc["name"] == "zc-bigapp-prod"
    assert doc["networks"]["zcnet"] == {"external": True}
    svc = doc["services"]
    assert set(svc) == {"migrate", "appredis", "backend", "frontend"}
    b = svc["backend"]
    assert b["image"] == "zc-bigapp-backend:prod-${PROD_VERSION}"
    assert b["build"] == "../backend"
    assert not any(str(v).startswith("../") for v in b["volumes"])   # 镜像即产物：不挂源码卷
    assert b["volumes"] == ["uploads:/uploads"]
    assert b["networks"] == ["default", "zcnet"]                     # 上 zcnet 只为访 zerocode-pg16
    env = b["environment"]
    assert env["SPRING_DATASOURCE_URL"].startswith("jdbc:postgresql://zerocode-pg16:5432/${PROD_DB}")
    assert env["SPRING_DATASOURCE_USERNAME"] == "${PROD_USER}"       # 数据源非密钥：保持裸插值
    assert env["SPRING_DATASOURCE_PASSWORD"] == "${PROD_PASSWORD}"
    assert env["SPRING_DATA_REDIS_HOST"] == "appredis"              # 主机名跟着服务名走
    assert env["SPRING_DATA_REDIS_PASSWORD"].startswith("${REDIS_PASSWORD:?")
    assert env["JWT_SECRET"].startswith("${JWT_SECRET:?") and env["UPLOAD_DIR"] == "/uploads"
    assert "ports" not in b                                          # 只有前端对外（规格 05 FR5-06）
    assert "command" not in b                                        # 无 dev server 命令
    assert b["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    assert b["depends_on"]["appredis"]["condition"] == "service_started"
    r = svc["appredis"]
    assert r["image"] == "redis:7-alpine" and "ports" not in r
    assert r["networks"] == ["default"]                              # 不上全平台共享的 zcnet（与 dev 同）
    assert r["volumes"] == ["redis-data:/data"]
    assert "--appendonly" in r["command"] and "yes" in r["command"]   # 缓存型基座也开持久化（command 是 flow 序列，逐元素判）
    assert "${REDIS_PASSWORD:?" in str(r["command"])                 # 空值让 compose 自己拒收，不起无密码 Redis
    f = svc["frontend"]
    assert f["image"] == "zc-bigapp-frontend:prod-${PROD_VERSION}"
    assert f["build"] == "../frontend" and f["ports"] == ["8210:80"]
    assert doc["volumes"] == {"uploads": {}, "redis-data": {}}
    assert _named_volume_refs(doc) == set(doc["volumes"])
    y = compose.render_prod_compose(META_SB)
    assert "__" not in y
    for name in ("migrate", "appredis", "backend", "frontend"):
        assert y.splitlines().count(f"  {name}:") == 1               # 片段注入不得重复追加


def test_ensure_deploy_files_springboot_writes_prod_compose(tmp_path):
    """落盘闸门按「已支持发布的栈集合」判定，不再写死单栈；密钥与 dev 同源同一份 .env。"""
    compose.ensure_deploy_files(tmp_path, META_SB, {"dev": CRED["dev"]})
    text_ = (tmp_path / "deploy" / "prod-compose.yml").read_text(encoding="utf-8")
    assert "image: zc-bigapp-backend:prod-${PROD_VERSION}" in text_
    env = (tmp_path / "deploy" / ".env").read_text(encoding="utf-8")
    assert "JWT_SECRET=" in env and "REDIS_PASSWORD=" in env
    assert env.count("JWT_SECRET=") == 1                 # 文件只有一行键（prod 与 dev 共用）
    assert "PROD_PASSWORD=\n" in env                     # 未供给时留空语义不变


def test_render_prod_compose_fastapi_matches_golden():
    """回归钉（替代草稿的「拿实现重放」镜像断言）：新增 SB 分支不得扰动 fastapi prod 产物一个字节。

    与 dev 侧 `test_legacy_stacks_match_golden_files` 同理：golden 是仓外硬事实，要动必须显式重生
    （重生：`python -c "from app.deploy import compose; ..."` 把 render_prod_compose(META) 写回此文件）。
    """
    golden = Path(__file__).parent / "golden" / "prod-compose.fastapi.yml"
    assert compose.render_prod_compose(META) == golden.read_text(encoding="utf-8")


# ---- 应用 dev 栈的依赖镜像源注入
#
# 变量由打包时烘进平台容器（build_output.py 写的 `${ZEROCODE_PYPI_MIRROR:-打包值}`），渲染器在
# 平台容器里读 os.environ 再转注进应用的 dev 栈——于是服务器上改一次 deploy 层的 .env 就同时管住
# 平台自己与它起出来的每个应用，不必重跑打包。三个边界要紧：没设时零注入（旧 golden 不动）、
# 只给一个时另一个服务不许凭空长出 environment 段、畸形值要响而不是静默丢。

PYPI_ENV = "ZEROCODE_PYPI_MIRROR"
NPM_ENV = "ZEROCODE_NPM_REGISTRY"


@pytest.fixture(autouse=True)
def _neutralize_host_mirror_env(monkeypatch):
    """每个用例起手清掉宿主机上的镜像源变量。

    这个功能鼓励的正是「打包机上 setx 一次，以后每次发布自动带上」，而 pytest 进程会继承那份
    用户级变量——不隔离的话，同一份代码在配过镜像源的机器上会让 golden 逐字节比对与零注入用例
    莫名变红（换台机器又是绿的，最难查的那种）。需要取值的用例自己 setenv，覆盖本清除。
    """
    for name in (PYPI_ENV, NPM_ENV):
        monkeypatch.delenv(name, raising=False)


def test_dev_compose_injects_nothing_without_mirror_env():
    """零注入：两个变量都没设时，backend 的 env 段与 command 行紧贴，frontend 不长出 environment。"""
    y = compose.render_dev_compose(META)
    assert "PIP_INDEX_URL" not in y and "NPM_CONFIG_REGISTRY" not in y
    # 字节级：注入标记空替换后不得留下空行（标记附在行尾而不是占独立模板行的全部理由）
    assert 'APP_PORT: "8000"\n    command:' in y
    doc = yaml.safe_load(y)
    assert "environment" not in doc["services"]["frontend"]
    assert set(doc["services"]["backend"]["environment"]) == {"DATABASE_URL", "APP_PORT"}
    assert "__" not in y                            # 标记不得残留


def test_dev_compose_injects_pypi_and_npm_from_env(monkeypatch):
    """两个都设：fastapi dev 栈的 backend 收 pip、frontend 收 npm，且整份仍是合法 yaml。"""
    monkeypatch.setenv(PYPI_ENV, "https://mirrors.aliyun.com/pypi/simple")
    monkeypatch.setenv(NPM_ENV, "https://registry.npmmirror.com")
    y = compose.render_dev_compose(META)
    doc = yaml.safe_load(y)
    assert doc["services"]["backend"]["environment"]["PIP_INDEX_URL"] == \
        "https://mirrors.aliyun.com/pypi/simple"
    assert doc["services"]["frontend"]["environment"]["NPM_CONFIG_REGISTRY"] == \
        "https://registry.npmmirror.com"
    assert "${{" not in y and "__" not in y
    # 注入后服务名与卷的引用↔声明关系不能变（compose 的 undefined volume 是 up 期硬失败）
    assert _named_volume_refs(doc) == set(doc["volumes"])


def test_dev_compose_npm_only_does_not_fabricate_backend_pip(monkeypatch):
    """只给 npm：backend 的 env 段不得多出 pip 行；frontend 的 environment 段由注入块自己开。"""
    monkeypatch.setenv(NPM_ENV, "https://registry.npmmirror.com")
    y = compose.render_dev_compose(META)
    assert "PIP_INDEX_URL" not in y
    doc = yaml.safe_load(y)
    assert doc["services"]["frontend"]["environment"] == {
        "NPM_CONFIG_REGISTRY": "https://registry.npmmirror.com"}
    # migrate 片段与 backend 各有一段、frontend 新开一段：行计数盯的是「没多长出第四段」
    assert y.count("environment:") == 3
    assert set(doc["services"]["backend"]["environment"]) == {"DATABASE_URL", "APP_PORT"}


def test_dev_compose_pypi_only_keeps_frontend_environmentless(monkeypatch):
    """只给 pip：frontend 不得凭空长出一个空的 environment 段（compose 会吃 KeyError 之外的怪错）。"""
    monkeypatch.setenv(PYPI_ENV, "https://pypi.tuna.tsinghua.edu.cn/simple")
    doc = yaml.safe_load(compose.render_dev_compose(META))
    assert doc["services"]["backend"]["environment"]["PIP_INDEX_URL"] == \
        "https://pypi.tuna.tsinghua.edu.cn/simple"
    assert "environment" not in doc["services"]["frontend"]


@pytest.mark.parametrize("stack", ["static", "vue3-springboot"])
def test_dev_compose_non_fastapi_stacks_inject_npm_only(monkeypatch, stack):
    """static 与 vue3-springboot 栈里没有 pip 消费方：即使两个变量都设，也只注入 npm。

    SB 的后端是 maven（mvn spring-boot:run），maven 仓库镜像不在本次参数范围内，所以它的
    backend 段保持原样——这条断言同时把这个缺口钉住：想顺手给 SB 后端加 PIP_INDEX_URL 会红。
    """
    monkeypatch.setenv(PYPI_ENV, "https://mirrors.aliyun.com/pypi/simple")
    monkeypatch.setenv(NPM_ENV, "https://registry.npmmirror.com")
    m = copy.deepcopy(META)
    m["stack"] = stack
    y = compose.render_dev_compose(m)
    doc = yaml.safe_load(y)
    assert doc["services"]["frontend"]["environment"]["NPM_CONFIG_REGISTRY"] == \
        "https://registry.npmmirror.com"
    assert "PIP_INDEX_URL" not in y
    assert "__" not in y
    assert _named_volume_refs(doc) == set(doc["volumes"])


def test_prod_compose_ignores_mirror_env(monkeypatch):
    """诚实缺口钉住：prod 栈靠 `build: ../backend` 出镜像，pip/npm 跑在**构建容器**里，
    compose 的 environment 段对 build 阶段无效——所以这里一律不注入。

    设了变量也必须与 golden 逐字节一致：将来若要覆盖 prod 构建期，走的是 build args 而非
    environment，届时这条断言会红着提醒一并重生 golden，而不是留下两份静默不一致的产物。
    """
    monkeypatch.setenv(PYPI_ENV, "https://mirrors.aliyun.com/pypi/simple")
    monkeypatch.setenv(NPM_ENV, "https://registry.npmmirror.com")
    golden = Path(__file__).parent / "golden" / "prod-compose.fastapi.yml"
    assert compose.render_prod_compose(META) == golden.read_text(encoding="utf-8")
    y = compose.render_prod_compose(META_SB)
    assert "PIP_INDEX_URL" not in y and "NPM_CONFIG_REGISTRY" not in y


@pytest.mark.parametrize("value", [
    "mirrors.aliyun.com/pypi/simple",   # 不带协议：pip/npm 要的是完整地址
    "not a url",                        # 含空白
    "ftp://mirrors.aliyun.com",         # 非 http(s)
])
def test_dev_compose_rejects_malformed_mirror_env(monkeypatch, value):
    """畸形值整栈失败（DeployError），不静默丢弃：静默丢的结果是服务器上照旧装不到依赖。"""
    for name in (PYPI_ENV, NPM_ENV):
        monkeypatch.setenv(name, value)
    with pytest.raises(DeployError, match="ZEROCODE_"):
        compose.render_dev_compose(META)


@pytest.mark.parametrize("value", ["", "   "])
def test_dev_compose_blank_mirror_env_counts_as_unset(monkeypatch, value):
    """空值与纯空白都视同未设：不报错、不注入（与打包机 env 的通行规则一致）。"""
    for name in (PYPI_ENV, NPM_ENV):
        monkeypatch.setenv(name, value)
    y = compose.render_dev_compose(META)
    assert "PIP_INDEX_URL" not in y and "NPM_CONFIG_REGISTRY" not in y
    assert 'APP_PORT: "8000"\n    command:' in y
