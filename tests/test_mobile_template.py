# tests/test_mobile_template.py
"""移动栈覆盖层的模板体检（规格 2026-11-03 §5）。

与 tests/test_scaffold.py 的分工：那边钉叠加机制（顺序、保留、种子不重复），这边钉
「真实的 uniapp-overlay 叠在 vue3-springboot 上」以后产物长什么样。判据尽量是跨文件一致性——
服务名、端口、产物路径分别写在 compose / vite / Dockerfile 三处，只有互相对得上才算接好。
"""
import json
import re
from pathlib import Path

import yaml
from app.deploy import compose, scaffold

OVERLAY = Path(__file__).resolve().parents[1] / "app" / "templates" / "uniapp-overlay"
BASE = Path(__file__).resolve().parents[1] / "app" / "templates" / "vue3-springboot"
MAPPING = {"SLUG": "bigapp", "APP_NAME": "大型应用", "DEV_PORT": "8211", "PROD_PORT": "8210",
           "DEV_DB": "bigapp_dev", "PROD_DB": "bigapp_prod", "TODAY": "20260922"}
RESIDUE = re.compile(r"\{\{\s*[A-Z][A-Z0-9_]*\s*\}\}")


def _render(tmp_path) -> Path:
    scaffold.render_layers([BASE, OVERLAY], tmp_path, MAPPING)
    return tmp_path


def _mobile(tmp_path) -> Path:
    return _render(tmp_path) / "frontend" / "mobile"


def test_overlay_layers_onto_the_springboot_base_and_leaves_no_residue(tmp_path):
    """整栈真渲染：基底后端原样在、移动端工程在、全树无占位符残留。

    覆盖层是「叠」不是「换」：基底的 backend/src 与 schema/ 必须一个不少——少了它，
    应用能创建、能起 H5，接口全 404，而且错在看起来最正常的地方。
    """
    out = _render(tmp_path)
    assert (out / "backend" / "src" / "main" / "java" / "com" / "zerocode" / "app"
            / "BaseApplication.java").is_file()
    assert (out / "schema" / "schema.sql").is_file()
    mob = out / "frontend" / "mobile"
    for rel in ("package.json", "package-lock.json", "index.html", "vite.config.js",
                "src/main.js", "src/App.vue", "src/pages.json", "src/manifest.json",
                "src/pages/index/index.vue", "src/utils/request.js"):
        assert (mob / rel).is_file(), rel
    for p in out.rglob("*"):
        if not p.is_file():
            continue
        assert not RESIDUE.search(p.read_text(encoding="utf-8", errors="replace")), p


# 覆盖层带进仓库的后端代码只允许两类：
#   · 新增文件（基底没有）——注册链路；
#   · 影子文件（基底有、覆盖层整份顶替）——每加一个都必须在 SHADOW_EDITS 里写清改了哪几行。
# US-03 的本意是「后端代码只有一份真相」，不是「覆盖层一个 java 文件都不能有」：
# 把本文件集合钉死，任何整份复制基底的行为（尤其复制 AuthController/SysMenuServiceImpl）都会当场红。
OVERLAY_BACKEND_NEW = {
    "backend/src/main/java/com/zerocode/app/controller/MobileAuthController.java",
    "backend/src/main/java/com/zerocode/app/dto/MobileRegisterDTO.java",
    "backend/src/main/java/com/zerocode/app/service/MobileAuthService.java",
    "backend/src/main/java/com/zerocode/app/service/impl/MobileAuthServiceImpl.java",
}
OVERLAY_BACKEND_SHADOW = {
    "backend/src/main/java/com/zerocode/app/config/SecurityConfig.java",
    "backend/src/main/java/com/zerocode/app/dto/SysUserQueryDTO.java",
    "backend/src/main/resources/mapper/SysUserMapper.xml",
}
OVERLAY_FRONTEND_SHADOW = {
    "frontend/src/api/user.ts",
    "frontend/src/views/system/user/index.vue",
}


def test_overlay_backend_is_new_files_plus_declared_shadows():
    """覆盖层的后端文件集合必须与声明逐字相等（US-03 的仓库侧守卫）。

    影子文件是刻意的：SecurityConfig 放行注册、SysUserQueryDTO/XML 加用户范围过滤。
    它们必须逐条列在测试里，且都真的存在于基底（否则「影子」其实是裸拷贝）。
    """
    files = {p.relative_to(OVERLAY).as_posix() for p in (OVERLAY / "backend").rglob("*") if p.is_file()}
    assert files == OVERLAY_BACKEND_NEW | OVERLAY_BACKEND_SHADOW
    for rel in OVERLAY_BACKEND_NEW:
        assert not (BASE / rel).exists(), rel          # 「新增」必须是真新增
    for rel in OVERLAY_BACKEND_SHADOW:
        assert (BASE / rel).is_file(), rel
    # 基底的认证/菜单实现一个都不许被复制：它们正是「只有一份」的核心
    for rel in ("backend/src/main/java/com/zerocode/app/controller/AuthController.java",
                "backend/src/main/java/com/zerocode/app/service/impl/AuthServiceImpl.java",
                "backend/src/main/java/com/zerocode/app/service/impl/SysMenuServiceImpl.java",
                "backend/src/main/java/com/zerocode/app/service/impl/SysUserServiceImpl.java"):
        assert rel not in files


def test_overlay_sqlchange_only_adds_the_mobile_role_seed():
    """角色是数据不是结构：只追加一条 D 种子，不碰 schema.sql，也不复制基座种子。"""
    files = sorted(p.name for p in (OVERLAY / "sqlchange").glob("*.sql"))
    assert files == ["D20260101_02_mobile_role.sql"]
    assert not list((OVERLAY / "sqlchange").glob("V*.sql"))
    assert not (OVERLAY / "schema").exists()
    assert not (OVERLAY / "sqlchange" / "D20260101_01_rbac_seed.sql").exists()   # 基座种子只有一份


def test_rendered_mobile_stack_ships_both_data_track_files_in_order(tmp_path):
    """叠出来的数据轨：基座 RBAC 在前、移动端角色在后，按文件名序执行。"""
    files = sorted(p.name for p in (_render(tmp_path) / "sqlchange").glob("*.sql"))
    assert files == ["D20260101_01_rbac_seed.sql", "D20260101_02_mobile_role.sql"]


# 影子文件 = 基底 + 下列逐条编辑。编辑锚点在基底里必须唯一命中，
# 基底一旦改动而覆盖层没跟上，_apply 找不到锚点或结果对不上就红——这就是漂移守卫。
SHADOW_EDITS = {
    "backend/src/main/java/com/zerocode/app/config/SecurityConfig.java": [
        ('.requestMatchers("/auth/login", "/auth/captcha", "/health").permitAll()',
         '.requestMatchers("/auth/register", "/auth/login", "/auth/captcha", "/health").permitAll()'),
    ],
    "backend/src/main/java/com/zerocode/app/dto/SysUserQueryDTO.java": [
        ("""    /** 数据权限: 是否需要拼接可见部门条件（由切面置 true） */
    @Schema(hidden = true)
    private boolean dataScopeFiltered = false;
}""",
         """    /** 数据权限: 是否需要拼接可见部门条件（由切面置 true） */
    @Schema(hidden = true)
    private boolean dataScopeFiltered = false;

    /**
     * 用户范围: 空 = 后台用户（排除「移动端用户」角色）；传角色 key 时只看挂该角色的用户。
     * 移动端注册出来的 C 端账号默认不进后台浏览面，显式筛选该角色时才出现
     */
    private String roleKey;
}"""),
    ],
    "backend/src/main/resources/mapper/SysUserMapper.xml": [
        ("        <if test=\"query.dataScopeFiltered\">",
         """        <!-- 移动端账号默认过滤：role_key='mobile' 与 sqlchange/D20260101_02_mobile_role.sql、
             MobileAuthServiceImpl.MOBILE_ROLE_KEY 三处对齐，改一处必须同改三处 -->
        <if test="query.roleKey != null and query.roleKey != ''">
            AND EXISTS (
                SELECT 1 FROM sys_user_role ur
                INNER JOIN sys_role r ON r.id = ur.role_id AND r.deleted = 0
                WHERE ur.user_id = u.id AND r.role_key = #{query.roleKey}
            )
        </if>
        <if test="query.roleKey == null or query.roleKey == ''">
            AND NOT EXISTS (
                SELECT 1 FROM sys_user_role ur
                INNER JOIN sys_role r ON r.id = ur.role_id AND r.deleted = 0
                WHERE ur.user_id = u.id AND r.role_key = 'mobile'
            )
        </if>
        <if test="query.dataScopeFiltered">"""),
    ],
    "frontend/src/api/user.ts": [
        ("""  username?: string
  status?: number
}): Promise<ApiResult<PageResult<SysUser>>> {""",
         """  username?: string
  status?: number
  /** 用户范围：空 = 后台用户（后端默认排除移动端账号）；'mobile' = 只列移动端注册用户 */
  roleKey?: string
}): Promise<ApiResult<PageResult<SysUser>>> {"""),
    ],
    "frontend/src/views/system/user/index.vue": [
        ("""        <a-select v-model:value="query.status" placeholder="状态" allow-clear style="width: 120px">
          <a-select-option :value="1">正常</a-select-option>
          <a-select-option :value="0">停用</a-select-option>
        </a-select>""",
         """        <a-select v-model:value="query.status" placeholder="状态" allow-clear style="width: 120px">
          <a-select-option :value="1">正常</a-select-option>
          <a-select-option :value="0">停用</a-select-option>
        </a-select>
        <!-- 默认（不选）= 后台用户，后端会排除移动端注册账号；显式选「移动端用户」才列出 C 端账号 -->
        <a-select v-model:value="query.roleKey" placeholder="后台用户" allow-clear style="width: 140px">
          <a-select-option value="mobile">移动端用户</a-select-option>
        </a-select>"""),
        ("const query = reactive({ username: '', status: undefined as number | undefined })",
         "const query = reactive({ username: '', status: undefined as number | undefined, roleKey: undefined as string | undefined })"),
        ("""      username: query.username || undefined, status: query.status,
    })""",
         """      username: query.username || undefined, status: query.status, roleKey: query.roleKey,
    })"""),
        ("function resetQuery() { query.username = ''; query.status = undefined; pagination.current = 1; loadData() }",
         "function resetQuery() { query.username = ''; query.status = undefined; query.roleKey = undefined; pagination.current = 1; loadData() }"),
    ],
}


def _apply(text, edits):
    for old, new in edits:
        assert text.count(old) == 1, ("编辑锚点不唯一或已漂移", old[:60])
        text = text.replace(old, new, 1)
    return text


def test_shadow_files_are_the_base_plus_only_the_declared_edits():
    """覆盖层的影子文件 = 基底 + 声明过的编辑，逐字节成立。

    这是整份复制带来的唯一真实风险：基底改了、覆盖层还是旧版，生成的应用就同时有两套
    行为的记忆。锚点没命中或差一个字符都会在这里红，强制覆盖层跟着基底一起更新。
    """
    for rel, edits in SHADOW_EDITS.items():
        base = (BASE / rel).read_text(encoding="utf-8")
        overlay = (OVERLAY / rel).read_text(encoding="utf-8")
        assert _apply(base, edits) == overlay, rel


def test_overlay_frontend_shadows_are_declared_too():
    """管理端被顶替的文件同样要登记：少登记一个，漂移守卫就漏一个。"""
    files = {p.relative_to(OVERLAY).as_posix() for p in (OVERLAY / "frontend" / "src").rglob("*")
             if p.is_file() and not p.relative_to(OVERLAY).as_posix().startswith("frontend/mobile")}
    assert files == OVERLAY_FRONTEND_SHADOW == set(SHADOW_EDITS) - OVERLAY_BACKEND_SHADOW


def test_mobile_role_key_agrees_across_seed_java_and_admin_filter():
    """role_key 写在四个地方：种子、Java 常量、XML 过滤、管理端下拉值。

    它们没有共享的常量载体（SQL/XML 不认 Java 常量），只能靠这条测试锁成同一个字面量。
    任何一处改名而别处没跟，症状都是「注册成功但用户列表里查不到 / 过滤后是空页」。
    """
    sql = (OVERLAY / "sqlchange" / "D20260101_02_mobile_role.sql").read_text(encoding="utf-8")
    m = re.search(r"VALUES \('移动端用户', '([a-z_]+)', (\d+)", sql)
    assert m, "种子里的角色行形态变了"
    assert m.group(1) == "mobile"
    assert m.group(2) == "2"                       # data_scope=本部门；配合 dept_id 为空 → 空可见集
    assert "INSERT INTO sys_role_menu" not in sql  # 无任何后台菜单（US-12）

    impl = (OVERLAY / "backend/src/main/java/com/zerocode/app/service/impl"
            / "MobileAuthServiceImpl.java").read_text(encoding="utf-8")
    assert 'MOBILE_ROLE_KEY = "mobile"' in impl
    xml = (OVERLAY / "backend/src/main/resources/mapper/SysUserMapper.xml").read_text(encoding="utf-8")
    assert "r.role_key = 'mobile'" in xml
    vue = (OVERLAY / "frontend/src/views/system/user/index.vue").read_text(encoding="utf-8")
    assert '<a-select-option value="mobile">移动端用户</a-select-option>' in vue


def test_register_endpoint_is_permitted_only_in_the_overlay(tmp_path):
    """注册必须真的可达：路径 permitAll + 控制器映射 + H5 页调用点三处接上。

    基底不放行 /auth/register（断言反向）——放行只由覆盖层带来，所以这条编辑是载荷，
    不是格式调整；顺带钉住基底与覆盖层在这一行上的差异来源。
    """
    base_sec = (BASE / "backend/src/main/java/com/zerocode/app/config/SecurityConfig.java"
                ).read_text(encoding="utf-8")
    assert "/auth/register" not in base_sec
    overlay_sec = (OVERLAY / "backend/src/main/java/com/zerocode/app/config/SecurityConfig.java"
                   ).read_text(encoding="utf-8")
    line = [ln for ln in overlay_sec.splitlines() if "/auth/register" in ln]
    assert len(line) == 1 and ".permitAll()" in line[0]

    ctrl = (OVERLAY / "backend/src/main/java/com/zerocode/app/controller"
            / "MobileAuthController.java").read_text(encoding="utf-8")
    assert '@RequestMapping("/auth")' in ctrl and '@PostMapping("/register")' in ctrl
    impl = (OVERLAY / "backend/src/main/java/com/zerocode/app/service/impl"
            / "MobileAuthServiceImpl.java").read_text(encoding="utf-8")
    assert "passwordEncoder.encode" in impl and "batchInsertUserRole" in impl
    page = (_render(tmp_path) / "frontend" / "mobile" / "src" / "pages" / "register"
            / "index.vue").read_text(encoding="utf-8")
    assert "request('/auth/register'" in page


def test_manifest_pins_h5_to_the_same_origin_path(tmp_path):
    """H5 的 base 必须是 /h5/ 且 hash 路由（ADR-0039 / US-02）。

    base 决定两件事：产物里资源 URL 的前缀（实测 uni build 产出 /h5/assets/*）与路由前缀。
    只改一处就会得到「页面能开、静态资源全 404」或「资源能取、路由跳回 /」这类半截故障。
    """
    m = json.loads((_mobile(tmp_path) / "src" / "manifest.json").read_text(encoding="utf-8"))
    assert m["h5"]["router"] == {"mode": "hash", "base": "/h5/"}
    assert m["vueVersion"] == "3"
    assert m["name"] == "大型应用"


def test_mobile_navigation_targets_match_pages_json():
    """所有跳转目标必须命中 pages.json 登记的路由。

    写错一层的症状很安静：点「登录」没反应或控制台报 navigateTo 失败，页面本身不报错。
    路由文件是唯一真相，守卫代码、请求 401 兜底与页面互跳都从它取路径。
    """
    src = OVERLAY / "frontend" / "mobile" / "src"
    routes = {p["path"] for p in json.loads(
        (src / "pages.json").read_text(encoding="utf-8"))["pages"]}
    for rel in ("App.vue", "pages/index/index.vue", "pages/login/index.vue",
                "pages/register/index.vue", "utils/request.js"):
        text = (src / rel).read_text(encoding="utf-8")
        for m in re.finditer(r"['\"](/?(?:pages)/[A-Za-z0-9_/-]+)['\"]", text):
            assert m.group(1).lstrip("/") in routes, (rel, m.group(1))


def test_nginx_serves_h5_and_keeps_the_api_reverse_proxy(tmp_path):
    """prod 的 /h5/ 由同一个前端 nginx 提供：新块不能挤掉基底的 /api 反代。

    两个 location 共用一份 server 块，改错顺序或整份重写最常见——那样后端接口全 404，
    而 H5 页面本身是好的（页面不调接口时完全看不出来）。
    """
    ng = (_render(tmp_path) / "frontend" / "nginx.conf").read_text(encoding="utf-8")
    assert "location /h5/" in ng and "try_files $uri $uri/ /h5/index.html;" in ng
    assert "location = /h5" in ng and "301 /h5/" in ng       # 少斜杠不许 404
    assert "proxy_pass http://backend:8080;" in ng            # 基底的接口反代还在
    assert "client_max_body_size 50m;" in ng                  # APK/附件上传的体积闸门还在
    assert "alias" not in [ln.strip().split()[0] for ln in ng.splitlines()
                           if ln.strip() and not ln.strip().startswith("#")]


def test_nginx_conf_is_lf(tmp_path):
    """nginx.conf 必须是 LF（本仓库既有踩坑：CRLF 让容器起不来）。

    按字节判断，不用 Git Bash 的 grep/od——那套在 Windows 上会把整份文件误判成 CRLF。
    """
    raw = (_render(tmp_path) / "frontend" / "nginx.conf").read_bytes()
    assert b"\r" not in raw
    assert raw.endswith(b"\n")


def test_dockerfile_bakes_h5_into_the_frontend_image(tmp_path):
    """H5 烘进前端镜像的 html/h5（US-09）：第三个 prod 服务会破坏 prod.py 的两 role 记账。

    断言两头对齐：写产物用的 UNI_OUTPUT_DIR 与 COPY 的源路径必须是同一个值——
    对不上时 COPY 会 404（大声失败），这条把它提前到测试里。
    """
    dk = (_render(tmp_path) / "frontend" / "Dockerfile").read_text(encoding="utf-8")
    outdir = re.search(r"ENV UNI_OUTPUT_DIR=(\S+)", dk)
    assert outdir, "缺少 UNI_OUTPUT_DIR"
    assert f"COPY --from=mobile-build {outdir.group(1)} /usr/share/nginx/html/h5" in dk
    assert "COPY --from=build /src/dist /usr/share/nginx/html" in dk     # 管理端产物照旧
    assert "nginx:1.27-alpine" in dk
    assert "COPY mobile/package.json mobile/package-lock.json ./" in dk  # 先装依赖再拷源码，缓存才有意义


def test_frontend_vite_proxies_h5_with_websocket(tmp_path):
    """管理端 vite 必须把 /h5 连 ws 一起代理给 mobile 服务（US-06/US-07）。

    不带 ws: 页面能开但改一行不热更（改成整页刷新），这类「看起来只是慢」的问题最难被当成 bug 报。
    """
    vite = (_render(tmp_path) / "frontend" / "vite.config.ts").read_text(encoding="utf-8")
    block = vite.split("'/h5'", 1)[1].split("}", 1)[0]
    assert "target: 'http://mobile:8080'" in block
    assert "ws: true" in block
    assert "changeOrigin" not in block      # 保留原 Host：vite 的 DNS-rebinding 守卫只放行 localhost/IP
    assert "optimizeDeps" in vite           # 不锁入口，mobile/index.html 会被当成管理端的第二个入口
    assert "entries: ['index.html']" in vite


def test_dev_service_name_port_and_proxy_target_agree():
    """三处写死的名字必须互相对得上：compose 服务名、vite 代理目标、uni dev server 端口。

    名字对不上时 compose 照样 up 成功，症状是预览里 /h5/ 502——而 502 的日志在管理端
    容器里、服务名在另一个文件里，只有这条测试能一眼指出来。
    """
    meta = {"schema": "zc-app/1", "name": "大型应用", "slug": "bigapp", "scale": "large",
            "stack": compose.MOBILE_STACK, "ports": {"prod": 8210, "dev": 8211},
            "database": {"dev": {}, "prod": {}}, "health": {"frontend": "/", "backend": "/api/health"}}
    doc = yaml.safe_load(compose.render_dev_compose(meta))
    assert "mobile" in doc["services"]
    uni_cfg = (OVERLAY / "frontend" / "mobile" / "vite.config.js").read_text(encoding="utf-8")
    assert "port: 8080" in uni_cfg and "strictPort: true" in uni_cfg    # 端口被占要当场失败，不能静默挪
    assert "host: '0.0.0.0'" in uni_cfg
    assert "../frontend/mobile:/src" in str(doc["services"]["mobile"]["volumes"])


def test_dockerignore_keeps_the_uni_config_but_drops_dependency_trees(tmp_path):
    """上下文卫生：node_modules/dist/unpackage 不进构建上下文，而 mobile/vite.config.js 必须进。

    Docker 的 .dockerignore 是无斜杠模式只匹配根目录那一层：`vite.config.js` 干掉的是
    管理端的陈旧编译产物（Vite 读 .js 优先于 .ts），不能顺手写成 `**/vite.config.js`——
    那会把 uni-app 工程的真配置也排除掉，容器里直接 build 失败。
    """
    lines = [ln for ln in (_render(tmp_path) / "frontend" / ".dockerignore")
             .read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")]
    assert "vite.config.js" in lines and "**/vite.config.js" not in lines
    for pat in ("**/node_modules", "**/dist", "**/unpackage"):
        assert pat in lines, pat


def test_app_name_only_sits_in_double_quoted_hosts():
    """APP_NAME 只能待在双引号宿主或 HTML 文本位里（评审 A4c IMP-1 同类洞）。

    check_name 放行单引号（「O'Brien 仓库」是正常业务名），而 JS 单引号字符串里的名字会
    把脚本写坏——模板里每出现一次就多一个「创建成功、页面空白」的入口。HTML 文本位
    （`>名字<`）另一侧由 NAME_UNSAFE 兜住（`< > & "` 全拒），所以两种宿主都算安全。
    """
    for p in OVERLAY.rglob("*"):
        if not p.is_file() or p.suffix == ".ico":
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"\{\{APP_NAME\}\}", text):
            line_start = text.rfind("\n", 0, m.start()) + 1
            before_in_line = text[line_start:m.start()]
            assert before_in_line.count("'") % 2 == 0, \
                (p, text[max(0, m.start() - 40):m.end() + 20])
            if before_in_line.count('"') % 2 == 1:
                continue                       # 双引号字符串内（JSON / JS / HTML 属性）都安全
            before, after = text[m.start() - 1], text[m.end():m.end() + 1]
            assert (before, after) == (">", "<"), \
                (p, text[max(0, m.start() - 40):m.end() + 20])


def test_mobile_package_json_and_lock_agree(tmp_path):
    """`npm ci` 的前置：lock 的两处 root name 与 package.json 同名，且依赖版本逐个对得上。

    对不上时 prod 构建在 `npm ci` 处失败（ENOLOCK / 锁文件不同步），而 dev 的 `npm install`
    会悄悄把 lock 改写——于是一边红一边绿，最难查的那种不一致。
    """
    mob = _mobile(tmp_path)
    pkg = json.loads((mob / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((mob / "package-lock.json").read_text(encoding="utf-8"))
    assert pkg["name"] == "bigapp-mobile" == lock["name"] == lock["packages"][""]["name"]
    assert "build:h5" in pkg["scripts"] and "dev:h5" in pkg["scripts"]
    deps = {**pkg["dependencies"], **pkg["devDependencies"]}
    for name, spec in deps.items():
        locked = lock["packages"][f"node_modules/{name}"]["version"]
        if spec.startswith("^"):
            # 带 ^ 的（vue）只要求 root 记录的 spec 与 package.json 逐字一致，具体解析版本交给 lock
            assert lock["packages"][""]["dependencies"].get(name) == spec or \
                   lock["packages"][""]["devDependencies"].get(name) == spec, name
        else:
            assert locked == spec, name
    # 移动端与大栈的管理端是两套依赖树，不得共用同一个包名（缓存/卷会串味）
    fe = json.loads((_render(tmp_path) / "frontend" / "package.json").read_text(encoding="utf-8"))
    assert fe["name"] != pkg["name"]
