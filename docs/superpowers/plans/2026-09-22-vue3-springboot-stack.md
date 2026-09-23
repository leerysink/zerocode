# 大型应用栈 vue3-springboot（base-pro 移植）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付 `app/templates/vue3-springboot/` 模板（以 `D:\project\private\base-pro` 为基座移植改造），并让该栈的 dev 预览与 prod 发布全链路跑通。

**Architecture:** 模板是 fork 快照，形状与 `vue3-fastapi` 同构（`frontend/ backend/ schema/ sqlchange/ README.md`），compose 与 `app.json` 仍由平台渲染。平台侧只做四件事：scaffold 渲染器支持二进制文件与「栈自带数据轨即让位」；compose 增加 springboot 分支（redis + `.m2` 卷 + `mvn spring-boot:run`）；健康探针统一 `/api/health` 并按栈取超时；契约按栈追加条款。

**Tech Stack:** Spring Boot 3.2.5 / JDK 17 / MyBatis-Plus 3.5.6 / Spring Security + jjwt / Redis 7 / Vue3 + TS + ant-design-vue 4 / vite 5；平台侧 FastAPI + SQLAlchemy async + pytest。

**规格来源:** [docs/superpowers/specs/2026-09-22-vue3-springboot-stack-design.md](file:///d:/project/zerocode/docs/superpowers/specs/2026-09-22-vue3-springboot-stack-design.md) —— 决策 D-1…D-7、改造清单 C-1…C-9 以其为准，本计划只展开「怎么落」。

**全局约定:**
- `$ref = D:\project\private\base-pro`（参考工程，只读），`$tpl = app\templates\vue3-springboot`。仓内操作都在 `d:\project\zerocode`，PowerShell 分隔符用 `;`。
- 每个 Task 结尾提交一次；**不推送远端**（推送时机归用户）。
- 测试命令统一：`cd d:\project\zerocode; .venv\Scripts\python -m pytest <target> -q`。
- 删文件一律用 DeleteFile 工具，不用 shell 删除命令。
- Phase A/B/C/D 对应规格 §11 的四片，**每片结束都要有真跑证据**，不允许「前三片纸面绿、第四片才第一次启动」。
- **回归环境注意**：本仓可能有并发会话共用同一个 `zerocode-pg16`，全量 `pytest tests` 会撞出
  `DeadlockDetectedError: deadlock detected` 与一批每次不同的 FAILED（实测同一棵树两次跑的红点不重叠）。
  任何红必须先单跑该文件定性，不得直接归因于本次改动，也不得为了跑绿去 stash 别人的在途修改。
- **提交前必验边界**：`git add <file>` 前先 `git diff <file>` 确认该文件的 diff **只含本次改动**。
  并发会话可能把半成品留在同一个文件里（实跑碰到过 `app_service.py` 里他人在途的 `gateway_routes` 引用），
  整文件 add 会提交出一个 import 不存在的坏 commit。
  - **索引也共享**（实跑碰到）：`git add` 与 `git commit` 之间的窗口里，并发会话的一次 commit 可能把
    **我已 stage 的文件连带提交**（我的四个文件落在了 `a90797a`，commit message 却是对方写的）。
    自己 commit 回报 `nothing to commit` 时不要重做，先 `git log --stat -1` 确认内容是否已被收走。
  - **✅ 已裁定（A4 实跑发现 → A4b/A4c 落地）**：`{{APP_NAME}}` 会被内插进机器可解析的文件，名字含
    `< > & " \` 或控制字符时产物直接碎（`pom.xml` 的 `<name>`、Java 字面量、`main.py` 的字符串）。
    用户选定「收紧 `check_name`」（**Task A4b**）：入口一条 400，不做多宿主转义。
  - **名字宿主铁则（A4c 复审 IMP-1 后的全局约束，Phase A 余下 Step 与后续任何新栈均适用）**：
    `check_name` 只拒 `"` 不拒 `'` 与 `%`，所以吃 `{{APP_NAME}}` 的位置**只能是双引号字符串、静态 HTML/Vue
    属性、XML 文本或格式化实参**；单引号 JS 字符串（`:title="'{{APP_NAME}}'"`、`appTitle = '{{APP_NAME}}'`）与
    格式字符串（text block 里直接内插）一律不许 —— 两者都是「创建成功、预览必碎」同一失效族。

---

## Phase A — 渲染器改造 + 模板落仓

### Task A1: scaffold 支持二进制文件按字节 copy

**Files:**
- Modify: `app/deploy/scaffold.py:26-35`
- Test: `tests/test_scaffold.py`

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_scaffold.py` 末尾）

```python
def test_render_copies_binary_file_byte_identical(tmp_path):
    """favicon.ico 这类非 UTF-8 资源必须按字节 copy（read_text 会抛 UnicodeDecodeError → 创建失败回滚）。"""
    import hashlib
    tpl = tmp_path / "tpl"
    (tpl / "frontend" / "public").mkdir(parents=True)
    (tpl / "frontend" / "package.json").write_text('{"name": "{{SLUG}}-fe"}', encoding="utf-8")
    blob = bytes([0, 0, 1, 0, 0, 0, 0, 0, 255, 254, 0, 1, 0, 0, 0, 0])   # 必然解不出 UTF-8
    (tpl / "frontend" / "public" / "favicon.ico").write_bytes(blob)
    scaffold.render(tpl, tmp_path / "out", {
        "SLUG": "s", "APP_NAME": "甲", "DEV_PORT": "1", "PROD_PORT": "2",
        "DEV_DB": "s_dev", "PROD_DB": "s_prod", "TODAY": "20260101"})
    got = (tmp_path / "out" / "frontend" / "public" / "favicon.ico").read_bytes()
    assert hashlib.md5(got).hexdigest() == hashlib.md5(blob).hexdigest()
    assert '"name": "s-fe"' in (tmp_path / "out" / "frontend" / "package.json").read_text(encoding="utf-8")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_scaffold.py::test_render_copies_binary_file_byte_identical -q`
Expected: FAIL — `UnicodeDecodeError: 'utf-8' codec can't decode byte ...`

- [ ] **Step 3: 改实现**

`render` 的复制循环里，把这四行

```python
        text = src.read_text(encoding="utf-8")
        text = PLACEHOLDER.sub(lambda m: str(mapping[m.group(1)]), text)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="")
```

替换为

```python
        _copy_text(src, target, mapping)     # 非 UTF-8 资源（图标/字体）只搬字节，不做占位替换
```

并在 `render` 之前插入模块级函数：

```python
def _copy_text(src: Path, target: Path, mapping: dict) -> bool:
    """按文本渲染单个文件；解不出 UTF-8 即按字节原样 copy 并返回 False（规格 §5.1）。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        text = src.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        target.write_bytes(src.read_bytes())
        return False
    target.write_text(PLACEHOLDER.sub(lambda m: str(mapping[m.group(1)]), text),
                      encoding="utf-8", newline="")
    return True
```

残留自检（`render` 末尾的 `for p in dst.rglob("*")`）**不改**：它已用 `errors="replace"`，二进制不抛。

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_scaffold.py -q`
Expected: PASS（原有 12 条全绿，文本写回的 `newline=""` 语义未变）

- [ ] **Step 5: 提交**

```bash
git add app/deploy/scaffold.py tests/test_scaffold.py
git commit -m "fix(scaffold): copy non-UTF-8 template files byte-for-byte instead of decoding them"
```

---

### Task A2: scaffold 的 demo 种子改为「栈自带数据轨即让位」

**Files:**
- Modify: `app/deploy/scaffold.py:36-44`
- Test: `tests/test_scaffold.py`

- [ ] **Step 1: 写失败测试**

```python
def test_render_skips_demo_seed_when_template_ships_its_own(tmp_path):
    """springboot 模板自带 RBAC 种子，平台不再追加 demo_notes 种子（该表只存在于 fastapi 栈 → 首迁移必红）。"""
    tpl = tmp_path / "tpl"
    (tpl / "sqlchange").mkdir(parents=True)
    (tpl / "sqlchange" / "D20260101_01_rbac_seed.sql").write_text("SELECT 1;", encoding="utf-8")
    out = tmp_path / "out"
    scaffold.render(tpl, out, {
        "SLUG": "s", "APP_NAME": "甲", "DEV_PORT": "1", "PROD_PORT": "2",
        "DEV_DB": "s_dev", "PROD_DB": "s_prod", "TODAY": "20260922"})
    assert [p.name for p in (out / "sqlchange").glob("*.sql")] == ["D20260101_01_rbac_seed.sql"]


def test_render_appends_demo_seed_when_template_has_no_sql(tmp_path):
    """对照（回归钉）：模板数据轨为空时照旧追加，vue3-fastapi 行为一字不变。"""
    tpl = tmp_path / "tpl"
    (tpl / "sqlchange").mkdir(parents=True)
    out = tmp_path / "out"
    scaffold.render(tpl, out, {
        "SLUG": "s", "APP_NAME": "甲", "DEV_PORT": "1", "PROD_PORT": "2",
        "DEV_DB": "s_dev", "PROD_DB": "s_prod", "TODAY": "20260922"})
    assert (out / "sqlchange" / "D20260922_01_seed.sql").is_file()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_scaffold.py -q -k demo_seed`
Expected: 第一条 FAIL（多出一个 `D20260922_01_seed.sql`）

- [ ] **Step 3: 改实现**

把 `render` 中从 `init = dst / "sqlchange" / ...` 到 `init.write_text(...)` 的段落替换为：

```python
    today = mapping["TODAY"]
    # 栈自带数据轨（springboot 的 RBAC 基线）时不追加 demo 种子：demo_notes 是 vue3-fastapi
    # 专有条目，落到别的栈就是首预览迁移必红（规格 §5.1）。
    shipped = sorted((dst / "sqlchange").glob("[VD]*.sql")) if (dst / "sqlchange").is_dir() else []
    if not shipped:
        init = dst / "sqlchange" / f"D{today}_01_seed.sql"
        init.parent.mkdir(parents=True, exist_ok=True)
        # M-2②（终审 F-03）：APP_NAME 进 SQL 字面量前做 PG 标准转义 `'`→`''`（对照探针：含引号名称
        # 原值渲染出引号不闭合的 INSERT，psql --single-transaction 整文件回滚 → 首预览必红且 M1 无删除入口）。
        init.write_text(INIT_SQL.format(
            TODAY=today,
            TODAY_DASH=f"{today[:4]}-{today[4:6]}-{today[6:]}",
            APP_NAME=str(mapping["APP_NAME"]).replace("'", "''")), encoding="utf-8", newline="")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_scaffold.py tests/test_app_service.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add app/deploy/scaffold.py tests/test_scaffold.py
git commit -m "feat(scaffold): let stacks that ship their own sqlchange seed suppress the demo_notes seed"
```

---

### Task A3: 模板骨架落仓（复制 + 包名改名 + 占位符碰撞体检）

**Files:**
- Create: `app/templates/vue3-springboot/backend/**`（92 个 java + `logback-spring.xml` + `mapper/*.xml` + `pom.xml`）
- Create: `app/templates/vue3-springboot/frontend/**`（33 个 src 文件 + 根配置 + `public/favicon.ico`(6991B)、`public/favicon.svg`）

本 Task 只搬运 + 改名，**不改内容**（内容改造在 A4/A5）。产物还不可运行，但先过碰撞体检，后续才敢往上改。

- [ ] **Step 1: 复制后端**（java 包直接落到新包路径，避免「先拷再删」）

```powershell
cd d:\project\zerocode
$ref = 'D:\project\private\base-pro'
$tpl = 'app\templates\vue3-springboot'
robocopy "$ref\backend\src\main\java\com\leerysink\base" "$tpl\backend\src\main\java\com\zerocode\app" /E /XD .scratch logs target uploads /NFL /NDL /NJH /NJS
robocopy "$ref\backend\src\main\resources" "$tpl\backend\src\main\resources" /E /XF application-dev.yml application-prod.yml /NFL /NDL /NJH /NJS
Copy-Item "$ref\backend\pom.xml" "$tpl\backend\pom.xml"
```

Expected: robocopy 退出码 ≤3；`$tpl\backend\src\main\resources` 下只剩 `application.yml`、`logback-spring.xml`、`mapper/*.xml`。

- [ ] **Step 2: 复制前端**

```powershell
robocopy "$ref\frontend" "$tpl\frontend" /E /XD dist node_modules .vscode .scratch /XF *.tsbuildinfo vite.config.js vite.config.d.ts /NFL /NDL /NJH /NJS
```

Expected: `$tpl\frontend\public\favicon.ico` 与 `package-lock.json` 都在（`vite.config.js`/`vite.config.d.ts` 是 base-pro 的 tsc 产物，必须排除）。

- [ ] **Step 3: 全量替换包名**（无 BOM 写回，只碰文本扩展名）

```powershell
$enc = New-Object System.Text.UTF8Encoding($false)
$files = Get-ChildItem -Recurse -File "$tpl\backend","$tpl\frontend" -Include *.java,*.xml,*.yml,*.ts,*.vue,*.json,*.css,*.html,*.md
foreach ($f in $files) {
  $t = [System.IO.File]::ReadAllText($f.FullName, [System.Text.Encoding]::UTF8)
  $n = $t -replace 'com\.leerysink\.base', 'com.zerocode.app'
  if ($n -ne $t) { [System.IO.File]::WriteAllText($f.FullName, $n, $enc) }
}
```

> Windows PowerShell 5.1 的 `Set-Content -Encoding UTF8` 会写 BOM，Java/YAML 带 BOM 是隐雷，故用 `UTF8Encoding($false)`。

- [ ] **Step 4: 改名体检**

```powershell
Get-ChildItem -Recurse -File $tpl | Select-String -Pattern "leerysink" | ForEach-Object { $_.Path + ":" + $_.LineNumber }
(Get-ChildItem -Recurse -File "$tpl\backend\src\main\java").Count
(Get-ChildItem -Recurse -File "$ref\backend\src\main\java\com\leerysink\base").Count
$bad = @(); Get-ChildItem -Recurse -File "$tpl\backend","$tpl\frontend" | Where-Object { $_.Extension -in '.java','.xml','.yml','.ts','.vue','.json','.css','.html','.md' } | ForEach-Object { try { $t = [System.IO.File]::ReadAllText($_.FullName, [System.Text.UTF8Encoding]::new($false, $true)) } catch { $bad += $_.Name; return }; if ($t.Contains([char]0xFFFD)) { $bad += $_.Name } }; "encoding damage: $($bad.Count)"
```

Expected（实跑已核）：
- 第一条**只允许两处**：`backend/pom.xml` 的 `<groupId>com.leerysink</groupId>`（不含 `.base`，本次替换替不到）与 `application.yml` 的 `jwt.secret` 默认值——两处都由 **A4** 收口（Step 2 整文件重写 yml + Step 5 改 pom 坐标），其余必须零命中；
- 第二、三条相等（实测都是 **92**；早期记录的 100 是把 `target/` 产物算进去了）；
- 第四条 `encoding damage: 0`——Step 3 的 `ReadAllText(UTF8)` 遇到非 UTF-8源文件会写成替换符（且替换符序列仍是合法 UTF-8，事后看不出来），所以必须双查「严格解码 + 不含 U+FFFD」。

- [ ] **Step 5: 占位符碰撞体检（本 Task 硬闸门）**

`PLACEHOLDER = \{\{(\w+)\}\}` 对未登记键会抛 `KeyError` → 创建 500 回滚。落仓时就确认：

```powershell
Get-ChildItem -Recurse -File $tpl | Select-String -Pattern '\{\{\w+\}\}' |
  ForEach-Object { $_.Filename + ":" + $_.LineNumber + "  " + $_.Matches[0].Value } | Sort-Object -Unique
```

Expected: **无输出**。base-pro 的插值全部形如 `{{ appTitle }}`（带空格），不匹配该正则（实测 92 java/43 frontend 全树仅 12 个 .vue 里有 93 处插值，java/yml/xml/ts/json 零命中）。
若有输出（即无空格的 `{{word}}` 形态，会被 `PLACEHOLDER` 吃掉并抛 `KeyError`）且确属前端插值
→ 就地改成带空格形态或提为常量，**不许留到后面**（与本 Task 评审后的 A3b 不矛盾：A3b 放的是
「带空格的大写判据外」的插值，无空格的小写形态仍必须在源头消掉）。

> ⚠⚠ 但本 Step 只能保证 `PLACEHOLDER` 不报 `KeyError`，**保不住 `render` 末尾的残留自检**：那条
> 自检用的是宽判据 `"{{" in text`，93 处插值全部撞穿→创建应用 500。已单开 **Task A3b** 收口，
> 不修完再进 A4，会把后续每次「渲染验不过」误判成自己改坏了。

- [ ] **Step 6: 提交**

```bash
git add app/templates/vue3-springboot
git commit -m "feat(template): vendor base-pro backend/frontend into vue3-springboot stack, repackaged to com.zerocode.app"
```

---

### Task A3b: 残留自检判据收窄到「平台占位符形态」（A3 评审 I-1）

**状态：✅ 已完成**（与本 Task 同批提交，必在 A4 之前）

**背景**：A3 评审实跑 `scaffold.render(vue3-springboot 模板)` 抛
`DeployError: 占位符渲染残留: frontend\src\layout\index.vue`。原因：`render()` 末尾的自检是
子串级宽判据 `"{{" in text`，而 Vue 插值带空格（`{{ appTitle }}`）不被 `PLACEHOLDER` 替换，
会原样活到产物里 → **整栈创建应用 100% 500 回滚**。fastapi 栈时代模板里的 `{{` 全是大写形态、
渲染即消失，所以这条判据从不会命中；A3 是第一次往模板里放「渲染后仍存活」的 `{{`。

**为什么不逐处消掉 93 个插值**（v-pre / 提常量）：成本高，且会永久约束 AI 后续生成页面的写法。
平台占位符键一律大写下划线（见 `render` 的 `required`），Vue 插值一律小驼峰，两种形态可分。

**Files:**
- Modify: `app/deploy/scaffold.py`（顶部新增 `RESIDUE` + `render` 末尾自检循环）
- Modify: `.gitattributes`（二进制资源显式钉住，A3 评审 M-1）
- Test: `tests/test_scaffold.py`

- [x] **Step 1: 写失败测试**（三条：插值要能活 / 大写带空格仍要拦 / 真模板能渲染干净）

```python
def test_render_keeps_vue_interpolation_alive(tmp_path):
    """Vue 插值不是平台占位符：{{ appTitle }} 这类必须能原样活过残留自检。"""
    # fixture 里同时放 `{{ appTitle }}`、`{{ record.status === 1 ? '启用' : '停用' }}` 与 `{{SLUG}}`，
    # 断言前两者原样存活、后者被替换成 `s`。


def test_render_flags_spaced_platform_placeholder_as_residue(tmp_path):
    """真事故仍要拦：平台占位符全是大写下划线，带空格写歪就替换不掉（PLACEHOLDER 不吃空格）。"""
    # README.md 写 `{{ APP_NAME }}` → pytest.raises(DeployError, match="APP_NAME")。


def test_render_springboot_template_end_to_end(tmp_path):
    """整栈真模板必须能渲染干净（不留到 A7 才发现）。"""
    # render 真模板，断言 vite.config.ts 与 com/zerocode/app/BaseApplication.java 存在。
```

- [x] **Step 2: 跑测试确认失败** — `pytest tests/test_scaffold.py -q` → 3 failed 12 passed（三条都撞在
  `DeployError: 占位符渲染残留` 上，前两条仍因报错文案不含 token 而 `match="APP_NAME"` 不中）。

- [x] **Step 3: 改实现**

```python
PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
# 残留自检的判据：平台占位符形态（大写下划线，容忍手滑带的空格），不是任意 "{{"。
# 模板里的 Vue 插值 {{ appTitle }} / {{ record.status === 1 ? '启用' : '停用' }} 本就该原样
# 活到产物里（vue3-springboot 有 93 处），宽判据会把整栈创建应用打成 500。
# 平台占位符键全是大写下划线（见 render 的 required），插值全小驼峰，两种形态可分。
RESIDUE = re.compile(r"\{\{\s*[A-Z][A-Z0-9_]*\s*\}\}")
```

`render` 末尾的自检循环改为逐文件 `RESIDUE.search(...)`，报错时把命中的 token 一并带出（方便定位）：

```python
    # 残留自检：任何文件不得残留未渲染的平台占位符
    for p in dst.rglob("*"):
        if not p.is_file():
            continue
        hit = RESIDUE.search(p.read_text(encoding="utf-8", errors="replace"))
        if hit:
            raise DeployError(f"占位符渲染残留: {p.relative_to(dst)} 处有 {hit.group(0)}")
```

`.gitattributes` 补二进制钉（评审 M-1：当前靠 git 内容自判 + `_render_file` 的「解不出 UTF-8」兼容，
两道全兜不住「恰好是合法 UTF-8 的二进制」这个未来形态）：`*.ico *.png *.jpg *.jpeg *.gif
*.woff *.woff2 *.ttf *.eot` 均 `binary`。没加 `app/templates/** text eol=lf`：产物已由
`read_text` 通用换行 + `write_text(newline="")` 收敛成 LF，再改工作区 EOL 只动无关文件。

- [x] **Step 4: 跑绿 + 相关回归** — `tests/test_scaffold.py` 15 passed；scaffold 消费方
  （app_service / apps_api / store_install_flow / schemacheck / compose_render / deploy_basics /
  provision）67 passed。全量红的 12 条经单跑定性为并发会话抢 PG（deadlock），与本改动无关。

- [x] **Step 5: 提交** — `fix(scaffold): narrow the residue self-check to platform-placeholder shape`

---

### Task A4: 后端改造（配置合一 + actuator + 安全白名单 + pom + 构建件）

**Files:**
- Rewrite: `app/templates/vue3-springboot/backend/src/main/resources/application.yml`
- Modify: `.../com/zerocode/app/config/SecurityConfig.java`、`.../com/zerocode/app/BaseApplication.java`、`backend/pom.xml`
- Create: `backend/Dockerfile`、`backend/.dockerignore`

- [x] **Step 1: 确认 profile 只剩一份**

```powershell
Get-ChildItem app\templates\vue3-springboot\backend\src\main\resources -File | Select-Object -ExpandProperty Name
```

Expected: `application.yml`、`logback-spring.xml`。若仍出现 `application-dev.yml` / `application-prod.yml`，用 DeleteFile 工具删除。

- [x] **Step 2: 整文件重写 `application.yml`（C-3 + C-4）**

```yaml
# 单一配置：环境差异全部外置为平台注入的环境变量。
# 标量后不带 `:默认值` 的键（数据源三项、SPRING_DATA_REDIS_HOST、JWT_SECRET、UPLOAD_DIR）一旦
# 缺失，应用启动即失败——刻意不留「静默连上本地库 / 用仓库公开密钥签 token」的降级路径。
server:
  port: ${SERVER_PORT:8080}
  servlet:
    context-path: /api

spring:
  application:
    name: {{SLUG}}
  datasource:
    driver-class-name: org.postgresql.Driver
    url: ${SPRING_DATASOURCE_URL}
    username: ${SPRING_DATASOURCE_USERNAME}
    password: ${SPRING_DATASOURCE_PASSWORD}
    hikari:
      maximum-pool-size: 10
      minimum-idle: 2
      connection-timeout: 30000
  data:
    redis:
      host: ${SPRING_DATA_REDIS_HOST}
      port: ${SPRING_DATA_REDIS_PORT:6379}
      password: ${SPRING_DATA_REDIS_PASSWORD}
      database: ${SPRING_DATA_REDIS_DATABASE:0}
      timeout: 5000ms
      lettuce:
        pool:
          # 必须显式开启：不开则依赖 Boot 从「pool 节点被写过」隐式推断，将来有人删下三行就会静默退化为非池化
          enabled: true
          max-active: 16
          max-idle: 8
          min-idle: 1
          # 默认 -1（池耗尽时无限阻塞），给上限让耗尽时快速失败而不是挂住请求线程
          max-wait: 3000ms
  servlet:
    multipart:
      max-file-size: 10MB
      max-request-size: 50MB
  jackson:
    date-format: yyyy-MM-dd HH:mm:ss
    time-zone: Asia/Shanghai

mybatis-plus:
  mapper-locations: classpath*:mapper/**/*.xml
  type-aliases-package: com.zerocode.app.entity
  configuration:
    map-underscore-to-camel-case: true
    # 默认不打 SQL 日志；预览期想看 SQL 就地改成 StdOutImpl（本文件在应用工程内，AI 可编辑）
    log-impl: org.apache.ibatis.logging.nologging.NoLoggingImpl
  global-config:
    db-config:
      id-type: auto
      logic-delete-field: deleted
      logic-delete-value: 1
      logic-not-delete-value: 0

# 探针与前端同前缀：context-path(/api) + base-path(/) => GET /api/health
# 只放开 health，绝不暴露 env/beans/metrics
management:
  endpoints:
    web:
      base-path: /
      exposure:
        include: health

jwt:
  secret: ${JWT_SECRET}
  expiration: 86400000
  header: Authorization
  token-prefix: "Bearer "

file:
  allowed-types: jpg,jpeg,png,gif,bmp,webp,pdf
  max-size: 10485760
  upload-dir: ${UPLOAD_DIR}

# 接口文档默认关（要开就让 AI 把 enabled 改 true，并在 SecurityConfig 放开 /doc.html、/webjars/**）
springdoc:
  api-docs:
    enabled: false
  swagger-ui:
    enabled: false
knife4j:
  enable: false
  production: true

logging:
  path: ${LOG_PATH:${user.dir}/logs}
  max-history: 180
  max-file-size: 100MB
  level:
    com.zerocode.app: info
```

- [x] **Step 3: `SecurityConfig` 白名单放开 health（C-5）**

把

```java
                        .requestMatchers("/auth/login", "/auth/captcha").permitAll()
```

改为

```java
                        .requestMatchers("/auth/login", "/auth/captcha", "/health").permitAll()
```

> `/health` 是**相对 context-path** 的 pattern，探针实际 URL 是 `/api/health`。spike 实测：不放行则 actuator 被兜底拦成 401，发布体检必失败。

- [x] **Step 4: 启动横幅去品牌、去文档行（C-6）**

`BaseApplication.java` 中把

```java
                  base-pro 基础管理后台 启动成功!
                  后端地址: %s
                  接口文档: %s/doc.html
                ===================================================
                """.formatted(baseUrl, baseUrl));
```

改为（**A4c 返修后的最终形状**：名字作 `formatted` 的实参，不写进格式串）

```java
                  %s 启动成功!
                  后端地址: %s
                ===================================================
                """.formatted("{{APP_NAME}}", baseUrl));
```

> 原计划此处写的是 `{{APP_NAME}} 启动成功!` 直接落在 text block 里，评审 I-1 指出那等于把
> 名字当格式字符串，名字含 `%` 即启动即崩（详 **Task A4c**）。

- [x] **Step 5: `pom.xml` 改坐标 + 补 actuator（C-1）**

原 `<groupId>com.leerysink</groupId>` 不含 `.base`，A3 Step 3 替不到它，此处手工改这四行：

```xml
    <groupId>com.zerocode</groupId>
    <artifactId>{{SLUG}}</artifactId>
    <version>1.0.0</version>
    <name>{{APP_NAME}}</name>
```

并在 `spring-boot-starter-web` 那个 `<dependency>` 之后插入：

```xml
        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-actuator</artifactId>
        </dependency>
```

`<parent>` 的 `3.2.5` 与 `<java.version>17` **不动**（D-6：以 spike 实测基线为准，低于旧规格 02 §4.3 的 `^3.3`，差异在 Task D2 回写规格）。

- [x] **Step 6: 新增 `backend/Dockerfile`（C-8，prod build 用）**

```dockerfile
# syntax=docker/dockerfile:1
FROM maven:3.9-eclipse-temurin-17 AS build
WORKDIR /build
COPY pom.xml ./
COPY src ./src
# BuildKit 缓存挂载：同机跨版本复用依赖，首次之后的构建不再重下（冷启动 410s 的来源就是下载）
RUN --mount=type=cache,target=/root/.m2 mvn -B -DskipTests package

FROM eclipse-temurin:17-jre-alpine
WORKDIR /app
COPY --from=build /build/target/*.jar app.jar
ENV SERVER_PORT=8080 UPLOAD_DIR=/uploads
EXPOSE 8080
ENTRYPOINT ["java", "-jar", "/app/app.jar"]
```

> `target/*.jar` 用通配：artifactId 已被替换成 slug，写死文件名会漂。`-DskipTests` 与 fastapi 栈「build 期不跑测试」同口径。

- [x] **Step 7: 新增 `backend/.dockerignore`**

```
target/
logs/
uploads/
```

（dev 期 `mvn spring-boot:run` 在挂载卷里产出 `backend/target`、`backend/logs`；不排除会把几百 MB 塞进 build context。）

- [x] **Step 8: 平台约定违规体检**

```powershell
Get-ChildItem -Recurse -File app\templates\vue3-springboot | Select-String -Pattern 'D:/project|8082|PG_PASSWORD|application-dev|application-local|leerysink' | ForEach-Object { $_.Filename + ":" + $_.LineNumber + "  " + $_.Line.Trim() }
```

Expected: **无输出**（绝对路径上传目录、8082 固定端口、旧环境变量名、A3 遗留的两处 `leerysink` 全部消失）。

> 实跑勘误：本体检是**全模板**的，而 `8082` 与 `application-dev.yml` 字样还留在
> `frontend/vite.config.ts:29-30`（那是 **A5 Step 1** 的活）。A4 跑时按目录取严：
> `Get-ChildItem -Recurse -File $tpl\backend` 必须零命中，frontend 侧剩这两行属预期，A5 后归零。

- [x] **Step 9: 提交** — `e115a6f`（实跑：渲染后 12 项断言全绿，唯一 residue 在 `vite.config.ts`，属 A5）

```bash
git add app/templates/vue3-springboot/backend
git commit -m "feat(template): port backend to zerocode conventions (single application.yml, actuator on /api/health, prod Dockerfile)"
```

### Task A4b: 应用名字符集收口（阻断 `{{APP_NAME}}` 内插进源码的碎产物路径）

**用户裁定（2026-09-22）**：选「收紧 `check_name`」，不在模板侧规避，也不只记风险。

**背景**：A4 实跑发现 `check_name` 只校 strip + 长度。而 `{{APP_NAME}}` 会被内插进机器可解析的
文件：本栈 `pom.xml` 的 `<name>`（`&` 即 XML 非法）与 `BaseApplication` 的 Java text block
（`\q` 是非法转义 → 编译不过）；fastapi 栈同一形状早就存在（`backend/app/main.py:18`
`APP_NAME = "{{APP_NAME}}"` 含 `"` 即碎）。全仓只有一个渲染入口带名字
（`app_service.py:136` 的 mapping，上游就是 `check_name`），改名路径（:255）也共用同一函数，
所以一处收口就能盖住两个栈。

**为什么不转义**：`< > & " \` 在 Java text block / XML / Python 字面量 / HTML 文本四种宿主里转义规则
各不相同，按目标格式转义需引入多个占位符键（契约面变大，AI 还要学会不用错）；而“应用名带引号或反斜杠”本身
就是少量输入，入口拒掉的成本远低于产物碎掉后排查。

**字符集**：只拒真正写坏源码的——`<` `>` `&` `"` `\` 与控制字符（含换行制表）。**不拒** `'`
（在四种宿主里均安全，SQL 字面量路径已由 scaffold 做 `'`→`''` 转义），也不拒中文/空格/emoji。

**Files:**
- Modify: `app/services/app_service.py`（`check_name` 内加一条，顶部 `import re`）
- Test: `tests/test_app_service.py`、`tests/test_apps_rename_delete.py`

- [x] **Step 1: 写失败测试**

```python
@pytest.mark.parametrize("bad", ['库存<管理>', 'R&D 中心', '设备"台账"', 'C:\\仓库', '巡检\n日志', 'a\x00b'])
async def test_check_name_rejects_source_breaking_chars(bad):
    """名字会被内插进生成工程的 pom/Java/py/html，这些宿主没有通用转义，入口就得拒。"""
    with pytest.raises(DeployError, match="应用名称"):
        app_service.check_name(bad)


@pytest.mark.parametrize("ok", ["来料登记", "库存 管理 v2", "O'Brien 仓库", "巡检·看板", "a" * 100])
async def test_check_name_still_accepts_ordinary_names(ok):
    """对照钉：只拒写坏源码的字符，不顺手把中文/空格/单引号/长度边界一起拒掉。"""
    assert app_service.check_name(ok) == ok
```

> 实跑记注：两条写成 `async def`（本文件 `pytestmark = pytest.mark.asyncio`，同步用例会刷
> 「marked with asyncio but not an async function」告警），`check_name` 本身仍是同步函数。

再加一条改名路径的同口径断言（`tests/test_apps_rename_delete.py` 的
`test_rename_rejects_source_breaking_chars`）：五个坏名字 `PATCH /api/apps/{id}` 全 400 且
「应用名称」在 detail 里，改完已落库的名字仍为「改名前」（不得留半改状态）。

- [x] **Step 2: 跑测试确认失败** — 实跑 `pytest tests/test_app_service.py -q -k check_name` →
  **6 failed, 5 passed**（拒接组全红、对照组已绿，说明「不拒中文/单引号」无需实现）。

- [x] **Step 3: 实现**

`app/services/app_service.py` 顶部 `import datetime, json, shutil` 旁加 `re`；`check_name` 的
空名校验之后插入：

```python
    # 应用名会被内插进生成工程的源码与配置（pom.xml 的 <name>、Java text block 横幅、
    # main.py 的字符串字面量、index.html 的 <title>）——这些宿主没有通用转义规则，
    # 含 < > & " \ 或控制字符的名字会让产物直接编译不过／XML 非法。
    # 与其让坏产物在预览阶段炸开且无从溯源，不如在入口给一条明确的 400。
    if NAME_UNSAFE.search(stripped):
        raise DeployError('应用名称不能包含 < > & " \\ 或换行等控制字符（会写进生成的源码里）')
```

模块级常量（`NAME_MAX` 旁）：

```python
NAME_UNSAFE = re.compile(r"[<>\"\\&\x00-\x1f\x7f]")
```

- [x] **Step 4: 跑绿** — `pytest tests/test_app_service.py tests/test_apps_rename_delete.py -q` → 33 passed；
  受影响面补跑 `tests/test_apps_api.py tests/test_store_install_flow.py tests/test_store_api.py tests/test_ws.py`
  → 31 passed（名字入口唯一，无其他调用方需要同步收口）。

- [x] **Step 5: 提交** — `feat(apps): reject app names that would break generated source files`

---

### Task A4c: A4/A4b 评审返修（I-1 名字里的 `%` 打挂横幅 + I-2 健康路径与模板矛盾）

**来源**：A4+A4b 的两轴评审（轴一 ✅ 边界干净，轴二提出 2 必修 + 3 记账）。两项都不修完不能进 A5：
I-1 造成「创建成功但预览永远起不来」，I-2 把错路径喂给 AI 契约。

**I-1（必修）**：`check_name` 刻意不拒 `%`（「50% 折扣」是正常业务名，且 `%` 在 pom / Java 字面量里合法），
而 A4 落地的横幅把 `{{APP_NAME}}` 直写进 text block —— 那里是 `formatted` 的**格式字符串**，名字里的裸 `%`
被当转换符，启动即抛 `UnknownFormatConversionException`。修法取评审推荐：**名字改为格式实参**（两行都用 `%s`），
而不是在平台侧加拒 `%`（那是把模板的锅转嫁成用户输入限制，且 fastapi 栈名字仍可用 `%`）。

**I-2（必修）**：`HEALTH_BY_STACK["vue3-springboot"]` 仍是 `/actuator/health`，与 A4 落地的 `/api/health`（D-7）矛盾。
该值经 `app.json` → `contract.py:82` 原文进 AI 契约、经 `store/release.py:94` 进商店包 manifest，
留错等于让 AI 按一个 404 的路径去改后端。本栈无存量应用，无历史值需迁移；**B2 的整栈真创建端到端断言仍留 B2**
（那一条还依赖 B1 的 dev-compose 与 `.env` 密钥，现在跑不过）。

**Files:**
- Modify: `app/templates/vue3-springboot/backend/src/main/java/com/zerocode/app/BaseApplication.java`
- Modify: `app/services/app_service.py`（`HEALTH_BY_STACK` 一项 + 两处注释同步为当下事实）
- Test: `tests/test_scaffold.py`、`tests/test_app_service.py`

- [x] **Step 1: 写失败测试**

```python
def test_springboot_banner_keeps_percent_in_app_name_harmless(tmp_path):
    name = "50% 折扣 进度%s完成"
    scaffold.render(SPRINGBOOT_TEMPLATE, tmp_path, {..., "APP_NAME": name, ...})
    java = (tmp_path / "backend" / ... / "BaseApplication.java").read_text(encoding="utf-8")
    body = java.split('"""')[1]                       # 横幅 text block 本体
    assert "%s" in body and "%" not in body.replace("%s", "")   # 只允许 %s 这类合法转换符
    assert '"%s"' % name in java.split(".formatted(")[1]        # 名字以带引号实参出现，不在格式串里


async def test_large_stack_health_path_matches_template():
    assert app_service.HEALTH_BY_STACK["vue3-springboot"] == "/api/health"
```

- [x] **Step 2: 跑红** — 实跑两条新用例：**2 failed**（横幅 body 里裸 `%` 命中 / 路径仍 `/actuator/health`）。

- [x] **Step 3: 实现**

```java
        System.out.println("""

                ===================================================
                  %s 启动成功!
                  后端地址: %s
                ===================================================
                """.formatted("{{APP_NAME}}", baseUrl));
```

```python
HEALTH_BY_STACK = {"vue3-fastapi": "/api/health", "vue3-node": "/api/health",
                   "vue3-springboot": "/api/health", "static": ""}
```

- [x] **Step 4: 真 JVM 实测（纸面通过不算通过）** — 本机 JDK 17.0.10，把渲染产物的 `println` 表达式原样
  塞进单文件 `Mini.java` 编译并运行（一次性验证物，用完已删）：
  ① 现状 rc=0，输出里含 `50% 折扣 进度%s完成`；② **反例**（名字写回格式串）rc=1 且 stderr 是
  `java.util.UnknownFormatConversionException: Conversion = ' '`——证明①不是空跑；③ pom `<name>` 渲染成原值。

- [x] **Step 5: 跑绿 + 提交** — `pytest tests/test_scaffold.py tests/test_app_service.py tests/test_apps_rename_delete.py -q`
  → **50 passed**。

**评审记账项处置**（不另开任务，归到各自所属 Step）：
- M-1：三份 yml 合一后不再有 `dev` profile，logback 走 `!dev` 分支 → 预览期日志由 DEBUG 降 INFO（排障细节变少，
  不黑屏）。与模板自洽，用户想看 SQL 就地改 `application.yml`（已在 C-6 注释里写明）；
- M-2：`stringtype=unspecified` 必须由平台注入的 JDBC 串带上（模板 yml 不给默认值）——**已覆盖**：B1 Step 的
  dev-compose 与 C1 的 prod-compose 注入串都带 `?currentSchema=public&stringtype=unspecified`；
- M-3：prod 未保留 base-pro 的 `logging.level.root: warn`——本栈模板统一 `com.zerocode.app: info`，root 走 Boot 默认，
  不额外拦第三方的 INFO（dev/prod 同构比“少几条噪声”重要，且应用工程内可改）。

**复审追加（同一失效族就地收口，落在 `a90797a`）**：复审确认 I-1/I-2 真闭环（含评审方自己重跑的
真 JVM 正/反例），并补了三个同族小项：
- **IMP-1**：`vue3-fastapi/frontend/src/App.vue:29` 的 `:title="'{{APP_NAME}}'"` 是 **JS 单引号字符串**，
  而 `check_name` 刻意放行 `'` —— 名字 `O'Brien 仓库` 渲染后生成 `title: 'O'Brien 仓库'`，`node --check` 直接
  SyntaxError（复审实跑证据）。改静态属性 `title="{{APP_NAME}}"`，并新增
  `test_fastapi_app_vue_name_sits_in_a_double_quoted_host` 钉住；同一规则已写进全局约定，**A5 Step 5 的
  `appTitle` 也改为双引号宿主**（否则新栈刚交付就带同一个洞）。
- **IMP-2**：横幅钉只挡裸 `%`，没挡「转换符与实参个数不匹配」（`MissingFormatArgumentException` 是 I-1 的另一半）
  → 补 `assert body.count("%s") == 2`。
- **MIN-1**：`%` 的「可用性」半边无测试 → `test_check_name_still_accepts_ordinary_names` 补一项 `50% 折扣`（
  有人反手把 `%` 加进 `NAME_UNSAFE` 时这条会红）。

---

### Task A5: 前端接线与 prod 反代（vite / nginx / Dockerfile / 文案）

**Files:**
- Modify: `frontend/vite.config.ts`、`frontend/index.html`、`frontend/package.json`、`frontend/src/config/index.ts`、`frontend/src/views/login/index.vue`
- Create: `frontend/nginx.conf`（覆盖 base-pro 版）、`frontend/Dockerfile`、`frontend/.dockerignore`

- [x] **Step 1: `vite.config.ts` 的 `server` 块换成 compose 形状（C-7）**

把

```ts
  server: {
    // 本地开发端口，3000 已被同机 snfyxm 占用，改成 3001
    port: 3001,
    proxy: {
      '/api': {
        // 对应 dev 环境的后端端口（application-dev.yml 的 server.port）
        target: 'http://localhost:8082',
        changeOrigin: true,
      },
    },
  },
```

替换为

```ts
  server: {
    // 容器内监听：host 必须 0.0.0.0，端口固定 5173（宿主端口由平台 compose 映射）
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      // backend 是 compose 服务名；后端 context-path 已是 /api，故不写 rewrite
      '/api': {
        target: 'http://backend:8080',
        changeOrigin: true,
      },
    },
  },
```

- [x] **Step 2: 整文件重写 `frontend/nginx.conf`**

```nginx
server {
    listen 80;
    server_name _;
    root /usr/share/nginx/html;
    index index.html;

    # 应用后端反代：context-path 已是 /api，URI 原样透传（proxy_pass 不带路径段）
    # 上传件回读也在这一条里：后端把资源挂在 context 内的 /uploads/**，对外 URL 就是 /api/uploads/**
    location /api/ {
        proxy_pass http://backend:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        # 与 spring.servlet.multipart.max-request-size 对齐，否则大文件先在网关层 413
        client_max_body_size 50m;
    }

    # SPA 路由回落（history 模式刷新不 404）
    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

> 勘误（A5 评审 I-1 后的交付形状，见 Task A5b）：本 Step 初版在此额外写了一段 `location /uploads/ { proxy_pass http://backend:8080; }`，
> 那是错的——`addResourceHandler("/uploads/**")` 注册的是 **context 内**路径，真实地址 `/api/uploads/**`，原样透传只会 404。
> 修法没采「nginx + vite 各补一条改写」（standalone `java -jar` 仍是 404），而是让后端返回自己真实的公开 URL。

> 实跑记注（与 base-pro 版的差异）：原版的 `X-Real-IP` / `X-Forwarded-Proto` / `charset utf-8` /
> `proxy_read_timeout 60s` 四项**不保留**——`IpUtils` 只读 `x-forwarded-for`（已留）；
> `X-Forwarded-Proto $scheme` 在平台中央网关（外部 HTTPS、本地 http）下会把 scheme 误报成 `http`，
> 宁缺毋滥；`charset` 对 SPA（HTML 自带 UTF-8 meta + JSON）无必要；`proxy_read_timeout 60s` 就是 nginx 默认值。

- [x] **Step 3: 新增 `frontend/Dockerfile`**

```dockerfile
FROM node:20-alpine AS build
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/dist /usr/share/nginx/html
```

（`npm run build` = `vue-tsc -b && vite build`，spike 已在容器内跑通过一次；类型不过即构建失败——这是要的。）

> `npm ci` 比 fastapi 栈的 `npm install` 严格，**lock 与 package.json 必须同改名**：Step 6 改了
> `package.json` 的 `name` 后，`package-lock.json` 的两处 root `name`（顶层与 `packages[""]`）也同步吃
> `{{SLUG}}-frontend`，否则渲染出的产物两边名字不一致（铺底期是 `base-frontend`，两边一致所以看不出来）。

- [x] **Step 4: 新增 `frontend/.dockerignore`**

```
node_modules/
dist/
# 陈旧编译产物会遮蔽真配置（Vite 读 vite.config.js 优先于 .ts），别让它们进构建上下文
vite.config.js
vite.config.d.ts
*.tsbuildinfo
```

（后三条是 A5b 补的：`vue-tsc -b` 的产物重定向进 node_modules 后，本地残留仍在，兜一层。）

- [x] **Step 5: 三处品牌文案吃占位符**

- `index.html`：`<title>基础管理后台</title>` → `<title>{{APP_NAME}}</title>`
- `src/config/index.ts` 整文件重写：

```ts
/** 全局可配置项 */
export const appTitle = "{{APP_NAME}}"
export const appSubtitle = 'Vue3 + Spring Boot 3 基础管理后台'
```

> 名字用**双引号**（全局约定「名字宿主铁则」）：`check_name` 拒 `"` 不拒 `'`，单引号宿主下
> 「O'Brien 仓库」会渲染出语法错的产物（A4c 复审在 fastapi 栈 `App.vue` 实跑到）。

- 登录页：`Select-String -Pattern "基础管理后台|base-pro"` 定位 `src/views/login/index.vue` 里写死的品牌字样，改为引用已导入的 `appTitle`；并在表单下方保留一行常驻提示：

```html
          <p class="login-tip">初始账号 admin / 初始密码 admin123，登录后请到「个人中心」修改</p>
```

配套样式追加到该组件 `<style scoped>` 末尾：`.login-tip { color: #94a3b8; font-size: 12px; margin-top: 8px; text-align: center; }`

> 实跑记注：登录页**没有写死的品牌字样**——base-pro 早已把 logo 标题、副标题、dashboard 欢迎语、
> `document.title` 全走 `appTitle`/`appSubtitle`（`Select-String` 实测：只剩 `config/index.ts:2` 与
> `style/theme.css:2` 的注释），所以本 Step 只需补初始账号 tip 一行。tip 的颜色用组件已有的
> `var(--text-muted)`（同文件其余 muted 文本都用变量，其值 `#9ca3af` 与计划写的 `#94a3b8` 同级）。
>
> **记账（全模板实测 4 处，故意不改）**：`git grep -n "基础管理后台"` 在 A5 收口后仍命中 4 处——
> `frontend/src/config/index.ts:3`（登录页副标题，用户可见但只是通用技术栈描述、非品牌）、
> `frontend/src/style/theme.css:2`（首行注释）、`backend/pom.xml:18`（`<description>`）、
> `frontend/public/favicon.svg:1`（`aria-label`）。初版此处只记了两处，漏了 pom 与 favicon（A5 评审 F-1）。
> 四处都非品牌泄露、都在应用工程内（生成后 AI 可改），改它们超出本 Task 的 Files 边界；
> 写死品牌位已全部收敛到 `{{APP_NAME}}`。

- [x] **Step 6: `package.json` 的 name 吃 slug**

`"name"` 现值 → `"name": "{{SLUG}}-frontend"`（与 fastapi 栈同形）。实跑连带改了 `package-lock.json` 的
两处 root `name`（Step 3 的 `npm ci` 同步要求）。

- [x] **Step 7: 提交** — `feat(template): wire vue3-springboot frontend to compose service names and prod nginx reverse proxy`

**验收实跑**（证据，不纸面）：
- 新增 `tests/test_scaffold.py::test_render_springboot_frontend_wiring`（先红：vite 仍 `8082` → 实现后绿）；
  `pytest tests/test_scaffold.py -q` → **18 passed**。（该测试的两条断言在 A5b 被推翻重写，见下。）
- **全模板**违规字样体检归零（口径比 A4 Step 8 更严：`D:/project|8082|PG_PASSWORD|application-dev|application-local|leerysink|base-pro|snfyxm`）——A4 当时留在
  `vite.config.ts:29-30` 的两行已随本 Task 消失，**backend 与 frontend 都是零命中**；
- 占位符清单（大小写敏感）：前端只剩 `{{APP_NAME}}`×2（index.html / config）与 `{{SLUG}}`×3（package.json + lock 两处），
  全在注册键里（整栈渲染绿即证明 `PLACEHOLDER` 不报 `KeyError`、`RESIDUE` 无残留）。

```bash
git add app/templates/vue3-springboot/frontend
git commit -m "feat(template): wire vue3-springboot frontend to compose service names and prod nginx reverse proxy"
```

---

### Task A5b: A5 评审返修（I-1 上传件 URL 打不通 + I-2 vite 配置被静默遮蔽；含 M-1 假绿守卫）

**评审输入**：A5 两阶段评审（范围 `a90797a..684f6f1`）轴一 ✅、轴二 ❌：两条必修 + 一条测试卫生项。

**Files**：`backend/.../service/impl/FileServiceImpl.java`、`backend/.../vo/FileVO.java`、`frontend/nginx.conf`、`frontend/vite.config.ts`（仅注释）、`frontend/tsconfig.node.json`、`frontend/.dockerignore`、`tests/test_scaffold.py`

- [x] **Step 1: I-1 — 上传件 URL 必须是「经那一层反代真能取到」的路径**

事实链（在模板里自己核过，不照抄评审）：`application.yml:7` `context-path: /api` → `WebMvcConfig:24`
`addResourceHandler("/uploads/**")` 注册的是 **context 内**路径 → 真实地址 `/api/uploads/**`；而
`FileServiceImpl:81` 返回裸 `/uploads/...`（`FileVO` 还自称「可直接访问的访问地址」——契约本就与实现不符）。

两案里选了改后端：
- ✗ nginx + vite 各补一条 `/uploads` 改写：能救平台内预览，但 standalone `java -jar`（模板作者本地直跑）仍 404，
  且从此两处代理要同步维护；
- ✓ `FileServiceImpl` 用 `@Value("${server.servlet.context-path:}")` 拼出真实公开 URL：代理只留 `/api` 一条规则，
  dev / prod / 本地直跑三处同形（顺带让 `FileVO.url` 的既有注释成立）。

nginx 侧因此**删掉** A5 初版那段 `location /uploads/`，vite 侧不加特例（两边都写了注释说明为何不需要）。

- [x] **Step 2: I-2 — `vue-tsc -b` 不许往前端根吐 `vite.config.js`**

评审推荐的两案都实测过（探针 `.qoder-tmp/a5b_ts_probe.ps1` + 三个 case 的产物目录，用 base-pro 的 tsc 5.9.3；
因在 `.qoder-tmp/`（已 gitignore）且不入库，**留着供复核**，`powershell -NoProfile -ExecutionPolicy Bypass -File .qoder-tmp\a5b_ts_probe.ps1` 可重现）：

| 配置 | 结果 |
|---|---|
| 现状（composite、不重定向） | exit=0，**根目录出现 `vite.config.js` + `vite.config.d.ts`** → Vite 读 `.js` 优先于 `.ts`，静默遮蔽 |
| ① `tsconfig.node.json` 加 `"noEmit": true` | **exit=2**：`error TS6310: Referenced project ... may not disable emit` → `npm run build` 直接打死 |
| ② 只动 `.dockerignore` | 只挡容器，本地 `vite dev` 仍被遮蔽 |
| ③ `outDir`/`tsBuildInfoFile` 指向 `./node_modules/.tmp/node-cfg`（采纳） | exit=0，根目录无 `.js`，子项目产物全在 node_modules（root 自己的 `tsconfig.tsbuildinfo` 仍在根，不遮蔽任何东西，.dockerignore 兜） |

即：**评审推荐的 ① 不可行**（`typescript: ^5.4.5` 今天解析到 5.9.x，引用型 composite 子项目不得关 emit），改用 ③；
`.dockerignore` 再兜 `vite.config.js` / `vite.config.d.ts` / `*.tsbuildinfo` 一层（本地陈旧产物不进容器）。
`tsconfig.node.json` 里写了 JSONC 注释钉住理由（防后人「清理」掉 outDir 把坑装回去），测试因此按原文断言而不走 `json.loads`。

- [x] **Step 3: M-1 — 拆掉三条只会假绿的 skip 守卫 + 补 lock 断言**

`if not SPRINGBOOT_TEMPLATE.is_dir(): pytest.skip("vue3-springboot 模板尚未落仓")` 在模板落仓后永不触发，
但它会把「模板目录被误删/改名」这种真事故判成 skip（绿），三条全删；另补 `package-lock.json` 两处 root `name`
与 `package.json` 同名的断言（`npm ci` 的前置条件，A5 Step 3 只在纸上说过）。

- [x] **Step 4: 验收实跑**
- 先红：`test_render_springboot_upload_url_is_gateway_visible` + `test_springboot_frontend_config_build_does_not_shadow_vite_config`
  → `2 failed, 18 passed`；后绿：`pytest tests/test_scaffold.py -q` → **20 passed**。
- 相关回归：`pytest tests/{test_scaffold,test_app_service,test_compose_render,test_provision,test_deploy_basics,test_publish_api}.py -q` → **89 passed**。
  （中途一批 setup 报 `ConnectionRefusedError [WinError 1225]`：根因是平台自己的 `zerocode-pg16` 容器在 55 分钟前的
  Docker 重启里没起来（`docker ps -a` 实查 Exited (255)），与本 diff 无关；`docker start zerocode-pg16`（只 start、不重建）后全绿。）
- 同族洞自查：fastapi 栈无 `vue-tsc`（build 是纯 `vite build`）、也无静态/上传挂载 → 无同类；
  `components.d.ts`（unplugin 生成物）本来就在仓，不属遮蔽类，不动。

- [x] **Step 5: 记账（不顺手改）**：M-3 中央网关 `gateway/nginx.conf` 没有 `client_max_body_size`（nginx 默认 **1m**）
  ——大型栈的 50m 上传会在中央网关层先 413。`gateway/` 是并发会话的在途领域，本 Task 只记账，
  **移交 C1（prod 反代按栈取源）一并处理**。

```bash
git add app/templates/vue3-springboot tests/test_scaffold.py docs/superpowers/plans/2026-09-22-vue3-springboot-stack.md
git commit -m "fix(template): make the upload URL gateway-visible and stop vue-tsc from shadowing vite.config"
```

- [x] **Step 6: A5b 复审跟进（Nit 收口）** —— 复审确认 I-1/I-2/M-1 三条均闭环（对方自己重跑了探针，
  独立证实 TS6310 与「root 改 solution-style `files: []` 也救不了①」），只留一条注释措辞 Nit：
  「产物全赶进 node_modules」写得偏满（root 项目自己的 `tsconfig.tsbuildinfo` 仍在根目录），
  已把 `tsconfig.node.json` 的注释与本表改成准确口径（行为不变，无需新断言）。

---

### Task A6: 数据库双轨拆分（结构轨 + RBAC 种子轨）

**Files:**
- Create: `app/templates/vue3-springboot/schema/{schema.sql,README.md}`
- Create: `app/templates/vue3-springboot/sqlchange/{D20260101_01_rbac_seed.sql,README.md}`

来源 `$ref\database\init-postgres.sql`（298 行、12 表 + 索引 + COMMENT + 种子 + 6 条 `setval`）。基线**已是幂等形态**（`CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS` / `ON CONFLICT DO NOTHING`），所以本 Task 是「按边界切两刀」而不是重写 DDL。

- [x] **Step 1: 按文本标记切两段（不手数行号）**

> 执行勘误（两处，都是计划没算到的文本事实）：
> 1. 数据轨的切点**不能**取 `-- 预置数据` 那一行本身——它上一行是 `-- =====` 横幅的收尾，从标记行切会把这条横幅劈成两半（结构轨留个尾巴、种子文件缺个头）。实际从标记行的**前一行**切入，让整条横幅跟数据走。
> 2. 结构轨不是「第 1–11 张表」这么干净：基线里第 12 张是 base-pro 自己的台账表 `schema_history`，连同它的「初始台账登记」INSERT 一起位于种子段之后，切法为 `[0..iStruct)` / `[iData..iHist)` 正好把它整段排除。

```powershell
cd d:\project\zerocode
$src = 'D:\project\private\base-pro\database\init-postgres.sql'
$all = [System.IO.File]::ReadAllLines($src, [System.Text.Encoding]::UTF8)
$iStruct = ($all | Select-String -SimpleMatch '-- 12. SQL' | Select-Object -First 1).LineNumber - 1
$iData   = ($all | Select-String -SimpleMatch '-- 预置数据' | Select-Object -First 1).LineNumber - 1
$iHist   = ($all | Select-String -SimpleMatch '-- 初始台账登记' | Select-Object -First 1).LineNumber - 1
$enc = New-Object System.Text.UTF8Encoding($false)
New-Item -ItemType Directory -Force app\templates\vue3-springboot\schema, app\templates\vue3-springboot\sqlchange | Out-Null
[System.IO.File]::WriteAllLines("$PWD\app\templates\vue3-springboot\schema\schema.sql", $all[0..($iStruct-1)], $enc)
[System.IO.File]::WriteAllLines("$PWD\app\templates\vue3-springboot\sqlchange\D20260101_01_rbac_seed.sql", $all[$iData..($iHist-1)], $enc)
```

Expected: `schema.sql` = 第 1–11 张 `sys_*` 表（含索引与 COMMENT）；种子文件 = 预置数据 + 6 条 `setval`。

- [x] **Step 2: 换掉结构轨文件头**（原头注释讲的是 base-pro 自己的台账机制）

> 执行勘误：横幅里**不写** `app/deploy/sqlrunner/run.sh` 这类平台内部路径——生成的应用交给人/模型看，不该知道平台仓库结构；fastapi 模板的同名文件也是只说「平台每次预览与发布前全量重放」。另补两条本栈才需要的口径：列定义与后端实体逐列对齐（写查询前先读本文件取权威列）、PG16 且需 12+（依赖 identity 序列 / 部分唯一索引 / `CREATE INDEX IF NOT EXISTS`）。

用 SearchReplace 把 `schema/schema.sql` 开头到第一条建表语句前的注释块替换为：

```sql
-- =====================================================
-- {{APP_NAME}}（{{SLUG}}）结构真相 —— 声明式目标态快照，可重放、永不 DROP
-- 平台在每次预览/发布前全量 fold 本文件（app/deploy/sqlrunner/run.sh），因此：
--   · 只写 CREATE TABLE IF NOT EXISTS / 逐列 ALTER TABLE ADD COLUMN IF NOT EXISTS
--        / CREATE INDEX IF NOT EXISTS / COMMENT ON；
--   · 不写数据、不写 DROP、不改列类型（破坏性变更走两步法，见 ../sqlchange/README.md）；
--   · 改结构 = 编辑本文件的目标态；改数据 = 往 ../sqlchange 追加 D 文件。
-- 基座 11 张 sys_* 表不得改名、不得删列（会连带打断登录、侧栏与操作日志）。
-- =====================================================
```

- [x] **Step 3: 给数据轨种子补文件头**（保留原有中文注释）

> 执行勘误：种子正文 L283 附近原有的中文注释里**写着 `schema_history`**（“不像 schema_history 那样…”一类的自述）。不改写它，Step 4 的「零命中」判据必然失败——而这条判据要的恰恰是「本模板不再有任何自带台账概念」。故把该注释改写为只提「平台账本」的中性说法。另：6 条 `setval` 的适用范围注释也补了「两张日志表无预置数据、三张关联表没有 id 列，不要重置成 0（`setval` 对 MINVALUE 1 的序列传 0 会报错）」。

```sql
-- D20260101_01_rbac_seed.sql —— 基座 RBAC 种子（幂等可重放；一经应用不得修改，只能追加新 D 文件）
-- 固定日期 20260101：文件名不经占位替换（渲染器只改内容不改路径），保证永远排在本应用新增文件之前。
```

- [x] **Step 4: 平台账本唯一性体检（规格 §4 的收口）**

```powershell
Get-ChildItem app\templates\vue3-springboot\schema\schema.sql, app\templates\vue3-springboot\sqlchange\*.sql | Select-String -Pattern 'schema_history' | ForEach-Object { $_.Filename + ":" + $_.LineNumber }
Select-String -Path app\templates\vue3-springboot\schema\schema.sql -Pattern 'INSERT|DROP'
```

Expected: 两条都**无输出**——base-pro 自带的第 12 张 `schema_history` 表与其台账 INSERT 已剔除（与平台 `sqlchange_history` 双记账不可共存）；结构轨无数据、无 DROP。

实测：两条命令均无输出（平台账本真名经 `app/deploy/sqlrunner/run.sh:14` 核实为 `sqlchange_history`，不叫 `schema_history`，所以模板里出现 `schema_history` 就是自带台账残留）。

- [x] **Step 5: 幂等性实测（纸面通过不算通过）**

本机 `zerocode-pg16` 需为运行态（不在则 `docker start zerocode-pg16`）：

> 执行勘误（计划这段命令在本机**跑不起来**，三条事实）：
> 1. 容器超用户不是 `postgres`——`docker exec zerocode-pg16 env` 实测 `POSTGRES_USER=zerocode`（与 `app/config.py` 的 DSN 一致），写 `-U postgres` 直接 `FATAL: role "postgres" does not exist`。
> 2. `Get-Content ... -Raw | docker exec -i ... psql -f -` 这条管道会把中文洗成乱码（PowerShell 5.1 控制台编码写进 stdin），种子一多字符就进库了。改用 `docker cp` 文件 + `psql -f` + `-e PGCLIENTENCODING=UTF8`，字节直接进容器不经管道。
> 3. 脚本本身得把所有字符串字面量写成 ASCII：无 BOM 的 `.ps1` 被 PS 5.1 按 GBK 读，全角 `）`（UTF-8 `EF BC 89`）的尾字节会吃掉紧跟的单引号，直接解不开。另外 `docker exec` 的 `-e` 必须在容器名**之前**，`psql -tAc` 必须把 SQL 紧跟在**后面**（否则 `-c` 吞掉 `-U`）。
>
> 还有一条坑不属于脚本、但会误判：**psql 的 NOTICE 走 stderr**，外层 `2>&1` 会被 PowerShell 包成 ErrorRecord 让脚本整体 `ExitCode=1`——幂等重放本来就该吐 24 条 “already exists, skipping”，把它当失败就本末倒置了。故探针内部自行捕获 stderr，只对 `ERROR` 计数。

落地的探针（留在 `.qoder-tmp` 供复核，不删）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .qoder-tmp\a6_idempotency_probe.ps1
```

实测结果（全绿，进程 `OVERALL=0`）：

| 检查 | 实测 | 期望 |
|---|---|---|
| schema#1 / seed#1 / schema#2 / seed#2 四次重放 | 全 `exit=0`、`ERROR=0`（schema#2 NOTICE=24，即逐条跳过） | 幂等可重放 |
| `sys_*` 表数 | 11 | 11（无第 12 张台账表） |
| `schema_history` 表数 | 0 | 0 |
| `sys_menu` | 30 | 30 = 10 菜单/目录 + 20 按钮 |
| `sys_role_menu` | 41 | 41 = admin 全量 30 + common 11 |
| `sys_menu_id_seq.last_value` | 1072 | 1072（`setval` 前移生效，新菜单不会撞基座 id） |
| `octet_length(dept_name)/length` | 9B/3c | 9B/3c（UTF-8 三汉字未被洗） |

探针库 `zc_tpl_probe` 由脚本自己 `DROP DATABASE ... WITH (FORCE)` 收尾，不留残留。

- [x] **Step 6: 两份 README 沿用 fastapi 口径 + 补本栈特有的菜单条款**

```powershell
Copy-Item app\templates\vue3-fastapi\schema\README.md app\templates\vue3-springboot\schema\README.md
Copy-Item app\templates\vue3-fastapi\sqlchange\README.md app\templates\vue3-springboot\sqlchange\README.md
```

在 `vue3-springboot/sqlchange/README.md` 末尾追加下面这段（外层用四反引号围栏，内层 sql 围栏照常）：

````markdown
## 本栈特有：菜单也要走数据轨

路由是前端静态注册的（`frontend/src/router/index.ts`），`sys_menu` 只决定侧栏可见性与路由守卫放行。
新增业务页必须补一个 D 文件，否则页面点开就是 403：

```sql
INSERT INTO sys_menu (id, parent_id, menu_name, menu_type, path, component, permission, icon, sort, visible)
VALUES (200, 0, '工单管理', 'C', 'ticket', 'business/ticket/index', 'biz:ticket:list', 'ProfileOutlined', 20, 1)
ON CONFLICT (id) DO NOTHING;
INSERT INTO sys_role_menu (role_id, menu_id) SELECT 1, 200 ON CONFLICT DO NOTHING;
SELECT setval('sys_menu_id_seq', (SELECT MAX(id) FROM sys_menu));
```

显式写 id（避开基座占用的 1–1072），一个文件一个逻辑变更。
````

> 执行勘误：追加前先逐条核过这段的断言（写给 AI 看的操作手册不能想当然）——路由确实是 `frontend/src/router/index.ts` 静态注册；同文件 `beforeEach`（L139–149）确实「当前路径命中不了菜单集合就跳 `/403`」；`path` 拼接口径在 `frontend/src/utils/menuPath.ts`（顶层写片段即可，拼接后是 `/ticket`）；后端**没有** `@PreAuthorize`，按钮级是 `useUserStore().hasPermission()` 且 admin 恒过。据此把这段扩成「一条 SQL + 4 条注意点」，比计划的两句 more actionable。

- [x] **Step 7: 提交**

> 执行补记：本次一并提交 `tests/test_scaffold.py` 的 `test_springboot_template_ships_both_db_tracks`（红→绿：先 FileNotFoundError 红，拆完 21 passed）。断言口径要经得起文件头反问——**不能**用裸子串计数，因为新写的结构轨横幅本身就写着 “CREATE TABLE IF NOT EXISTS” 与 “永不 DROP”；改为行首 `\nCREATE TABLE IF NOT EXISTS ` 计数 == 11，并显式禁 `DROP TABLE|COLUMN|INDEX` 三种写法。

```bash
git add app/templates/vue3-springboot/schema app/templates/vue3-springboot/sqlchange
git commit -m "feat(template): split base-pro baseline into schema track and idempotent RBAC seed track"
```

已提交 `797c8a4`（6 文件 +397/−14）；提交前回归 `test_scaffold / test_contract / test_app_service / test_deploy_basics / test_sqlrunner / test_publish_api` → **83 passed**（新增两个模板目录不会打破任何枚举模板文件的测试）。

- [x] **Step 8: 复审返修（CodeReview 范围 `668b28f..797c8a4`）**

复审结论：无 Blocking，2 Important + 2 Nit。对方亲自重跑了探针与 pytest（未采信转述），并独立确认了 11 vs 12 的差集恰为台账表、`setval` 空表路径不可达、`{{APP_NAME}}` 无法从 `--` 注释越狱（`check_name` 拒控制字符故插不进换行）。

| 条目 | 自查复算 | 处置 |
|---|---|---|
| I-1 种子残留 `与源项目 init-all.sql 一致` / `源项目的业务字典不迁移` | 成立。且 `init-all.sql` 连文件名都对不上本 Task 的真实基线 `init-postgres.sql`；与本 Task Step 2 自己立的口径（不泄漏仓库结构）直接冲突 | 两处改中性说法（提醒默认密码需改 / 业务字典由本应用自行追加或替换） |
| I-2 README 范例 id=200 与「避开 1–1072」自相矛盾 | 成立，但**根因不是 200 不可用**。实查 `sys_menu` 占用集 = {1, 2, 100–107, 1011–1072}，200 确实是空隙（能跑通）；错在「粗区间 + 区间内范例」示范了到占用区挑号这个危险模式 | 规则与范例两头对齐：范例抬到 1200，规则改写精确占用集 + 「取 >1072」，并补上为何危险（踩到已用 id 时 `ON CONFLICT DO NOTHING` **静默跳过**，菜单不出现也不报错） |
| N-3 `schema_history` 守护只盖两个 .sql | 成立，全树 grep 目前 0 命中，只是守护面缺口 | 断言扩到渲染产物的全部 `.sql`/`.md`，并顺带把 I-1 的泄漏词（源项目/base-pro/init-all）一同钉住 |
| N-4 结构轨横幅内插 `{{APP_NAME}}`与 fastapi（零占位）口径分叉 | 已核实安全，纯风格 | 不改。文件头带应用名便于定位归属，代价只是注释级文本差异 |

返修后实跑：`pytest tests/test_scaffold.py -q` → 21 passed；幂等探针仍 `RESULT: PASS`（改动虽只涉种子注释一行，重跑为求证据而非推断）。

- [x] **Step 9: 再复审（CodeReview 范围 `797c8a4..afec884`）与守护收窄**

再复审：上轮两条 Important 确认闭合（对方还把 `git grep` 面扩到了 .java/.vue/.yml，0 命中），无 Blocking；但**我上一轮的修法本身被推翻了一次**：

| 条目 | 自查复算 | 处置 |
|---|---|---|
| I-1′ 守护把「机制层禁令」写成「词汇禁令」 | 成立，而且刀口就在脚下：下一个 Task A7 要新增模板 README，而「台账为何叫 `sqlchange_history`、不要引入 Flyway 的 `schema_history`」正是该写进 README 的正面解释，会被我自己刚加的断言判成泄漏 | 收窄成两层：`.sql`（会真进库执行，与 Step 4 体检同口径）依旧全词禁；`.md` 放开台账词，只禁参考仓库的私有假设 |
| N-2′ `base-pro` 裸子串误伤 `base-production` | 成立（`'base-pro' in 'base-production'` → True） | 改 `base-pro\b` 正则 |
| N-1′ README 把「有孔的区间」说成整段占用 | 成立（1015–1020、1024–1030 等是空隙） | 改为「已占用 `1`、`2`、`100–107`，并**预留** `1011–1072`」，补上按钮 id 的编号规则（父菜单 ×10 + 序号），空隙例子同步换成 1015 |
| N-3′ 「默认密码」在本栈有两个所指 | 成立：登录页提示写的是「初始密码 admin123」，而 `SysUserServiceImpl.java:124` 新增用户兜底是 `123456` | 种子注释改「初始密码」，与登录页同口径 |

守护非空转的实测（六个合成用例走同一套判据）：`CREATE TABLE IF NOT EXISTS schema_history` / `与源项目一致` / `init-all.sql` / `derived from base-pro` → **全部 CAUGHT**；`registry/base-production:1.2` 与「不要引入 Flyway 的 schema_history」（.md）→ **allowed**。收完后：`test_scaffold` 21 passed、幂等探针仍 `RESULT: PASS`（`OVERALL=0`）。

> 这一轮的真正教训不是「词汇会误伤」，而是：**守护的尺度要比它保护的不变量窄一级**。判据只看「会不会造成平台后果」，不看「看起来像不像旧名字」；否则下一个 Task 写文档时，它会从护栏变成路障。本轮之所以又重跑一次探针，是因为种子的 L14 再次被改——幂等是这个 Task 的核心证据，不能拿「只改了注释」推断。

---

### Task A7: 模板 README + 整栈渲染回归测试

**Files:**
- Create: `app/templates/vue3-springboot/README.md`
- Test: `tests/test_springboot_template.py`（新建）

- [x] **Step 1: 写失败测试**（新建 `tests/test_springboot_template.py`）

> 执行勘误（计划的这段测试代码有三处不能照抄，都已改）：
> 1. **同一步里两个测试互相矛盾**：本步第二个测试断言整棵树 `"{{" not in text`，而最后 `test_template_files_are_strict_utf8_or_known_resource` 自己注明「不能断言 `b"{{" not in`：Vue 插值本就该活到产物里（见 Task A3b）」。模板里确实有 `{{ appTitle }}`、`{{ userStore.userInfo?.nickname }}` 这样的产品形态，按前者的写法直接红。→ 统一收到 `RESIDUE = rb"\{\{\s*[A-Z][A-Z0-9_]*\s*\}\}"`（与 `scaffold.RESIDUE` 同口径），并改成**字节版**以便连着二进制文件一起扫。
> 2. **测试不得依赖仓库之外的路径**：计划写 `REF_FAVICON = D:/project/private/base-pro/...` + `skipif 不在就跳`，换台机器就静默 skip，而「二进制有没有被当文本重新编码」恰恰只在别的机器上才容易出现。→ 改为 pin 基线指纹（`FAVICON_SHA256 = e7773a39…f3821`，已实测与参考工程同哈希）+ 断言「渲染产物字节 == 模板字节」，两道都不依赖仓外文件。
> 3. **`@pytest.mark.skipif(not TEMPLATE.is_dir())` ×7 全部去掉**：模板早在 A3 就落仓了，这层守卫现在只会把「目录真的丢了」判成通过（永久静默 skip 的测试等于没测试）。同时丢掉计划里那个未使用的 `gates` 变量。
>
> 另外：`base-pro` 的判据带词边界（与 A6b 同口径，避免误伤 `base-production`）；根 README 只留导览与铁律，双轨细则**只在** `schema/README.md` 与 `sqlchange/README.md` 里各写一份（两处各写一遍就是将来漂移的来源）。


计划草稿（以下代码按上面三条勘误落地，与最终的 `tests/test_springboot_template.py` 不完全一致）：

```python
# tests/test_springboot_template.py
"""大型应用栈模板的渲染体检：整棵目录树必须能被 scaffold 渲染成可运行的应用工程。"""
import hashlib
import re
from pathlib import Path
import pytest
from app.deploy import scaffold

TEMPLATE = Path(__file__).resolve().parents[1] / "app" / "templates" / "vue3-springboot"
REF_FAVICON = Path("D:/project/private/base-pro/frontend/public/favicon.ico")
MAPPING = {"SLUG": "bigapp", "APP_NAME": "大型应用", "DEV_PORT": "8211", "PROD_PORT": "8210",
           "DEV_DB": "bigapp_dev", "PROD_DB": "bigapp_prod", "TODAY": "20260922"}
PKG = Path("com") / "zerocode" / "app"
gates = [pytest.mark.skipif(not TEMPLATE.is_dir(), reason="vue3-springboot 模板尚未落仓")]


def _render(tmp_path):
    out = tmp_path / "app"
    scaffold.render(TEMPLATE, out, MAPPING)
    return out


@pytest.mark.skipif(not TEMPLATE.is_dir(), reason="vue3-springboot 模板尚未落仓")
def test_template_shape():
    assert (TEMPLATE / "backend" / "pom.xml").is_file()
    assert (TEMPLATE / "frontend" / "src" / "main.ts").is_file()
    for junk in ("target", "logs", ".scratch"):
        assert not (TEMPLATE / "backend" / junk).exists()
    for junk in ("dist", "node_modules"):
        assert not (TEMPLATE / "frontend" / junk).exists()
    assert not (TEMPLATE / "frontend" / "vite.config.js").exists()      # tsc 产物不带
    assert not (TEMPLATE / "backend" / "src" / "main" / "resources" / "application-dev.yml").exists()


@pytest.mark.skipif(not TEMPLATE.is_dir(), reason="vue3-springboot 模板尚未落仓")
def test_render_has_no_residue_and_no_foreign_brand(tmp_path):
    out = _render(tmp_path)
    for p in out.rglob("*"):
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        assert "{{" not in text, p.relative_to(out)
        assert "leerysink" not in text and "base-pro" not in text, p.relative_to(out)


@pytest.mark.skipif(not TEMPLATE.is_dir(), reason="vue3-springboot 模板尚未落仓")
def test_render_replaces_coordicates_and_title(tmp_path):
    out = _render(tmp_path)
    pom = (out / "backend" / "pom.xml").read_text(encoding="utf-8")
    assert "<artifactId>bigapp</artifactId>" in pom and "<name>大型应用</name>" in pom
    assert "spring-boot-starter-actuator" in pom and "3.2.5" in pom
    assert "com.zerocode.app" in (out / "backend" / "src" / "main" / "java" / PKG /
                                  "BaseApplication.java").read_text(encoding="utf-8")
    assert "大型应用" in (out / "frontend" / "index.html").read_text(encoding="utf-8")
    assert "http://localhost:8211" in (out / "README.md").read_text(encoding="utf-8")


@pytest.mark.skipif(not TEMPLATE.is_dir(), reason="vue3-springboot 模板尚未落仓")
def test_render_keeps_favicon_byte_identical(tmp_path):
    """二进制资源走字节 copy（Task A1）：图标必须与参考工程逐字节一致。"""
    if not REF_FAVICON.is_file():
        pytest.skip("参考工程 favicon 不在，无法做字节比对")
    out = _render(tmp_path)
    assert (out / "frontend" / "public" / "favicon.ico").read_bytes() == REF_FAVICON.read_bytes()


@pytest.mark.skipif(not TEMPLATE.is_dir(), reason="vue3-springboot 模板尚未落仓")
def test_render_ships_rbac_seed_and_no_demo_notes_seed(tmp_path):
    """Task A2 的实战面：本栈数据轨只有 RBAC 基线，平台不得追加 demo_notes 种子。"""
    out = _render(tmp_path)
    assert sorted(p.name for p in (out / "sqlchange").glob("*.sql")) == ["D20260101_01_rbac_seed.sql"]
    seed = (out / "sqlchange" / "D20260101_01_rbac_seed.sql").read_text(encoding="utf-8")
    assert "INSERT INTO sys_menu" in seed and "setval(" in seed and "demo_notes" not in seed
    snap = (out / "schema" / "schema.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS sys_user" in snap
    assert "schema_history" not in snap and "INSERT" not in snap.upper()


@pytest.mark.skipif(not TEMPLATE.is_dir(), reason="vue3-springboot 模板尚未落仓")
def test_template_wiring_targets_compose_service_names(tmp_path):
    out = _render(tmp_path)
    vite = (out / "frontend" / "vite.config.ts").read_text(encoding="utf-8")
    assert "http://backend:8080" in vite and "port: 5173" in vite and "8082" not in vite
    ng = (out / "frontend" / "nginx.conf").read_text(encoding="utf-8")
    assert "proxy_pass http://backend:8080" in ng and "try_files" in ng
    assert "client_max_body_size 50m" in ng
    assert re.search(r"^COPY nginx\.conf /etc/nginx/conf\.d/default\.conf$",
                     (out / "frontend" / "Dockerfile").read_text(encoding="utf-8"), re.M)
    dk = (out / "backend" / "Dockerfile").read_text(encoding="utf-8")
    assert "maven:3.9-eclipse-temurin-17" in dk and "--mount=type=cache,target=/root/.m2" in dk
    y = (out / "backend" / "src" / "main" / "resources" / "application.yml").read_text(encoding="utf-8")
    for env in ("SPRING_DATASOURCE_URL", "SPRING_DATASOURCE_USERNAME", "SPRING_DATASOURCE_PASSWORD",
                "SPRING_DATA_REDIS_HOST", "SPRING_DATA_REDIS_PASSWORD", "JWT_SECRET", "UPLOAD_DIR"):
        assert "${%s}" % env in y, env
    assert "base-path: /" in y and "include: health" in y
    sec = (out / "backend" / "src" / "main" / "java" / PKG / "config" /
           "SecurityConfig.java").read_text(encoding="utf-8")
    assert '"/health"' in sec          # 探针不放行则 401，发布体检必挂


RESOURCE_SUFFIXES = {".ico", ".png", ".jpg", ".jpeg", ".gif", ".woff", ".woff2",
                     ".ttf", ".eot", ".jar"}


@pytest.mark.skipif(not TEMPLATE.is_dir(), reason="vue3-springboot 模板尚未落仓")
def test_template_files_are_strict_utf8_or_known_resource(tmp_path):
    """收口 A1 的盲区：渲染器对解不出 UTF-8 的文件只搬字节（A1），所以错编码的文本
    （GBK/UTF-16）会被静默原样复制、占位符永远不替换——模板侧只允许「严格 UTF-8 文本」
    与「已知资源扩展名」两类，第三条路直接红。（A1 的 `_render_file` docstring 指名依赖本测试）
    """
    offenders = []
    for p in sorted(TEMPLATE.rglob("*")):
        if not p.is_file():
            continue
        rel = str(p.relative_to(TEMPLATE))
        raw = p.read_bytes()
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError:
            if p.suffix.lower() not in RESOURCE_SUFFIXES:
                offenders.append(f"{rel}：非 UTF-8 且不是已知资源件")
            elif b"{{" in raw:
                offenders.append(f"{rel}：资源件里含 {{{{ 字节，渲染器不会替换它")
    assert offenders == []
    # 渲染产物逐文件扫字节：不得残留「平台占位符形态」（大写键，容忍空格）。
    # 注意不能断言 `b"{{" not in`：Vue 插值本就该活到产物里（见 Task A3b）。
    out = _render(tmp_path)
    for p in out.rglob("*"):
        if p.is_file():
            assert not re.search(rb"\{\{\s*[A-Z][A-Z0-9_]*\s*\}\}", p.read_bytes()), p.relative_to(out)
```

- [x] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_springboot_template.py -q`
Expected: `test_render_replaces_coordicates_and_title` FAIL（缺 `README.md` → FileNotFoundError），其余已绿。

实跑：**2 failed, 5 passed**。第二条不是模板缺陷，是我自己写错的——`TypeError: cannot use a string pattern on a bytes-like object`（拿 str 正则去扫 `read_bytes()`）。改完得 1 failed / 6 passed，红的恰好只剩「缺 README」那一条，才进入 Step 3。教训：看到「预期外多一条红」不要当噪声抹掉，也不要当成模板问题去改模板。

- [x] **Step 3: 写 `app/templates/vue3-springboot/README.md`**

> 执行勘误：草稿里的每条断言落笔前先核过源码/配置，不照抄——接口前缀（`application.yml:7` `context-path: /api` + `utils/request.ts:5` `baseURL: '/api'`）、SQL 日志开关的真实键名（`mybatis-plus.configuration.log-impl`，默认 `NoLoggingImpl`）、接口文档的开关与要放开的三个路径（`springdoc.*.enabled` / `knife4j.enable` + `/doc.html`、`/webjars/**`、`/v3/api-docs/**`）、三个镜像名（两个 Dockerfile 实读：`maven:3.9-eclipse-temurin-17` / `eclipse-temurin:17-jre-alpine` / `redis:7-alpine`）、环境变量缺值即启动失败的既有设计（yml 头注就是这个意思，直接引用）。同时把草稿里的「数据变更写 `sqlchange/V*.sql`」按本栈实情改成 `D*.sql` + 菜单三件套，且不与子 README 重复细则。

计划草稿正文（以下为原计划内容，实际落笔按上面勘误调整）：

```markdown
# {{APP_NAME}}（{{SLUG}}）

zerocode 大型应用：Vue3 + Spring Boot 3 管理后台，自带登录 / 部门 / 用户 / 角色 / 菜单 /
字典 / 操作日志 / 登录日志全套基座。预览地址：http://localhost:{{DEV_PORT}}

## 开始之前

- **首次预览要等几分钟**：后端在容器里 `mvn spring-boot:run`，第一次要下载全部 Maven 依赖
  （实测约 7 分钟，之后约 1 分钟）。期间日志面板一直滚，**不要重复点「重启」**。
- 拉不到 `maven:3.9-eclipse-temurin-17` / `eclipse-temurin:17-jre-alpine` / `redis:7-alpine`
  时说明本机直连 Docker Hub 不通，先配镜像加速源再重试。
- 初始账号 `admin` / `admin123`（登录页也有提示），**登录后请到「个人中心」改密**。

## 目录

- `frontend/` Vue3 + TS + ant-design-vue，开发期由 vite 代理 `/api` → 后端
- `backend/` Spring Boot 3.2.5，包名固定 `com.zerocode.app`，接口不要再写 `/api` 前缀
- `schema/schema.sql` **结构真相**：每次预览/发布前全量重放，只写幂等 DDL
- `sqlchange/` **数据轨**：按文件名升序增量执行，一经应用不得修改；改数据、加菜单都在这里
- `deploy/` 与 `app.json`：平台管理，禁止读写

## 加一个业务页面（三件套，缺一页面点不开）

1. `frontend/src/views/<模块>/index.vue`，并在 `frontend/src/router/index.ts` 注册路由
2. `backend` 对应 `controller` / `service` / `mapper` / `entity`
3. `sqlchange/D<YYYYMMDD>_<NN>_menu_<模块>.sql` 往 `sys_menu` 插菜单/按钮并授权给角色 1

## 常用改动位置

- 想看 SQL 日志 / 开接口文档：改 `backend/src/main/resources/application.yml`
- 连接串、Redis 地址、`JWT_SECRET`、上传目录全部由平台注入环境变量，代码里不要写死
```

- [x] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_springboot_template.py tests/test_scaffold.py -q`→ 实跑 `test_springboot_template` **7 passed**（与 `test_scaffold` 21 passed 分两次跑，共 28）。

- [x] **Step 5: 全量回归**

Run: `pytest tests -q` → **438 passed**（263s，全绿，无 fail 无 skip 新增）。Phase A 未碰编排，任何红都是本相引入——本次没有。

- [x] **Step 6: 提交**

```bash
git add app/templates/vue3-springboot/README.md tests/test_springboot_template.py
git commit -m "feat(template): stack README and full-render contract tests for vue3-springboot template"
```

- [x] **Step 7: 复审返修**（评审范围 `7bf8220..842d27d`，无 Blocking，2 Important + 2 Nit 全部收口）

| # | 评审意见 | 复算结果 | 处置 |
| --- | --- | --- | --- |
| I-1 | README 把「SecurityConfig 需放开文档路径」写成待办，且真正的文档屏蔽开关是 `knife4j.production` 而非 `knife4j.enable` | 实读 `SecurityConfig.java:57-59` 确认 `/doc.html`、`/swagger-ui/**`、`/v3/api-docs/**`、`/webjars/**` **已** permitAll；WebSearch knife4j 官方配置参考证实 `production=true` 才使 `/doc.html`、`/v3/api-docs` 返回错误 JSON | README「想开接口文档」改成三处开关并注明屏蔽的是哪一个；同时改掉 `application.yml:79` 头注里同一条陈旧说法（同族洞自查，不只在 README 修） |
| I-2 | README 讲了 Phase B 才存在的事实：`redis:7-alpine` 与「容器里 `mvn spring-boot:run`」 | `grep -r` 整棵模板树：`redis` 0 命中（compose 尚未写），`mvn spring-boot:run` 0 命中 | 摘掉 `redis:7-alpine`，只留两个 Dockerfile 实读镜像；命令改成不写死的描述「用 Maven 现场启动（不打 jar）」。Step 3 勘误里那条「三个镜像名（含 redis:7-alpine）」是当时误记，保留原文不改，由本条钉住 |
| N-3 | junk 判据只钉 `backend/`、`frontend/` 两个固定子路径，且漏 `.env`/`.DS_Store`/`*.tsbuildinfo`/IDE 目录 | `Get-ChildItem -Recurse -Force` 实测模板树 151 个文件、上述名称 0 命中（今天干净，但深层 `.../target` 会漏检） | 抽 `_junk(tree)` 整棵树 rglob；清单补 `.env`/`.DS_Store`/`Thumbs.db`/`.idea`/`.vscode`/`.tsbuildinfo`/`.pyc`；**刻意不收 `.mvn`**（maven wrapper 是合法件）。新增 `test_guards_are_not_vacuous`：四层深各塞一个杂物必须全数报出、`base-production` 必须放行——干净树上门禁是绿的，没有这条就不知道它是否还在工作 |
| N-4 | `rf"\b{foreign}"` 缺尾词边界，`\bbase-pro` 仍会命中 `base-production` | 部分采纳：一刀切加 `\b` 尾巴会放过 `leerysink123` 这类带后缀的真实泄漏 | 两条判据按各自误伤风险分开写：`FOREIGN = (r"\bleerysink", r"\bbase-pro\b")`（延续 A6b「守护尺度比不变量窄一级」） |

> 返提交 `5a8acc3` 后的复审（`842d27d..5a8acc3`）又抓到一条 Important：**我对 I-2 的修法本身不合格**。摘掉字面命令 `mvn spring-boot:run` 只删了症状，留下的描述「在容器里用 Maven 现场启动（不打 jar）」依旧与同树的 `backend/Dockerfile` 正面冲突（L7 `mvn -B -DskipTests package` → L11 `COPY target/*.jar` → L14 `java -jar`）。一并还有条 Nit：两个镜像都被归给「两个 Dockerfile」，实则同出后端那一个（前端是 `node:20-alpine` / `nginx:1.27-alpine`）。
>
> 修正后的口径：只写**能在树里找到出处**的事实——「首次构建要等几分钟」直接引用后端 Dockerfile 自己的「冷启动 410s」注释与 BuildKit 缓存卷，镜像按前/后端归位列全四个。教训：**返修时要拿它反驳的那份源码再读一遍，而不是只把评审点名的那个词改掉**——只删词不换实质，下一轮评审必定还在同一条上。本次全量 439 passed，复审返修后 `test_springboot_template` + `test_scaffold` 29 passed。

## Phase B — dev 预览链路

### Task B1: dev compose 的 springboot 分支 + `.env` 运行密钥

**Files:**
- Modify: `app/deploy/compose.py`
- Test: `tests/test_compose_render.py`

- [x] **Step 1: 写失败测试**（追加到 `tests/test_compose_render.py`）

> 执行勘误（三条，都是动笔前核源码得出的）：
> 1. 密钥占位行不再单独模板化（计划里那个 `_ENV_SECRETS = "JWT_SECRET=__JWT_SECRET__…"` 没采用）：密钥位与生成循环都从 `STACK_SECRETS` 派生。两处各存一份键列表，将来往 `STACK_SECRETS` 加第三个密钥就会只改一处、静默漏一个位。
> 2. 补两条计划没有的断言：`test_m2_volume_name_is_single_source_of_truth`（与 `fe_node_modules` 同一条规矩：改卷名只动 `M2_VOLUME_NAME`，挂载条目与顶层声明不脱钩）；生成值的字符集必须不含 ` " $ \`（带 `$` 会被 compose 再展开一次、带空格会碎词）。
> 3. 旧 `test_render_dev_compose_unknown_stack_raises` 本步**不会变红**（它拿 vue3-springboot 当未知栈，此时确实还不支持），要到 Step 3 实现后才红——计划预告的「同时变红」发生在实现后而不是写测试时，属预期。


```python
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
```

- [x] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_compose_render.py -q`
Expected: 新用例 FAIL（`栈模板未支持: vue3-springboot`）；旧 `test_render_dev_compose_unknown_stack_raises` 同时变红——它拿 vue3-springboot 当未知栈，属预期改判，Step 4 处理。

实跑：**3 failed / 21 passed**（shape、m2 单源、密钥生成三条红）。新补的 `unknown_stack_still_raises`（vue3-node）与 `fastapi_untouched` 本就是绿的——前者是「未交付栈仍报错」的当前事实，后者是回归钉，它们不该在本次变红。

- [x] **Step 3: 实现卷常量与栈分支**

`app/deploy/compose.py`：`import re` 旁加 `import secrets`。在 `FE_VOLUME = _fe_volume()` 之后插入：

```python
M2_VOLUME_NAME = "m2"           # Maven 本地仓库：per-app（compose 自动加项目前缀），跳容器共享会被无锁并发写坏
M2_MOUNT_PATH = "/root/.m2"

# 栈私有运行密钥：缺则生成、有则保留。口令不进数据库、不进仓库、AI 禁读（deploy/ 是禁区）。
STACK_SECRETS = {"vue3-springboot": ("JWT_SECRET", "REDIS_PASSWORD")}


def _m2_volume() -> str:
    """与 _fe_volume() 同构：挂载条目与顶层声明同源。"""
    return f"{M2_VOLUME_NAME}:{M2_MOUNT_PATH}"


M2_VOLUME = _m2_volume()
```

在 `_MIGRATE_SVC` 之后插入新 dev 模板与密钥段：

```python
_DEV_COMPOSE_SB = """name: zc-__SLUG__-dev
networks:
  default: {}
  zcnet:
    external: true
services:
__MIGRATE_SVC__
  redis:
    image: redis:7-alpine
    command: ["redis-server", "--requirepass", "${REDIS_PASSWORD}"]
    networks: [default]
  backend:
    image: maven:3.9-eclipse-temurin-17
    working_dir: /src
    volumes:
      - ../backend:/src
      - __M2_VOL__
    environment:
      SPRING_DATASOURCE_URL: jdbc:postgresql://zerocode-pg16:5432/${DEV_DB}?currentSchema=public&stringtype=unspecified
      SPRING_DATASOURCE_USERNAME: ${DEV_USER}
      SPRING_DATASOURCE_PASSWORD: ${DEV_PASSWORD}
      SPRING_DATA_REDIS_HOST: redis
      SPRING_DATA_REDIS_PASSWORD: ${REDIS_PASSWORD}
      SERVER_PORT: "8080"
      JWT_SECRET: ${JWT_SECRET}
      UPLOAD_DIR: /src/uploads
    command: mvn -B spring-boot:run
    networks: [default, zcnet]
    depends_on:
      migrate:
        condition: service_completed_successfully
      redis:
        condition: service_started
  frontend:
    image: node:20-alpine
    working_dir: /src
    volumes:
      - ../frontend:/src
      - __FE_VOL__
    command: sh -c "npm install && npm run dev -- --host 0.0.0.0 --port 5173"
    ports:
      - "__DEV_PORT__:5173"
    depends_on:
      - backend
volumes:
  __FE_VOL_NAME__: {}
  __M2_VOL_NAME__: {}
"""

_ENV_SECRETS = """JWT_SECRET=__JWT_SECRET__
REDIS_PASSWORD=__REDIS_PASSWORD__
"""
```

`render_dev_compose` 的分支与替换链改为：

```python
    if stack == "static":
        tpl, fragments = _FE_ONLY, ()
    elif stack == "vue3-fastapi":
        tpl, fragments = _DEV_COMPOSE, (_MIGRATE_SVC,)
    elif stack == "vue3-springboot":
        tpl, fragments = _DEV_COMPOSE_SB, (_MIGRATE_SVC,)   # 迁移闸门完全复用（同库同轨同镜像）
    else:
        raise DeployError(f"栈模板未支持: {stack}", status=500)
    sources = tpl + "".join(fragments)          # 本次渲染用到的全部标记面
    y = (tpl
         .replace("__MIGRATE_SVC__", _MIGRATE_SVC)
         .replace("__DEV_PORT__", str(dev_port))
         .replace("__FE_VOL__", _fe_volume())
         .replace("__FE_VOL_NAME__", VOLUME_NAME)
         .replace("__M2_VOL__", _m2_volume())
         .replace("__M2_VOL_NAME__", M2_VOLUME_NAME)
         .replace("__SLUG__", slug))
```

`render_env` 的 `return (...)` 改为先赋局部变量再追加密钥位（真值/生成由 `ensure_deploy_files` 收口）：

```python
    e = (_ENV_TMPL
         .replace("__DEV_PORT__", str(meta["ports"]["dev"]))
         .replace("__PROD_PORT__", str(meta["ports"]["prod"]))
         .replace("__DEV_DB__", dev.get("db", ""))
         .replace("__DEV_USER__", dev.get("user", ""))
         .replace("__DEV_PASSWORD__", dev.get("password", ""))
         .replace("__PROD_DB__", prod.get("db", ""))
         .replace("__PROD_USER__", prod.get("user", ""))
         .replace("__PROD_PASSWORD__", prod.get("password", "")))
    if STACK_SECRETS.get(meta.get("stack")):
        e += _ENV_SECRETS.replace("__JWT_SECRET__", "").replace("__REDIS_PASSWORD__", "")
    return e
```

`ensure_deploy_files` 里，在 `for k in _CRED_KEYS:` 保留循环**之前**插入：

```python
    # 栈私有密钥：本次没带值 → 沿用 .env 既有值 → 还没有就现场生成
    # （发布/重启都会重渲染 .env，冲空在跑栈的密钥会让 Redis 与 token 全线失配）
    for k in STACK_SECRETS.get(meta.get("stack"), ()):
        if not _env_get(e, k):
            e = _env_set(e, k, _env_get(prev, k) or secrets.token_urlsafe(32))
```

- [x] **Step 4: 改掉旧栈闸门用例的改判**

`tests/test_compose_render.py`：删除旧 `test_render_dev_compose_unknown_stack_raises`（已被 Step 1 最后一条取代）；`test_render_dev_compose_no_marker_residue` 的栈元组扩为 `("vue3-fastapi", "static", "vue3-springboot")`；`test_templates_still_carry_markers` 的模板元组扩为 `(compose._DEV_COMPOSE, compose._FE_ONLY, compose._DEV_COMPOSE_SB)`。

> 执行补强：除了上面三条，另在 `test_templates_still_carry_markers` 里钉住 `__M2_VOL__`/`__M2_VOL_NAME__` **只在** SB 模板里（否则 fastapi/static 产物将来漏带 m2 标记时，零残留断言对它没牙齿）。

- [x] **Step 5: 跑测试确认通过**

Run: `pytest tests/test_compose_render.py -q` → 实跑 **23 passed**（24 旧集删 1 条 + 新增 5 条）。

额外真校（不靠自家 `yaml.safe_load`）：把渲染出的 SB 产物与一份 `.env` 落到 `.qoder-tmp/b1_compose_check/deploy/`，用 docker 自己的解析器验：

````
docker compose -p zc-bigapp-dev --env-file .env -f dev-compose.yml config --quiet   → exit=0
  SPRING_DATASOURCE_URL: jdbc:postgresql://zerocode-pg16:5432/bigapp_dev?currentSchema=public&stringtype=unspecified
  volumes: {source: m2, target: /root/.m2}        顶层 `m2: {name: zc-bigapp-dev_m2}`   ← per-app 前缀得证
  ports: published "8211"                          redis command: [redis-server, --requirepass, …]
```

意义：compose 的 `${}` 插值与命名卷前缀是平台真实行为，不是我们对其的猜测；`zcnet` 外部网络已存在（本机 `docker network ls` 实核）。

- [x] **Step 6: 提交**

```bash
git add app/deploy/compose.py tests/test_compose_render.py
git commit -m "feat(compose): render vue3-springboot dev stack (private redis, .m2 volume, mvn spring-boot:run) with generated runtime secrets"
```

> 执行补一笔事故记录（不抹）：改 `compose.py` 时一次 SearchReplace 的 `original_text` 多带了 `_FE_ONLY` 整块，把它整块删掉了（工具报 success，不会提醒）。靠 `git diff --numstat` 发现只有 1 行删除才兑住——预期只有 `return (_ENV_TMPL` → `e = (_ENV_TMPL` 那一行。教训：**改完模板块必得用 numstat 对删除行数**，光看 diff 的新增侧看不出自己删了什么。

- [x] **Step 7: 评审返修**（范围 `96d9185..7019e2f`，无 Blocking；2 Important + 4 Nit 处置如下）

| 编号 | 评审意见 | 复算 | 处置 |
|---|---|---|---|
| I-1 | `.env` 里 `REDIS_PASSWORD` 为空时，compose 会起一个 `--requirepass ""` 的**静默无密码** Redis（评审方真跑容器：PING→PONG、requirepass 空）。`ensure_deploy_files` 会回填，但任何人手工清过 `.env` 就静默裸奔 | 成立。`render_env` 产出的恰恰就是空位，回填是事后动作，产物本身必须自己拒收 | 三个密钥位（`appredis.command`、`SPRING_DATA_REDIS_PASSWORD`、`JWT_SECRET`）统一改 `${VAR:?not backfilled, see deploy/.env}`。实测：`--env-file good.env config --quiet` → exit=0；`empty.env` → **exit=1** 且逐条点名三个变量。纠正评审的一处事实：`${VAR:?}` 失败码是 **1**，不是它猜的 15。错误文案先写过中文，因全角括号/花括号有解析风险改成纯 ASCII |
| I-2 | dev 用「挂源码就地 mvn」，`backend/target`、`backend/logs` 会写回宿主；而 `files.py IGNORE_DIRS` 与 `packages.py EXCLUDE_DIRS` 只认 `build` → 文件树被 .class 页满、`source_hash` 每预览一次就变（分叉误报）、包体背几百 MB | 成立，两条链都读过量目录代码复核 | 两处名单各加 `target`/`logs`，并补测：`test_tree_ignores_and_nests` 扩 `backend/target/classes/App.class` + `backend/logs/app.log`；`test_store_packages.py` 新增 `test_source_hash_ignores_maven_build_churn`。**`uploads/` 刻意不加**——上传件算不算包内容是个产品口径（不加：每次上传都算分叉；加：作者的演示图进不了包），没定下来前不悄悄替它做决定，已就这一条向用户提问 |
| N-3 | redis 服务名 `redis` 与 zcnet 上可能存在的同名容器有 DNS 歧义（backend 同时挂 default + zcnet） | 成立（平台确实全平台共用 zcnet） | 服务名改 `appredis`，`SPRING_DATA_REDIS_HOST` 跟随；测试补 `svc["appredis"]["networks"] == ["default"]`（redis 连 zcnet 都不上）与 `backend.networks == ["default", "zcnet"]` 两侧断言 |
| N-4 | 旧栈「不得被改动」用「拿实现重放替换链」的镜像断言——会跟着模板一起漂；镜像断言还漏了 static | 成立 | 改为 golden 文件：新增 `tests/golden/dev-compose.{fastapi,static}.yml`，`test_legacy_stacks_match_golden_files` 逐字节比；`.gitattributes` 加 `tests/golden/*.yml text eol=lf`（否则 Windows 检出翻 CRLF 自找误红） |
| N-5 | 「SB 目前不落 prod-compose」没有钉子，将来 C 阶段之前被人顺手补上无人察觉 | 成立 | 新增 `test_springboot_gets_no_prod_compose_yet`：`ensure_deploy_files` 后 `prod-compose.yml` 不存在，且 `render_prod_compose` 抛 `DeployError(status=400)` |
| N-6 | 镜像名断言散在测试里 | 部分采纳 | 镜像名保留在 shape 用例（那是本栈的契约本体，不是回归钉），只把「旧产物回归」这一职责挪给 golden |

> 返修补记：改完 compose 模板必须回去重跑**真 docker 校验**（本 Step 的 good/empty 两跑），不能只看 `yaml.safe_load` 绿——`${VAR:?}` 是 compose 的语法，YAML 层根本看不见它。

- [x] **Step 8: 复审返修**（范围 `7019e2f..87acae2`，复审判定 I-2/N-3/N-4/N-5/N-6 合格，但 I-1 的修法撞出一条新的假绿路径）

| 编号 | 复审意见 | 复算（我自己重跑的证据） | 处置 |
|---|---|---|---|
| I-1′ | `${VAR:?}` 是 compose 的**文件解析期**闸门，不只 `up`：`.env` 空密钥时 `down`/`ps`/`logs` 全 exit=1。而 `stop_dev` 把非零降级成一条 warning 后照样落 `stopped` → 容器一个没停、dev 端口仍被占用、UI 显示「已停止」（restart 更糟：紧接的 up 因旧端口占用而挂） | 完全成立，独立复算：用 HEAD 渲染的 SB 产物 + 空密钥 `.env`，`down`/`ps` 均 **exit=1** 且错误原文就是 `error while interpolating services.appredis.command.[]: required variable REDIS_PASSWORD is missing a value`（一开头我加了 `--quiet` 跑，怕错的是旗标而不是插值，去掉后同样的 1） | `stop_dev` 在 `down` 前先 `ensure_deploy_files`（幂等，内容一致就不写），凭据从库里取、缺位时沿用 .env 既有值。两条新测：`test_stop_dev_backfills_unbackfilled_secrets_before_down`（红→绿，并钉住补位不冲空凭据、不新增 docker 调用）、`test_stop_dev_leaves_a_healthy_env_untouched`（健康 `.env` 停栈前后字节不变）。**不改** `code != 0 → 只记 warning` 的老收口：那是停栈路径共有的既有语义（prod 侧有同构用例），扩大范围属另一件事 |
| N-1 | `target`/`logs` 按裸名在任何深度屏蔽，`logs` 是名单里第一个业务通用词 | 复审实测当前零误伤，但风险真实：中文后台里 `target`（目标/KPI）与 `logs`（日志页）都是常见业务目录名，一个 `frontend/src/views/target/` 会被文件树永久藏掉、并被静默排除出包与 `source_hash` | 改为**路径锁窄**：两处名单各自回到裸名集不含 target/logs，新增 `IGNORE_PATH_TAILS`/`EXCLUDE_PATH_TAILS = (("backend","target"),("backend","logs"))`。packages 侧判文件（需「路径里连续出现」），files 侧判目录节点（祖先剪掉后就递不到，尾匹配够）——两边各自注明为什么不写成同一个。manifest 多录一项 `exclude_path_tails`。补两条反向钉子：业务 `views/target`、`views/logs` 必须仍然可见/进包；原 `"/target/" in f` 形式的断言同步改成锁到 `src/backend/target/` |
| N-3（新） | `files.py` 注释里「模型也会被引去看编译产物」这一半本次没修：`agent/tools.py list_dir` 不吃 `IGNORE_DIRS` | 成立，属同族老洞（`node_modules` 一样对 AI 可见），不是本次引入 | 删掉这半句承诺，不悄悄扩范围 |
| N-2 | 计划的 C1 草稿仍写 `redis` 服务名、密钥位无 `:?` | 成立（草稿写于返修前） | C1 头部加「以 B1 返修后的 `_DEV_COMPOSE_SB` 为准」注记，并把「停栈路径也要能解析」一并写进去 |
| N-4 | `EXCLUDE_DIRS` 是持久化指纹（`app_instance_link.baseline_hash`）的输入 | 成立但本次零影响：当前生产代码只写不比（全仓仅 install 写、测试读），且存量树里 0 个 `backend/target`。另：裸名那版从未入库（同一串未发布改动里就被收窄） | 不改代码；已记在本表，将来再动这两份名单就是一次需要重刷 baseline 的迁移事件 |

> 本 Step 的教训：`${VAR:?}` 类「产物自身硬拒」的闸门，必须同时问一句「**除了启动，还有谁要解析这份文件**」。
> 只验 `config`/`up` 是不够的：`down` 走的是同一个解析器，而平台对 `down` 的失败是吞掉的。

- [x] **Step 9: 三轮收 Nit**（范围 `87acae2..62783b8` 的复审判定无 Blocking/Important，4 条 Nit 全部吃掉）

| 编号 | 意见 | 复算 | 处置 |
|---|---|---|---|
| N-A | 补位是 `stop_dev` 新增的抛点（只读目录 / 未支持的栈 / `app["slug"]`），而 `restart_dev` 不像 `prod_restart` 那样 try/except 包停栈 → 可能把「栈卡死时唯一的自救通道」打死，UI 卡在 stopping | 成立（今天不可达：`_check_scale_stack` 已保证 stack 必在两个已交付模板里，库里实测无第三值无 NULL；但路径形状不该依赖这个） | `ensure_deploy_files` 包 try/except，失败只记 warning 后照旧 `down`（退回修前行为）。新测 `test_stop_dev_still_downs_when_the_backfill_fails`——写它时差点自己坑一次：`boom` 先写成了 `async def`，真实函数是同步的，抛出的异常藏在永不 await 的协程里，**不补位也会绿**；改回同步后才见到它先红、实现后转绿才对 |
| N-B | `down` 前重写 `dev-compose.yml`，而 down 按**文件里的服务名**移除：模板改过名（本系列自己就 `redis`→`appredis`）时旧名容器成孤儿 | 成立，与 I-1′ 同症状类；日常路径被「平台启动一律跑 `cleanup_orphans`（不带文件的 `-p` 按 label 收）」掩着，但窗口存在 | `down` 补 `--remove-orphans`（dev 项目名由 `name: zc-<slug>-dev` 锁定，不误伤 prod）；真 docker 实测不带栈时 `down --remove-orphans` → **exit=0**，幂等前提不破。旧测 `test_stop_dev_downs_stack_and_resets_status` 钉住这个旗标；`test_stop_dev_leaves_a_healthy_env_untouched` 扩到连 `dev-compose.yml` 字节一起钉 |
| N-C | 两套判据在「产物名落在文件上」时口径不一致（files 判目录、packages 判文件） | 成立且不是新洞（`EXCLUDE_FILES` 早就是这个形状：`deploy/.env` 同样树里看得见、包里不收） | 不改判据，把这行口径差写进 `_is_ignored_dir` 的 docstring，不假装两边完全同形 |
| N-D | 注释把产物落点说成「固定的」，实际由多处决定：拆多模块则变 `backend/<module>/target`；`application.yml` 在应用工程内、AI 可改 `logging.path` | 成立（逐条查了 pom 是单模块、logback defaultValue 只是兜底），当前不漏但说法撑不住 | 两份注释都改成「落点由 compose 的 `../backend:/src` 挂载 + 模板默认值共同决定，任一变动要回来对一眼名单」；并在 `test_compose_render.py` 给这个前提补一枚钉子（`svc["backend"]["working_dir"] == "/src"`） |

> 另：C1 注记里补一句——`prod_stop`（`app/deploy/prod.py:152`）**目前没有** ensure，prod 模板一旦也带 `${VAR:?}`，I-1′ 会在 prod 侧原样复发。

- [x] **Step 10: 产品裁定落地——`backend/uploads/` 不进市场包**（I-2 留下的待决口径，已向用户确认）

用户选的口径：**不收，算运行时数据**（与 `target`/`logs` 同族）。两个后果各自钉住：

- 不进包：安装者不被塞作者的示例图（`test_write_package_excludes_secrets_and_artifacts` 扩一条）。
- 不进 `source_hash`：装完之后有人在应用里传一张图，不会让下一次发布报成分叉
  （新测 `test_runtime_uploads_are_not_packaged_and_not_fingerprinted`：新增 + 覆盖两张图，指纹不变）。
- **但文件树照旧看得见它们**：`files.py` 的名单刻意不含 uploads（`test_tree_ignores_and_nests` 钉住 `uploads in kids`）。
  不进包 ≠ 不让用户看到自己的文件，两侧不同形是有意的，两边注释各写明一句。
- 旁证（不拿未落地的 C1 草稿当事实）：模板自己的 `backend/.dockerignore:3` 已把 `uploads/` 排除在构建上下文之外，
  `backend/Dockerfile:13` 的 `UPLOAD_DIR=/uploads` 也不是工作区路径——本条只管 dev 的 `UPLOAD_DIR: /src/uploads`。
- 存量影响：两个模板下都没有 track 的 `backend/uploads/**` 文件（`git ls-files` 实核 0 命中）。但得说准：
  已装实例的 `app_instance_link.baseline_hash` 是用**旧名单**写下的（`app/store/install.py` 写入），名单一变，同一棵工作区
  下一次算出的 `source_hash` 就与它**一次性不可比**。今天零影响（全仓只有写入处与测试读取，无比较逻辑），但 M3-b 落地
  分叉判定之前必须先重算/迁移 baseline，不能拿新旧值直接比。

---

### Task B2: `HEALTH_BY_STACK` 收敛到 `/api/health` + 大型栈创建放行

**Files:**
- Modify: `app/services/app_service.py`（本次只动 `_check_scale_stack` 里的 R-C 注释；`HEALTH_BY_STACK` 已随 A4c 到位）
- Modify（评审返修）: `app/store/packages.py` / `app/api/files.py` 的名单注释
- Test: `tests/test_app_service.py`

> 前提校正（B1 评审 Nit-7 复算后的当下事实）：本 Task 标题里的两件事**都已是既成**——`HEALTH_BY_STACK` 的
> springboot 条目在 **A4c** 就写成了 `/api/health`；创建闸门 `_check_scale_stack` 只看
> `(TEMPLATES / stack).is_dir()`，而模板自 **A3** 已落仓，所以大型栈今天已经能建。
> B2 的真实增量收敛为：① 端到端断言（真建库、真落盘：`app.json` 的 health、`deploy/dev-compose.yml` 的
> maven/appredis 服务、`deploy/.env` 的两个生成密钥）；② 把 `R-C` 那条注释从「M1 仅交付 vue3-fastapi」
> 改成当下事实（否则会误导下一个读它的人以为大型栈还被拦着）。
> 另记一条已确认的局限：B1→B3 之间大型栈预览会「前端 200 就报 running，而后端 mvn 还在下依赖」
> （`engine.start_dev` 只等前端）——那是 B3 的活，**B4 的验收证据必须等 B3**。

- [x] **Step 1: 写测试**（追加到 `tests/test_app_service.py` 末尾）

> 落地时改名 `test_create_large_app_opened_with_api_health_probe` → **`test_create_large_app_end_to_end_lands_the_springboot_stack`**：
> 「放行」与「健康路径」各自都已有窄断言（A3/A4c），本用例的职责是端到端落盘，名字要说清这件事。
> 断言在下面 Step 的草稿上扩了几处，都按**当下事实**写：`redis:7-alpine` 换成 `appredis`（B1 N-3 已改名）、
> 种子判定从「今天日期的文件不存在」换成 `sqlchange/*.sql` 全等 `== ["D20260101_01_rbac_seed.sql"]`（后者才真能抓住 demo 让位失效）、
> 补 `DEV_DB == f"{SLUG}_dev"`、`not prod-compose.yml`（N-5 的端到端版）、README 渲染真端口。
> 模块头只补了 `import json` 与 `from app.deploy import compose`（`datetime` 最终没用上：日期式断言被全等取代）。

```python
async def test_create_large_app_end_to_end_lands_the_springboot_stack():
    """大型栈端到真创建（B2）：真建库、真渲染、真落盘，把 Phase A+B 的接缝钉住。…"""
    sf = make_session_factory(make_engine())
    app = await app_service.create_app_project(
        sf, name="大型", slug=SLUG, scale="large", stack="vue3-springboot", root=str(ROOT))
    assert app["scale"] == "large" and app["stack"] == "vue3-springboot"
    assert (ROOT / "backend" / "src" / "main" / "java" / "com" / "zerocode" / "app").is_dir()
    assert sorted(p.name for p in (ROOT / "sqlchange").glob("*.sql")) == ["D20260101_01_rbac_seed.sql"]
    meta = json.loads((ROOT / "app.json").read_text(encoding="utf-8"))
    assert meta["health"] == {"frontend": "/", "backend": "/api/health"}
    dev_y = (ROOT / "deploy" / "dev-compose.yml").read_text(encoding="utf-8")
    assert "maven:3.9-eclipse-temurin-17" in dev_y and "appredis" in dev_y
    assert not (ROOT / "deploy" / "prod-compose.yml").exists()
    env = (ROOT / "deploy" / ".env").read_text(encoding="utf-8")
    assert compose._env_get(env, "JWT_SECRET") and compose._env_get(env, "REDIS_PASSWORD")
    assert compose._env_get(env, "DEV_DB") == f"{SLUG}_dev"
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"http://localhost:{app['dev_port']}" in readme and "{{DEV_PORT}}" not in readme
```

- [x] **Step 2: 跑测试确认失败**（计划写的是红；实测**无红阶段**，缘由如下）

Run: `pytest tests/test_app_service.py -q` → **21 passed，本用例落地即绿、没有红阶段**。

> 如实记下原因（不假装走过 TDD 的红）：前提校正版已说过——`HEALTH_BY_STACK` 的 springboot 条目在 **A4c** 就是
> `/api/health`，创建闸门看的是 `(TEMPLATES / stack).is_dir()` 而模板在 **A3** 已落仓，`dev-compose.yml`/`.env` 由 **B1** 渲染。
> 三条接缝都是既成事实，所以本用例的职责是**回归钉**而不是驱动新实现。
> 为排除「空转绿」做了变异验证：把 `HEALTH_BY_STACK["vue3-springboot"]` 临时改回 `/actuator/health` → 本用例
> **失败**（`assert {'backend': '/actuator/health'} != {'backend': '/api/health'}`），改回后复绿。其余读盘断言天然不空转：
> 文件不在就直接 `FileNotFoundError`，密钥没回填则 `_env_get` 返回空串让 `and` 断言红。

- [x] **Step 3: 改实现**

`HEALTH_BY_STACK` 已随 **Task A4c** 提前完成（I-2 必修），本次零代码改动。

`_check_scale_stack` 的 R-C 闸门**不删码**（模板目录落地后 `is_dir()` 自然为真，`vue3-node`/`static` 继续 400），只把注释改成当下事实：

```python
    # 裁定 R-C：模板未交付的栈在此提前 400（不放行则 scaffold.render 会冒「栈模板不存在 500」）。
    # vue3-springboot 已由 base-pro 移植交付（A3）；vue3-node / static 仍未开放。
```

> 同口径的过期说法在本文件 `test_create_app_rejects_bad_scale_stack` 的注释里也有一处（「M1 仅 vue3-fastapi」），一并改掉；
> `git grep "M1 仅"`（排除 docs/）现已零命中。向导前端文案里的同类过期说法属 **D2**，不在本 Task 动。

- [x] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_app_service.py tests/test_apps_api.py tests/test_contract.py -q` → **34 passed**
（`vue3-springboot` 无存量应用，`app.json` 无历史值需迁移）

- [x] **Step 5: 提交**

> 提交信息改写为当下事实（不写 “open creation / unify health path”——两件事都在 A3/A4c 做完了，本 Task 只加端到端钉 + 改注释）。

```bash
git add app/services/app_service.py tests/test_app_service.py docs/superpowers/plans/2026-09-22-vue3-springboot-stack.md
git commit -m "test(app): pin the large-stack creation path end to end, and refresh the stale R-C gating comment"
```

- [x] **Step 6: 评审返修**（范围 `995db8b..b6fc3f0`，把 `26e95b5` 的 uploads 切片一并交审；无 Blocking，1 Important + 4 Nit）

| 编号 | 评审意见 | 复算 | 处置 |
|---|---|---|---|
| I-1 | `EXCLUDE_PATH_TAILS` 不分栈生效（`_is_excluded`/`_iter_src` 不读栈信息），但注释给的三条理由全只在 vue3-springboot 成立；小型栈今天根本没有 uploads 目录，将来 AI 加个上传功能，真素材会被静默排除出包而树里照旧看得见 | 成立，探针直调 `_is_excluded`：`backend/uploads/2026/09/avatar.png` → True，对所有栈同视；`git grep -i uploads -- app/templates` 15 行（11 个文件）全在大型栈 | 采纳 (a)「把口径说满」而非 (b)「按栈取名单」：裁定说的是「上传目录 = 运行时」这个语义，不是某个模板的目录约定；按栈取会造出「同一路径在不同栈含义不同」的新不对称。注释改成明写「本条不分栈 + 真素材请放 `frontend/src/` 下自建目录」；**配套缺口**登记到 D1（契约里告诉 AI 这是运行时目录，今天契约全文不提 uploads） |
| N-1 | 这条口径差只有单向注释（packages.py 写了 files.py 不含它，反过来没有） | 成立，评审做了变异：往 `IGNORE_PATH_TAILS` 加 uploads 只有 `test_tree_ignores_and_nests` 红 | files.py 名单上方补一句「不收与不可见是两件事，想对齐两边前先读 packages.py 那段」 |
| N-2 | `assert not prod-compose.yml.exists()` 与 C1 正面冲突，而 C1 Step 5 的改判清单没列它 → C1 当天会红在名字叫「端到端落盘」的用例里，易被误判成回归 | 成立（计划漏项，代码本身没错） | 不改断言（今天它是真的），在该行加「C1 放行后要翻案」+ 把它列入 C1 Step 5 的改判清单 |
| N-3 | Step 10 「指纹只可能变得更稳定」说满了：`baseline_hash` 是旧名单写下的，新旧一次性不可比 | 成立（`install.py` 只写不比，今天零影响，但 M3-b 要比较） | 计划该段改写为「一次性不可比 + M3-b 前必须重算/迁移」；不改代码 |
| N-4 | B2 的 Files 行号已漂（写的 22-23,58-61，实为 23-24、60-70） | 成立 | 改成不依赖行号的说法，并补上本次动的两个文件 |

> 本轮回评审额外交到的两件事实（不需要行动，但记下来免得后人重查）：
> ① 端到端用例做了 9 组变异逐条挨打（只有 `DEV_DB` 那句打不红——它钉的是「库里凭据→.env」，不是模板 mapping），
> 已按这个事实给该行加注释并把 mapping 的锅指给 `test_store_install_seam.py`；
> ② 共享环境回收与 fastapi 同形（库/角色/dbcred/两表行/目录逐列比对），异常路径也收得干净（插 raise 实跑过）。

- [x] **Step 7: 复审收 Nit**（范围 `b6fc3f0..9dc0015`，复审判定无 Blocking/Important，4 Nit 全部吃掉）

| 编号 | 意见 | 复算 | 处置 |
|---|---|---|---|
| Nit-1 | 建议路径 `frontend/src/assets` 在**两个模板里都不存在**（`git ls-files "app/templates/*assets*"` 零命中），写成那样会让读者以为脚手架已有这个目录 | 成立，已自己复算：fastapi 的 `frontend/src` 无子目录，SB 的只有 api/config/layout/router/store/style/utils/views。同时直调名单验过建议本身安全：`_is_excluded(("frontend","src","assets","a.png"))` → False、`_is_ignored_dir(("frontend","src","assets"))` → False | 改成「模板里今天没有 assets/，要自建；`frontend/src/**` 两份名单都不碰，放了就进包」（D1 登记里同一句同步） |
| Nit-2 | 登记表里的计数「14 处」与当下的树差一；D1 那句「AI 没有任何信息能推出」说满了 | 成立：`git grep -i uploads -- app/templates` → **15 行**（11 个文件）；而 SB 模板的 `WebMvcConfig.java:24`、`FileServiceImpl.java:40/90` 确实把 uploads 当运行时存——信息不是没有，只是不在契约里 | 计数改「15 行（11 个文件）」；D1 那句改成「契约里没有任何一条**规则**这么说（模板代码是给人读的演示，不约束 AI）」 |
| Nit-3 | N-4 声称「改成不依赖行号」，但新文本仍写死 `L23-24`/`L67-68`（今天都对，但漂的正是这个） | 成立，属自己没做到自己写的道理 | 两处行号删掉，只留符号名 |
| Nit-4 | N-2 的改判列进了 C1 Step 5，但 C1 Step 7 的 `git add` 没跟着扩 → C1 当天会把刚翻案的断言留在工作区外 | 成立（计划内部不一致） | `git add` 补 `tests/test_app_service.py` 与计划本体，并加一句说明 |

> 复审同时独立确认了三件我本想自己验的事：I-1 选 (a) **不需要动代码**（代码本就不分栈，(a) 的任务是让注释说实话），
> 且该口径已有测试钉住（`test_store_packages.py` 的 fixture 把 `app.json` 写成 `vue3-fastapi`，而 uploads 断言照旧成立）；
> 「不分栈」不会被读成「files.py 也不分栈收 uploads」（两边名单都无栈维度，且已互为指针）；
> 「一次性不可比」不夸大（`baseline_hash` 全仓只有 `install.py` 写入 + 测试读长度，无比较逻辑）。

---

### Task B3: dev 联合健康判定 + 按栈超时

**Files:**
- Modify: `app/deploy/settings.py`（`DEFAULTS` 加 `deploy.health_timeout_large` + 新函数 `health_timeout(cfg, stack)`）
- Modify: `app/deploy/engine.py`（两个 waiter 收敛到 `_all_200`；新增 `wait_dev_health` 与 `JOINT_HEALTH_STACKS`；`start_dev` 的健康等待段按栈分支）
- Test: `tests/test_engine.py`（`_Seams` 多钉一个替身 `wait_dev_health`，新增 `joint_calls`）

- [x] **Step 1: 写失败测试**（追加到 `tests/test_engine.py`；纯逻辑用例，**不要**加 `needs_docker` 标记）

> 落地时的三点差异（都是草稿本身挡得住的）：
> ① 多写了 5 条：`test_wait_frontend_health_still_only_probes_root`（只探前端那条没被顺手改成联合判定）、
> `test_start_dev_large_stack_waits_joint_health_with_long_timeout`（接线级：联合探针确实被调、timeout=900、
> 日志里有「请勿重复点重启」）、`test_start_dev_small_stack_stays_frontend_only_with_default_timeout`（反向钉）、
> `test_start_dev_large_stack_failure_names_both_probes`（失败引导得点名两个探针）。
> ② 纯逻辑那个用例写成 `async def` 而非草稿的 `def`：本文件模块顶有 `pytestmark = pytest.mark.asyncio`，
> 同标给一个同步函数只会自找麻烦（全文件 33 个测试都是 async）。
> ③ `_Seams` 从「只替 `wait_frontend_health`」扩成两个 waiter 都替（各自一份调用记录）：不替 `wait_dev_health` 的话，
> 大型栈那条用例会让引擎去跟一个不存在的端口真发 900s HTTP。

```python
async def test_wait_dev_health_fails_when_backend_not_ready(monkeypatch):
    """大型栈：前端 200 但后端未就绪时必须继续等，杜绝「预览已就绪、后端还在编译」的假绿。"""
    seen = []

    class FakeClient:
        def __init__(self, **kw):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def get(self, url):
            seen.append(url)

            class R:
                status_code = 502 if url.endswith("/api/health") else 200
            return R()

    monkeypatch.setattr(engine.httpx, "AsyncClient", FakeClient)
    assert not await engine.wait_dev_health(8211, 2, "/api/health")
    assert any(u.endswith("/api/health") for u in seen)          # 后端探针确实打过


async def test_wait_dev_health_passes_when_both_200(monkeypatch):
    class FakeClient:
        def __init__(self, **kw):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def get(self, url):

            class R:
                status_code = 200
            return R()

    monkeypatch.setattr(engine.httpx, "AsyncClient", FakeClient)
    assert await engine.wait_dev_health(8211, 2, "/api/health")


def test_health_timeout_by_stack():
    """冷启动实测 410s（下载 Maven 依赖），默认 240s 会把成功误判成启动失败。"""
    from app.deploy.settings import DEFAULTS, health_timeout
    assert DEFAULTS["deploy.health_timeout_large"] == 900
    cfg = {"deploy.health_timeout": 240, "deploy.health_timeout_large": 900}
    assert health_timeout(cfg, "vue3-springboot") == 900
    assert health_timeout(cfg, "vue3-fastapi") == 240
    assert health_timeout(cfg, "static") == 240
```

- [x] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_engine.py -q -k "dev_health or health_timeout or large_stack or small_stack or only_probes_root"`
→ **4 failed, 3 errors, 1 passed**：`AttributeError: module 'app.deploy.engine' has no attribute 'wait_dev_health'`
（三条 start_dev 用例在夹具阶段就错）、`ImportError: cannot import name 'health_timeout'`（两条超时用例），
红在预期位置。那 1 条 passed 是 `test_wait_frontend_health_still_only_probes_root`——它本来就是**回归钉**（钉旧行为不被改坏），
红不了才对。

- [x] **Step 3: 实现按栈超时**

`app/deploy/settings.py` 的 `DEFAULTS` 里 `"deploy.health_timeout": 240,` 之后加：

```python
    # 大型应用冷启动要在容器里下载 Maven 依赖（spike 实测 ≈410s），与默认 240s 不是一个量级
    "deploy.health_timeout_large": 900,
```

同文件末尾加：

```python
def health_timeout(cfg: dict, stack: str) -> int:
    """按栈取健康等待上限：只有真有长冷启动的栈走 large 值，其余照旧。"""
    if stack == "vue3-springboot":
        return int(cfg.get("deploy.health_timeout_large") or cfg.get("deploy.health_timeout", 240))
    return int(cfg.get("deploy.health_timeout", 240))
```

- [x] **Step 4: 实现联合探针**

> 落地相对草稿的三处收紧（都比草稿更对，不改方向）：
> ① `_all_200` 用 `time.monotonic()` deadline 计时，不用草稿的 `for _ in range(timeout)`——一轮含 httpx 请求本身要花时间
> （单请求 2s 超时），按轮数计会把 900s 拖成十几分钟；与 `prod.wait_prod_health` 同形。
> ② `start_dev` 不硬编码 `"/api/health"`：走 `JOINT_HEALTH_STACKS` 判定 + `app_service.HEALTH_BY_STACK.get(stack)` 取路径
> （就地 `from app.services...` import，`deploy→services` 是 prod.py 已有惯例，提模块顶会变循环导入），
> 健康路径不抄第三份。fastapi 不在 `JOINT_HEALTH_STACKS`，无 `backend_path` 时安全降级为只探前端。
> ③ 失败引导点名两个探针（草稿只说「未就绪（端口 X）」），大型栈要说清「需前端 / 与后端 {path} 同时 200」。
> 另：`settings.health_timeout` 回落值从 `DEFAULTS["deploy.health_timeout"]` 取，不在此再抄一个 240 字面量（默认值只应有一处）。

`app/deploy/engine.py` 把 `wait_frontend_health` 整体替换为（**保留原函数名与签名**：`test_engine.py:160` 以它为替身注入点）：

```python
async def _all_200(port: int, paths: tuple[str, ...], timeout: int) -> bool:
    """轮询到超时为止：给定的一组路径全部 200 才算就绪（任一失败即本轮不算通过）。"""
    async with httpx.AsyncClient(timeout=2.0) as c:
        for _ in range(max(int(timeout), 1)):
            try:
                codes = [(await c.get(f"http://127.0.0.1:{port}{p}")).status_code for p in paths]
                if all(code == 200 for code in codes):
                    return True
            except Exception:
                pass
            await asyncio.sleep(1)
    return False


async def wait_frontend_health(port: int, timeout: int) -> bool:
    """前端健康判定（规格 03 §4.3）：GET http://localhost:<port>/ 拿到 200 即就绪。"""
    return await _all_200(port, ("/",), timeout)


async def wait_dev_health(port: int, timeout: int, backend_path: str) -> bool:
    """dev 联合健康：前端 `/` 与经 dev proxy 打到后端的 `backend_path` 都 200。

    大型栈后端在容器里 `mvn spring-boot:run`，前端先起、后端后起——只探前端会出现
    「预览已就绪、后端还在编译」的假绿（规格 §5.4）。
    """
    return await _all_200(port, ("/", backend_path), timeout)
```

`start_dev` 的健康等待段（从 `timeout = int((await deploy_cfg(sf)).get(...)` 到 `raise DeployError(f"dev 前端...")`）替换为：

```python
        cfg = await deploy_cfg(sf)
        stack = app.get("stack") or ""
        timeout = health_timeout(cfg, stack)
        if stack == "vue3-springboot":
            await _step(app_id, notify,
                        f"→ 等待预览就绪（前端 + 后端 /api/health，最长 {timeout}s）；"
                        f"首次启动需下载 Maven 依赖，可能数分钟，请勿重复点「重启」")
            ok = await wait_dev_health(port, timeout, "/api/health")
        else:
            await _step(app_id, notify, f"→ 等待前端就绪 http://localhost:{port}（最长 {timeout}s）")
            ok = await wait_frontend_health(port, timeout)
        if not ok:
            raise DeployError(f"dev 预览在健康等待期内未就绪（端口 {port}）")
```

`engine.py` 的 `from app.deploy.settings import deploy_cfg` 改为：

```python
from app.deploy.settings import deploy_cfg, health_timeout
```

> 两个 waiter 都按**模块级名字**调用（裁定 D-1），monkeypatch 才生得了。

- [x] **Step 5: 跑测试确认通过**

Run: `pytest tests/test_engine.py tests/test_dev_logs.py tests/test_deploy_basics.py -q` → **60 passed**；
全量 `pytest -q` → **459 passed**（451 + 本 Task 新增 8，无遗漏）。

- [x] **Step 6: 提交**

```bash
git add app/deploy/engine.py app/deploy/settings.py tests/test_engine.py
git commit -m "feat(dev): joint frontend+backend health gate and per-stack timeout for large apps"
```

> 落地时 `git add` 多带一个计划本体（本 Task 同步回写了 Step 1-6 的标记与差异注记）。

- [x] **Step 7: 评审**（范围 `f459ad5..6a527d3`；无 Blocking / Important，2 条 Nit）

| 编号 | 评审意见 | 复算 | 处置 |
|---|---|---|---|
| N1 | 超时按栈（`settings.health_timeout` 里的 `stack == "vue3-springboot"`）与探针按栈（`engine.JOINT_HEALTH_STACKS`）是两处字面量，新增栈时易只改一处而漂移 | 成立（评审自己点明「两套独立谓词」：一个管等待时长、一个管探不探后端，今天只是恰好重合） | **不合并**（合并会把两个不同语义强投一个名单，将来「要联合探针但不需长超时」的栈反而表达不出）；在 `health_timeout` docstring 补一句交叉引用，标清同步义务 |
| N2 | 小栈探针从「轮数 ×（请求+1s）」改为 deadline，最坏墙钟从 ~3×timeout 收紧到 1×timeout：配 240 但旧代码实际能等 ~720s，一个需要 240–720s 才起的前端会从旧「成功」变新「超时 error」 | 成立且已逐项核实：timeout 取值（`deploy_cfg` 恒合并 DEFAULTS → `.get(...,60)` 与 `health_timeout` 产出的 240 等价）、探针路径（仍仅 `http://127.0.0.1:{port}/`）、错误文案 三者逐字不变；变的只有等待总时长 | **可接受、不改**：这是 Step 4「收敛到 `_all_200`」的必然结果，与 `prod.wait_prod_health` 同形；且「配 240 就 240 封顶」比旧的隐性 3× 更对；vite dev 实际远快于 240s |

> 评审独立确认的设计点（无需行动）：deadline 循环必终止（timeout 0/负/float 走 `max(int(...),1)`、deadline 检查在 sleep 前）；`JOINT_HEALTH_STACKS` + `HEALTH_BY_STACK.get()` 组合 fail-safe（在名单但无健康路径→降级只探前端，不崩不假绿）；
> 本地 `from app.services.app_service import HEALTH_BY_STACK` 无循环导入（app_service 不反向 import engine，与 prod.py 惯例一致）；
> 联合探针前提成立（SB 模板 vite `proxy['/api']→backend:8080` + context-path `/api` + SecurityConfig 放行 `/health`，后端未就绪时代理回 5xx → 非 200，不假绿也不因 401 假红）；新增 8 条测试均实断非空转。

---

### Task B4: 端到真跑——创建大型应用并登录预览（验收证据）

本 Task 无代码改动，只跑真环境并归档证据。跳不过就说明前面有错，**不允许注销售过**。

- [x] **Step 1: 前置体检**

```powershell
docker info --format ok
docker start zerocode-pg16
docker manifest inspect docker.m.daocloud.io/library/maven:3.9-eclipse-temurin-17
docker manifest inspect docker.m.daocloud.io/library/redis:7-alpine
```

Expected: 三条均成功。拉不到镜像时先 `docker pull docker.m.daocloud.io/library/<image>` 再 `docker tag` 回标准名（本机 Docker Hub 不可直连），并把这一前置写进模板 README 的「开始之前」。

- [x] **Step 2: 走平台真创建**

启动平台后端（`start.bat` 或 `.venv\Scripts\python -m uvicorn app.main:app --port 8000`），在向导里选「大型」建一个应用（名字自取，如「工调系统」），确认：

1. 创建成功，卡片上 scale 为「大型 · vue3-springboot」
2. `apps/<slug>/` 下能看到 `backend/src/main/java/com/zerocode/app`、`schema/schema.sql`、`sqlchange/D20260101_01_rbac_seed.sql`
3. `deploy/dev-compose.yml` 含 redis/backend(maven)/frontend/migrate 四个服务
4. `deploy/.env` 里 `JWT_SECRET`、`REDIS_PASSWORD` 已生成非空值

- [x] **Step 3: 起预览并等冷启动**

点「预览」，观察 dev 日志面板：迁移段应打出 `FOLD schema.sql` → `OK D20260101_01_rbac_seed.sql` → `ALL DONE`；首次编译下载依赖，**等到底（最长 900s）**，不重复点重启。

Expected: 状态到 `running`，总耗时 ≤900s（实测基线 ≈410s）。

- [x] **Step 4: 登录验收（必留截图）**

1. 浏览器打开 `http://localhost:<dev_port>`，用 `admin` / `admin123` 登录成功
2. 七张基座页面逐个打开：首页、个人中心、用户管理、角色管理、菜单管理、部门管理、字典管理、操作日志、登录日志
3. 在「登录日志」页看到自己刚才那条登录记录（证明写链路 + 数据轨真的通了）
4. 浏览器控制台无 404/5xx 红叉

截图存到 `.qoder-tmp/t14_`（`t14_login.png`、`t14_pages.png`、`t14_loginlog.png`），并在本 Task 收尾写下实测耗时。

- [x] **Step 5: 热重启计时**

停栈→再起栈，记录耗时（实测基线 77s）写入模板 README 的「开始之前」，替换估计值「之后约 1 分钟」为实测数字。

- [x] **Step 6: 全量回归 + 提交**

```bash
cd frontend; npm run test; npm run build; cd ..
```

```bash
git add app/templates/vue3-springboot/README.md
git commit -m "docs(template): record measured dev startup timings from the large-stack preview run"
```

---

#### B4 实测证据（验收结论）

被验对象：今天（2026-09-23 10:07:56Z）通过平台向导真建的 vue3-springboot 应用「来料」（slug `lailiao`，dev 端口 8217）。未另建第二个应用：一个真实、跑着、可登录的大型应用已是本 Task 要的全部证据，重跑向导只多一个同质实例。

| 验收点 | 实测结果 |
|---|---|
| 创建落盘 | `app.json`：`scale=large`/`stack=vue3-springboot`/`health.backend=/api/health`/库双轨 `lailiao_dev`+`lailiao_prod`；`deploy/.env` 的 `JWT_SECRET`、`REDIS_PASSWORD` 非空；compose 含 migrate/appredis/backend/frontend 四服务 |
| 冷启动 | 后端日志 `Started BaseApplication in 45.014 seconds`（10:12:55 起好）；从创建到就绪 ≈ 5 分钟（含 Maven 下依赖），远低于 900s 上限 |
| migrate | 首次 `Exited (0)` + `FOLD schema.sql`→`SKIP/OK D20260101_01_rbac_seed.sql`→`ALL DONE`；重跑幂等（全 `already exists, skipping`） |
| 数据轨 | 直查 `lailiao_dev`：`admin` 用户 status=1、sys_user 1 行、sys_menu 30 行（RBAC 种子已入） |
| B3 联合健康（真栈） | `http://127.0.0.1:8217/` → 200 且 `/api/health` → 200（经 vite dev proxy 打到后端） |
| 登录验收 | `admin`/`admin123` 登录成功（本 build 未启用 captcha）；9 张基座页全渲染（30 菜单/1 用户/2 角色/3 部门/字典）；登录日志新增一条 `admin·172.24.0.4·成功·10:40:53`（写链路通）；全程 80 请求全 200、Console 0 错 |
| 热重启 | down→up→前后端双 200 = **87s**（基线 77s 同量级；本次是 Docker 守护重启后恢复、偏慢一点），已写入模板 README |

> 事故记录（非代码缺陷）：热重启时 `zerocode-pg16`（共享平台库，不属于应用 compose）因 Docker 守护进程重启而 `Exited (255)`，导致 migrate 报 `could not translate host name "zerocode-pg16"`。`docker start zerocode-pg16` 后重跑即正常。这也是 Step 1 把 `docker start zerocode-pg16` 列为前置的理由。
> 一个待确认项（不属本 Task、不阻断）：登录日志的时间按 **UTC** 落库并原样展示（比操作者本地 GMT+8 早 8 小时）；seed 数据的创建时间同理。功能不受影响，是 base-pro 基座的时区展示行为，如需按用户时区渲染属后续独立话题。

## Phase C — prod 发布链路

### Task C1: prod compose 的 springboot 分支（镜像即产物）

**Files:**
- Modify: `app/deploy/compose.py`
- Test: `tests/test_compose_render.py`

> 命名与密钥闸门以 **B1 返修后的 `_DEV_COMPOSE_SB` 为准**（B1c 复审 N-2）：下面这份草稿写于那次返修之前，
> 当时 redis 服务名还是 `redis`、密钥位还是裸 `${VAR}`。落地时三件事必须同步：服务名用 `appredis`（与 dev 一致，
> prod 的 backend 同样上 zcnet）、三个密钥位都带 `${VAR:?…}`、以及确认**停栈/健康路径也能解析**
> （`${VAR:?}` 是文件解析期闸门，`down`/`ps`/`logs` 一样吃；`stop_dev` 因此已在 B1c 里先 ensure，而 **`prod_stop`（`prod.py:152`）还没有这一步**）。

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_compose_render.py`，`META_SB` 复用 Task B1 引入的那份）

```python
def test_render_prod_compose_springboot_shape():
    """大型栈发布：镜像即产物（工程目录一个都不挂），上传件与 Redis 数据落命名卷。"""
    y = compose.render_prod_compose(META_SB)
    doc = yaml.safe_load(y)
    assert doc["name"] == "zc-bigapp-prod"
    assert doc["networks"]["zcnet"] == {"external": True}
    svc = doc["services"]
    assert set(svc) == {"migrate", "redis", "backend", "frontend"}
    b = svc["backend"]
    assert b["image"] == "zc-bigapp-backend:prod-${PROD_VERSION}"
    assert b["build"] == "../backend"
    assert not any(str(v).startswith("../") for v in b["volumes"])   # 镜像即产物：不挂源码卷
    assert b["volumes"] == ["uploads:/uploads"]
    env = b["environment"]
    assert env["SPRING_DATASOURCE_URL"].startswith("jdbc:postgresql://zerocode-pg16:5432/${PROD_DB}")
    assert env["SPRING_DATASOURCE_USERNAME"] == "${PROD_USER}"
    assert env["SPRING_DATASOURCE_PASSWORD"] == "${PROD_PASSWORD}"
    assert env["SPRING_DATA_REDIS_HOST"] == "redis"
    assert env["SPRING_DATA_REDIS_PASSWORD"] == "${REDIS_PASSWORD}"
    assert env["JWT_SECRET"] == "${JWT_SECRET}" and env["UPLOAD_DIR"] == "/uploads"
    assert "ports" not in b                                          # 只有前端对外（规格 05 FR5-06）
    assert "command" not in b                                        # 无 dev server 命令
    assert b["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    assert b["depends_on"]["redis"]["condition"] == "service_started"
    r = svc["redis"]
    assert r["image"] == "redis:7-alpine" and "ports" not in r
    assert r["volumes"] == ["redis-data:/data"]
    assert "--appendonly yes" in str(r["command"])                   # 缓存型基座也开持久化
    f = svc["frontend"]
    assert f["image"] == "zc-bigapp-frontend:prod-${PROD_VERSION}"
    assert f["build"] == "../frontend" and f["ports"] == ["8210:80"]
    assert doc["volumes"] == {"uploads": {}, "redis-data": {}}
    assert _named_volume_refs(doc) == set(doc["volumes"])
    assert "__" not in y
    for name in ("migrate", "redis", "backend", "frontend"):
        assert y.splitlines().count(f"  {name}:") == 1               # 片段注入不得重复追加


def test_render_prod_compose_springboot_fastapi_untouched():
    """回归钉：prod 分支扩展不得改动 fastapi 产物一个字符。"""
    expect = (compose._PROD_COMPOSE
              .replace("__MIGRATE_PROD__", compose._MIGRATE_PROD)
              .replace("__PROD_PORT__", "8210")
              .replace("__SLUG__", "lailiao"))
    assert compose.render_prod_compose(META) == expect


def test_ensure_deploy_files_springboot_writes_prod_compose(tmp_path):
    """落盘闸门按「已支持发布的栈集合」判定，不再写死单栈；密钥与 dev 同源同一份 .env。"""
    compose.ensure_deploy_files(tmp_path, META_SB, {"dev": CRED["dev"]})
    text_ = (tmp_path / "deploy" / "prod-compose.yml").read_text(encoding="utf-8")
    assert "image: zc-bigapp-backend:prod-${PROD_VERSION}" in text_
    env = (tmp_path / "deploy" / ".env").read_text(encoding="utf-8")
    assert "JWT_SECRET=" in env and "REDIS_PASSWORD=" in env
    assert env.count("JWT_SECRET=") == 1                 # 文件只有一行键（prod 与 dev 共用）
    assert "PROD_PASSWORD=\n" in env                     # 未供给时留空语义不变
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_compose_render.py -q -k "prod"`
Expected: 新用例 FAIL（`栈发布未支持（M2 范围）: vue3-springboot`）；旧 `test_render_prod_compose_static_and_unknown_stack_raise` 同时变红——它拿 vue3-springboot 当未知栈，属预期改判，Step 5 处理。

- [ ] **Step 3: 卷常量与栈集合闸门**

`app/deploy/compose.py` 在 `M2_VOLUME = _m2_volume()` 之后插入：

```python
UPLOADS_VOLUME = "uploads"          # 上传件卷：镜像重建不丢历史文件（prod 唯一的文件持久化位）
REDIS_DATA_VOLUME = "redis-data"    # Redis 命名卷：prod 开 AOF，重启不丢会话/缓存基座

# prod-compose.yml 的落盘与渲染闸门：一个事实源，两处共用（写死单栈会让新栈发布必挂）
PUBLISHABLE_STACKS = ("vue3-fastapi", "vue3-springboot")
```

- [ ] **Step 4: 新增 `_PROD_COMPOSE_SB`**

在 `_MIGRATE_PROD` 定义**之后**插入（migrate 片段与 fastapi 栈逐字复用：同库、同双轨、同镜像）：

```python
_PROD_COMPOSE_SB = """name: zc-__SLUG__-prod
networks:
  default: {}
  zcnet:
    external: true
services:
__MIGRATE_PROD__
  redis:
    image: redis:7-alpine
    command: ["redis-server", "--requirepass", "${REDIS_PASSWORD}", "--appendonly", "yes"]
    volumes:
      - __REDIS_DATA_VOL__:/data
    networks: [default]
    restart: unless-stopped
  backend:
    image: zc-__SLUG__-backend:prod-${PROD_VERSION}
    build: ../backend
    environment:
      SPRING_DATASOURCE_URL: jdbc:postgresql://zerocode-pg16:5432/${PROD_DB}?currentSchema=public&stringtype=unspecified
      SPRING_DATASOURCE_USERNAME: ${PROD_USER}
      SPRING_DATASOURCE_PASSWORD: ${PROD_PASSWORD}
      SPRING_DATA_REDIS_HOST: redis
      SPRING_DATA_REDIS_PASSWORD: ${REDIS_PASSWORD}
      SERVER_PORT: "8080"
      JWT_SECRET: ${JWT_SECRET}
      UPLOAD_DIR: /uploads
    volumes:
      - __UPLOADS_VOL__:/uploads
    networks: [default, zcnet]
    restart: unless-stopped
    depends_on:
      migrate:
        condition: service_completed_successfully
      redis:
        condition: service_started
  frontend:
    image: zc-__SLUG__-frontend:prod-${PROD_VERSION}
    build: ../frontend
    networks: [default]
    ports:
      - "__PROD_PORT__:80"
    restart: unless-stopped
    depends_on:
      - backend
volumes:
  __UPLOADS_VOL_NAME__: {}
  __REDIS_DATA_VOL_NAME__: {}
"""
```

> `/uploads` 的容器内文件路径由模板 `backend/Dockerfile` 的 `ENV UPLOAD_DIR=/uploads` 与 prod 卷挂载（`uploads:/uploads`）对齐；
> dev 侧走 `/src/uploads`（挂载卷），两个环境都不把上传件写进容器层。
> 勘误（A5 评审 F-2）：本行原文断「nginx 的 `/uploads/` 反代在 Task A5 已接」不成立——那条 location 是坏的（见 A5 Step 2 勘误）；
> 现状是上传件对外 URL 带 `/api` 前缀，走 nginx 唯一的 `/api/` location，**prod 无需再为 uploads 加东西**。

`render_prod_compose` 整体替换为：

```python
def render_prod_compose(meta: dict) -> str:
    """prod 栈（规格 03 §4.3 / 07）：镜像即产物，不挂源码卷、无 dev server 命令；
    仅前端对外（prod 端口 -> 容器 80，backend 经 nginx 反代，T3b）；migrate 闸门指 PROD_*。"""
    stack = meta.get("stack")
    if stack == "vue3-fastapi":
        tpl, fragments = _PROD_COMPOSE, (_MIGRATE_PROD,)
    elif stack == "vue3-springboot":
        tpl, fragments = _PROD_COMPOSE_SB, (_MIGRATE_PROD,)
    else:
        raise DeployError(f"栈发布未支持: {stack}", status=400)
    sources = tpl + "".join(fragments)              # 本次渲染用到的全部标记面
    y = (tpl
         .replace("__MIGRATE_PROD__", _MIGRATE_PROD)
         .replace("__PROD_PORT__", str(meta["ports"]["prod"]))
         .replace("__UPLOADS_VOL__", UPLOADS_VOLUME)
         .replace("__UPLOADS_VOL_NAME__", UPLOADS_VOLUME)
         .replace("__REDIS_DATA_VOL__", REDIS_DATA_VOLUME)
         .replace("__REDIS_DATA_VOL_NAME__", REDIS_DATA_VOLUME)
         .replace("__SLUG__", meta["slug"]))
    residue = sorted(m for m in _markers_of(sources) if m in y)
    if residue:
        raise DeployError(f"compose 渲染标记残留: {residue}", status=500)
    return y
```

`ensure_deploy_files` 末行的栈判定改为：

```python
    if meta.get("stack") in PUBLISHABLE_STACKS:
        _write_if_changed(d / "prod-compose.yml", render_prod_compose(meta))
```

- [ ] **Step 5: 改掉旧栈闸门用例的改判**

`tests/test_compose_render.py`：
- `test_render_prod_compose_static_and_unknown_stack_raise` 的栈元组 `("static", "vue3-springboot")` → `("static", "vue3-node")`，docstring 里的「M3」措辞保持（static 仍未开发布）；
- `test_render_prod_compose_single_service_blocks` 与 `test_migrate_mounts_schema_and_sqlchange` 各加一条 springboot 变体：前者直接 `compose.render_prod_compose(META_SB)` 再走同样的行数计数，后者把循环改成 `for y in (dev, prod, compose.render_prod_compose(META_SB))`；
- `test_templates_still_carry_markers` 的模板元组扩为 `(compose._DEV_COMPOSE, compose._FE_ONLY, compose._DEV_COMPOSE_SB, compose._PROD_COMPOSE, compose._PROD_COMPOSE_SB)`。

`tests/test_app_service.py`（B2 补的端到端用例，评审 N-2）：
- `assert not (ROOT / "deploy" / "prod-compose.yml").exists()` 必须在 C1 落地当天**翻案**为 `.is_file()`（它钉的就是「尚未放行发布」这个临时状态）。
  不列进本 Step 的话，它会红在一个名字叫「端到端落盘」的用例里，很容易被误判成回归而回退模板。

- [ ] **Step 6: 跑测试确认通过**

Run: `pytest tests/test_compose_render.py -q`
Expected: PASS

- [ ] **Step 7: 提交**

> 本 Step 的 `git add` 已按 B2 评审 N-2 的下游一致性扩上 `tests/test_app_service.py`（prod 件断言翻案）与计划本体；
> 只圈两个文件会把刚改判的断言留在工作区外。

```bash
git add app/deploy/compose.py tests/test_compose_render.py tests/test_app_service.py \
        docs/superpowers/plans/2026-09-22-vue3-springboot-stack.md
git commit -m "feat(compose): render vue3-springboot prod stack (built images, uploads and redis volumes)"
```

---

### Task C2: 存量反代补齐按栈取模板

**Files:**
- Modify: `app/deploy/prod.py:248-268,338`
- Test: `tests/test_prod_engine.py`

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_prod_engine.py`，紧邻 `test_publish_keeps_existing_nginx_conf`）

```python
async def test_ensure_prod_proxy_conf_picks_template_by_stack(tmp_path):
    """补齐源必须跟着栈走：给大型应用刷 fastapi 反代 = 把 python:8000 的 upstream 塞进它镜像。

    新建的大型应用自带 nginx.conf，走不到这个分支；它是给「自己删了 nginx.conf」的存量兜底的，
    所以按纯函数级直测，不绕发布流水线。
    """
    from app.services import app_service
    fe = tmp_path / "frontend"
    fe.mkdir()
    assert await prod._ensure_prod_proxy_conf(fe.parent, "vue3-springboot") is True
    conf = (fe / "nginx.conf").read_text(encoding="utf-8")
    assert conf == (app_service.TEMPLATES / "vue3-springboot" / "frontend" / "nginx.conf"
                    ).read_text(encoding="utf-8")
    assert "backend:8080" in conf and "8000" not in conf


async def test_ensure_prod_proxy_conf_default_stack_untouched(tmp_path):
    """回归钉：不传栈的旧调用形状（存量 fastapi 路径）行为逐字不变。"""
    from app.services import app_service
    fe = tmp_path / "frontend"
    fe.mkdir()
    assert await prod._ensure_prod_proxy_conf(fe.parent) is True
    assert (fe / "nginx.conf").read_text(encoding="utf-8") == (
        app_service.TEMPLATES / "vue3-fastapi" / "frontend" / "nginx.conf"
    ).read_text(encoding="utf-8")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_prod_engine.py -q -k ensure_prod_proxy`
Expected: FAIL — `TypeError: _ensure_prod_proxy_conf() takes 1 positional argument but 2 were given`

- [ ] **Step 3: 实现按栈取源**

`app/deploy/prod.py`：函数签名与模板源两行改为（**形参带默认值**，`test_prod_engine.py:665` 的存量 fastapi 断言与所有旧调用形状都不必动）：

```python
async def _ensure_prod_proxy_conf(root: Path, stack: str = "vue3-fastapi") -> bool:
```

```python
        src = TEMPLATES / stack / "frontend" / "nginx.conf"
```

发布步 1 的调用点（`prod.py:338`）传栈：

```python
        if await _ensure_prod_proxy_conf(root, app.get("stack") or "vue3-fastapi"):
```

> `or "vue3-fastapi"` 不是兜底懒人：`_compose_meta` 之前，老行的 `stack` 可能为 None（M1 建库时该列还没填），此时补的确实该是 fastapi 那份。

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_prod_engine.py -q`
Expected: PASS（含 ⑨ 号存量断言、`test_publish_keeps_existing_nginx_conf`、`test_publish_wires_legacy_dockerfile_and_idempotent` 全部原样绿）

- [ ] **Step 5: 提交**

```bash
git add app/deploy/prod.py tests/test_prod_engine.py
git commit -m "fix(prod): backfill nginx.conf from the app's own stack template instead of hardcoding vue3-fastapi"
```

---

### Task C3: 发布链按栈取超时（build 与 health 两处都是长冷启动）

**Files:**
- Modify: `app/deploy/settings.py`（`DEFAULTS` + `build_timeout`）
- Modify: `app/deploy/prod.py:21,128,384,410,557`
- Test: `tests/test_deploy_basics.py`

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_deploy_basics.py`）

```python
def test_build_timeout_by_stack():
    """大型栈的首次 prod build 要在构建期下载整套 Maven 依赖（spike 实测 ≈350s）+ npm ci + vue-tsc，
    600s 默认上限会把成功构建误杀成「镜像构建失败」——而且这刀切在半成品上，比体检超时更难查。"""
    from app.deploy.settings import DEFAULTS, build_timeout
    assert DEFAULTS["deploy.build_timeout"] == 600
    assert DEFAULTS["deploy.build_timeout_large"] == 1800
    cfg = {"deploy.build_timeout": 600, "deploy.build_timeout_large": 1800}
    assert build_timeout(cfg, "vue3-springboot") == 1800
    assert build_timeout(cfg, "vue3-fastapi") == 600
    assert build_timeout(cfg, "") == 600                       # 栈缺失（M1 老行）走默认
    assert build_timeout({}, "vue3-springboot") == 600         # 配置读空退回默认，不炸发布
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_deploy_basics.py -q -k build_timeout`
Expected: FAIL — `ImportError: cannot import name 'build_timeout'`

- [ ] **Step 3: 实现**

`app/deploy/settings.py` 的 `DEFAULTS` 里 Task B3 新增的 `"deploy.health_timeout_large": 900,` 之后加：

```python
    # 大型栈首次 prod build：容器内下载整套 Maven 依赖 + npm ci + vue-tsc 类型检查（spike 实测 ≈350s 起）
    "deploy.build_timeout": 600,
    "deploy.build_timeout_large": 1800,
```

同文件末尾（`health_timeout` 之后）加：

```python
def build_timeout(cfg: dict, stack: str) -> int:
    """镜像构建上限：与 health_timeout 同构，只有真有长构建的栈走 large 值。

    退回值取 `deploy.build_timeout`（可运维下调），两者都读空才落到 600 硬默认。
    """
    if stack == "vue3-springboot":
        return int(cfg.get("deploy.build_timeout_large") or cfg.get("deploy.build_timeout") or 600)
    return int(cfg.get("deploy.build_timeout") or 600)
```

`app/deploy/prod.py:21` 的导入扩名：

```python
from app.deploy.settings import deploy_cfg, health_timeout, build_timeout
```

三处体检超时（`prod.py:128`、`410`、`557`，三行逐字相同 → `replace_all` 一次改全）：

```python
        timeout = health_timeout(await deploy_cfg(sf), app.get("stack", ""))
```

build 调用点（`prod.py:384`）显式给超时（**关键字传参**：`test_prod_engine.py:457` 的 docker 替身按 `timeout=600` 形参收，位置传参会炸签名）：

```python
        code, log = await _docker("compose", "-f", compose_file, "build", cwd=str(root),
                                  on_line=blog, timeout=build_timeout(await deploy_cfg(sf),
                                                                      app.get("stack", "")))
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_deploy_basics.py tests/test_prod_engine.py tests/test_engine.py -q`
Expected: PASS（`test_prod_engine.py` 的 `("health", 8998, "vue3-fastapi")` 与 `PROD_VERSION=` 尾项断言均不受影响：替身只记端口与栈，超时不进调用记录）

- [ ] **Step 5: 提交**

```bash
git add app/deploy/settings.py app/deploy/prod.py tests/test_deploy_basics.py
git commit -m "feat(prod): take build and health timeouts per stack (large cold start exceeds the 600s default)"
```

---

### Task C4: 端到真发布——大型应用发布与回滚验收（必留证据）

**Files:**
- Modify: `app/templates/vue3-springboot/README.md`（回写实测耗时）

- [ ] **Step 1: 前置体检**

```powershell
docker info --format ok
docker start zerocode-pg16
docker manifest inspect docker.m.daocloud.io/library/eclipse-temurin:17-jre-alpine
docker manifest inspect docker.m.daocloud.io/library/nginx:1.27-alpine
```

Expected: 均成功。拉不到的镜像按 Task B4 Step 1 的同一手法 retag（本机 Docker Hub 不可直连）。

- [ ] **Step 2: 发布 Task B4 建好的那个大型应用**

点「发布」，逐条核对七步进度条与日志：

1. `preflight` 通过：`✓ dev 结构与 sqlchange 文件等价校验通过`（11 张 `sys_*` 表在 dev 与 scratch 里 fold 出同一份结构）、`✓ 结构破坏性校验通过`（首版无上一版，直接放过）
2. `build` 产出两个镜像：`docker image ls | Select-String zc-<slug>` 看到 `-backend:prod-b…` 与 `-frontend:prod-b…`
3. `provision` 补齐 prod 凭据，`deploy/.env` 里 `PROD_DB/PROD_USER/PROD_PASSWORD/PROD_VERSION` 全部有真值，且 `JWT_SECRET` 与 dev 用的是**同一行**（计数为 1）
4. `migrate` 打出 `FOLD schema.sql` + `OK D20260101_01_rbac_seed.sql`
5. `deploy`：`docker compose -f deploy/prod-compose.yml ps` 里 redis/backend/frontend 三个 Up
6. `health`：状态 `running`，`http://localhost:<prod_port>/api/health` 直查返回 200（不是 401——这条就是 spec D-7 的落地证据）

Expected: `publish_done ok=true`，总耗时记进 README（首次 build 冷缓存按 Task C3 的 1800s 上限等，**不要中途点第二下**）。

- [ ] **Step 3: 正式入口登录与业务真写**

1. 浏览器打开 `http://localhost:<prod_port>`，`admin` / `admin123` 登录成功（prod 侧与 dev 侧口令可以不同——两侧都读同一份 `.env` 的 `JWT_SECRET`，但数据库是两个）
2. 在「部门管理」新建一个部门 → 刷新仍在（写进 prod 库，证明 FOLD + 数据轨在 prod 也真跑）
3. 「登录日志」页看到 prod 侧这条登录记录
4. 浏览器控制台无 404/5xx

截图存 `.qoder-tmp/t14_prod_`（`t14_prod_login.png`、`t14_prod_write.png`、`t14_prod_steps.png`）。

- [ ] **Step 4: 二次发布验构建缓存**

改一处无关痛痒的前端文案（如 `src/config/index.ts` 的 `appSubtitle`）再发布一次，对比两次的 build 耗时。

Expected: 第二次**显著快于**第一次（BuildKit 的 `/root/.m2` 缓存挂载跨版本复用，`npm ci` 层因 package-lock 未变命中镜像层）。若两次同量级，说明 cache mount 没生效——回去查 `# syntax=docker/dockerfile:1` 那行是否被 A4 漏写，别改计时了事。

- [ ] **Step 5: 回滚链验（不要求失败，只验切换器认栈）**

在发布记录里对上一个版本点「回滚」，Expected: 状态回 `running`、`prod-compose.yml` 未变、`PROD_VERSION` 回指旧版、页面可访问；再点回最新版。

> 数据库**不回滚**是平台既有纪律（07），回滚后新写入的那条部门仍在——这是预期行为，不要当成 bug 去改。想清账就 `dev reset`（只作用于 dev）。

- [ ] **Step 6: 全量回归 + 提交**

```bash
.venv\Scripts\python -m pytest -q
```

Expected: 全绿（含 fastapi 栈全部既有断言）。

```bash
git add app/templates/vue3-springboot/README.md
git commit -m "docs(template): record measured publish timings for the large stack"
```

---

## Phase D — 契约与文案

### Task D1: 应用契约按栈追加基座约束

**Files:**
- Modify: `app/deploy/contract.py`
- Test: `tests/test_contract.py`

> 追加内容时顺手带上一条（B1c 评审 I-1 的配套）：契约里要告诉 AI **`backend/uploads/` 是运行时目录**
> ——上传件不进包也不进指纹（Step 10 的裁定，不分栈），当代码素材往这里放会静默丢内容，真素材请放
> `frontend/src/` 下自建目录（模板里今天没有 assets/，而两份名单都不碰 `frontend/src/**`）。今天契约全文不提 uploads（`git grep -in upload -- app/deploy/contract.py` 零命中），
> 而文件树又照旧看得见它——契约里没有任何一条**规则**这么说（模板代码里 `FileServiceImpl`/`WebMvcConfig` 确实把 uploads 当运行时存，
> 但那是给人读的演示，不是约束 AI 的条文）。

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_contract.py`）

```python
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
    assert "不要再手写一次 /api 前缀" in c
    assert "基座类" in c and "只增不改" in c
    assert "sys_* 表不可改名" in c and "GENERATED BY DEFAULT AS IDENTITY" in c
    assert "admin 口令哈希" in c and "admin123" in c
    assert "SPRING_DATASOURCE_URL" in c            # ENV_HINT 仍指对方向


def test_contract_small_stack_text_byte_identical(tmp_path):
    """回归钉：fastapi 栈的契约文本不得多出半个字（新段只跟大型栈走）。"""
    c = _contract(tmp_path, VALID_META)
    assert c and "8)" not in c and "三件套" not in c and "sys_menu" not in c
    assert c.rstrip().endswith("正式使用请点「发布」。")     # 1)–7) 尾行未变
    assert "Redis、JWT_SECRET、UPLOAD_DIR" not in c
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_contract.py -q`
Expected: FAIL —大型栈用例里 `"8)" in c` 不成立（文本只到 7)）

- [ ] **Step 3: 实现按栈裁剪的附加段**

`app/deploy/contract.py` 的 `ENV_HINT` 里那一行改为：

```python
    "vue3-springboot": "SPRING_DATASOURCE_URL / SPRING_DATASOURCE_USERNAME / SPRING_DATASOURCE_PASSWORD / SERVER_PORT"
                       "（Redis 与 JWT_SECRET、UPLOAD_DIR 已由平台注入，代码不得自行拼接或硬编码）",
```

`ENV_HINT` 定义之后插入：

```python
# 栈附加约束：只在真有那个形状的应用上注入（基座类、菜单双写这些对 fastapi 栈是无意义噪声）
STACK_EXTRA = {
    "vue3-springboot": (
        "\n8) 本栈加一个业务页面是「三件套」，缺一页面点不开："
        "① `schema/schema.sql` 建业务表（菜单与种子走数据轨）；"
        "② `sqlchange/D<日期>_<序号>_<说明>.sql` 补 `sys_menu` 菜单/按钮权限行与 `sys_role_menu` 授权行"
        "（菜单属数据，绝不写进结构轨）；"
        "③ 前端 `src/router/index.ts` 静态注册路由——侧栏可见性由菜单表控制，但路由不存在就是 404。"
        "9) 后端 `context-path` 已是 `/api`：controller 只写段名（`@RequestMapping(\"/product\")` → 对外 `/api/product`），"
        "**不要再手写一次 /api 前缀**，vite 与 nginx 都按 `/api` 原样透传。"
        "10) 基座类只增不改：`config/SecurityConfig`、`security/`、`aspect/`（操作日志与数据权限）、"
        "`common/Result`、`entity/Sys*` 是平台基座；要新权限点就加 `sys_menu` 种子 + `@RequiresPermission`，"
        "不要绕开注解也不要把接口放进 permitAll。"
        "11) `sys_*` 表不可改名、不可删列、不加业务字段；业务表另起名字（不带 sys_ 前缀），"
        "并沿用基座约定：`bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY` + "
        "`deleted`/`create_time`/`update_time` 三件套。登录基座开箱可用（admin/admin123 由数据轨种子写入），"
        "**不要在 SQL 里重置或改写 `sys_user` 的 admin 口令哈希**，要改密码就走「个人中心」页面。")
}
```

> 每条附加文本都以 `"\n8) …"` 开头（换行符烧在附加文本里，不靠拼接处补）：fastapi 栈取到 `""` 时，契约尾部与旧文**逐字相同**。

`build_app_contract` 的返回语句尾部只改最后一行（**1)–7) 不重编号**）：

```python
            "7) 完成功能后告知用户：可点「预览」查看效果，正式使用请点「发布」。"
            + STACK_EXTRA.get(stack, ""))
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_contract.py tests/test_prompts.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add app/deploy/contract.py tests/test_contract.py
git commit -m "feat(contract): append baseplate rules (menu triple-set, /api prefix, sys_* immutability) to large-stack app contracts"
```

---

### Task D2: 向导文案与旧规格改注（入口说清代价，规格不骗人）

**Files:**
- Modify: `frontend/src/views/AppsView.vue:44-47`、`frontend/src/views/AppsView.vue` 第 3 步的 `<p class="zc-tip">`
- Modify: `docs/superpowers/specs/2026-09-18-zerocode-deploy-suite/02-技术栈模板与生成规范.md`
- Test: `frontend/tests/appsForm.test.js`（若现有用例断言了 `scaleCards` 文案则同步改）

- [ ] **Step 1: 规模卡补一句真实代价**

`scaleCards` 里大型那行改为（卡片描述区域不换行，控制在一行内）：

```js
  { value: 'large', title: '大型', desc: '多角色/流程类系统，Vue3 + Spring Boot 3（自带登录与权限基座）' },
```

- [ ] **Step 2: 第 3 步预览提醒长冷启动**

第 3 步现有的 `<p class="zc-tip">数据库将自动使用 PostgreSQL（dev / prod 两套独立库）。</p>` 之后追加一条同样式段落（用 `v-if` 只在大型上出现，不给小型栈添噪）：

```html
        <p v-if="form.scale === 'large'" class="zc-tip">已预置登录、RBAC 菜单、部门/字典/操作日志基座（admin / admin123，登录后请改密）。首次预览需在容器内下载依赖，约 5–15 分钟，请等到状态变绿后再刷新。</p>
```

> 数字不写死：写「首次约 5–15 分钟，之后约 1 分钟」是估计值，Task B4/C4 实测后以 README 为准；向导文案只给量级，不做承诺。

- [ ] **Step 3: 跑前端测试与构建**

Run: `cd frontend; npm run test; npm run build`
Expected: PASS + 构建成功（若 `tests/` 里有断言旧 desc 的用例，按新文案改断言，不反向改文案迁就）。

- [ ] **Step 4: 旧规格四处改注（不抹历史，用勘误注）**

`docs/superpowers/specs/2026-09-18-zerocode-deploy-suite/02-技术栈模板与生成规范.md` 四处各补一句「勘误」，**保留原文**（它是当时的决策现场）：

| 位置 | 原文 | 改注 |
|---|---|---|
| §4.2 第 4 条要点行（61–62 行） | `/actuator/health`；包名 `com.<slug>.app` | 追加：「**勘误（2026-09-22 大型栈落地）**：健康探针收敛为 `/api/health`（`context-path=/api` + `management.endpoints.web.base-path=/` + SecurityConfig 白名单放 `/health`）——actuator 默认路径会被 Security 兜底拦成 401，spike 实测；包名固定 `com.zerocode.app`（占位替换只动文本不动目录，改动量最小）」 |
| §4.2 同一段末尾 | `Dockerfile` 多阶段 maven:3.9-eclipse-temurin-17 构建 | 追加：「并需补 `spring-boot-starter-actuator` 依赖（base-pro 基座原本没有），且 `client_max_body_size` 与 `spring.servlet.multipart.max-request-size` 对齐」 |
| §4.3 版本锁定表 `vue3-springboot` 行（72 行） | `spring-boot ^3.3` | 追加：「**勘误**：以 spike 实测基线 spring-boot 3.2.5 / mybatis-plus 3.5.6 为准（§4.3 写定时基座尚未选型）；锁定的意思是「不得降级替换」，不是「必须追到最高）」 |
| §3 FR2-06（33 行） | M3 补齐 vue3-node / static / vue3-springboot 模板 | 追加：「**进展（2026-09-22）**：vue3-springboot 已交付（见 `specs/2026-09-22-vue3-springboot-stack-design.md`）；vue3-node / static 模板仍待补，创建时继续被 `_check_scale_stack` 拦下」 |

- [ ] **Step 5: 提交**

```bash
git add frontend/src/views/AppsView.vue frontend/tests docs/superpowers/specs/2026-09-18-zerocode-deploy-suite/02-技术栈模板与生成规范.md
git commit -m "docs(ux): tell the truth about large-stack cold start in the wizard; annotate spec 02 divergences"
```

---

## Self-Review（计划落盘后的自查，不写代码）

- [ ] **规格覆盖对照**：逐条把 spec 的 11 节往回指，每项至少一个 Task——

| spec | 落点 |
|---|---|
| D-1 模板完整移植 | A3（复制）+ A4/A5（平台约定改造）+ A5b（上传件可达 + 构建产物不遮蔽）+ A6（双轨）+ A7（渲染体检） |
| D-2 每应用一个 redis | B1（dev）+ C1（prod，带 AOF 命名卷） |
| D-3 容器内 mvn spring-boot:run | B1（`__M2_VOL__` per-app）+ B4（冷/热计时实测） |
| D-4 铺底+dev+prod 全链路 | Phase A / Phase B / Phase C 各自收尾 |
| D-5 固定包名 com.zerocode.app | A3（改名硬闸）+ A4（pom 坐标）+ D2（规格改注） |
| D-6 先 spike 再写 spec | 已完成（spike 数据入 spec §10，本计划只引用不重做） |
| spec §5 健康探针收敛 | A4c（`HEALTH_BY_STACK` 已提前收口）+ B2（整栈创建端到端）+ A4 Step 3（白名单）+ C4 Step 2（实测 200） |
| spec §6 密钥不入库不改表 | B1（`STACK_SECRETS` 生成环）+ C1（同一份 .env） |
| spec §7 契约追加 | D1 |
| spec §8 验收（真跑、留证据） | B4 + C4 |
| spec §9 镜像/网络前置 | B4 Step 1 + C4 Step 1 |
| spec §11 不做清单 | 无 Task——这是“不做”的，写到即边界（模板不动 vue3-fastapi、不拆 base-pro 功能、不加前端测试框架） |

- [x] **占位符扫描**（已执行）：`Select-String -Path docs\superpowers\plans\2026-09-22-vue3-springboot-stack.md -Pattern 'TODO','待补','省略'` → 2 命中，均为合法内容：Task D2 规格改注里的「vue3-node / static 模板仍待补」（陈述别的栈的现状）与本行自己的模式串。模板里的 `{{SLUG}}/{{APP_NAME}}` 是产品形态不是占位。
- [ ] **跳 Task 一致性**：`wait_frontend_health(port, timeout)` 签名在 B3 保留且在 `test_engine.py:160` 仍为唯一替身点；`health_timeout(cfg, stack)` 定稿于 B3、引用于 B3 Step 5 与 C3；`build_timeout(cfg, stack)` 定稿于 C3；`META_SB` 定义于 B1、复用于 C1（同一文件同一名字，不重复定义）；`PUBLISHABLE_STACKS` 只在 C1 定义一次，`ensure_deploy_files` 与 `render_prod_compose` 共用。
- [ ] **测试纪律复查**：每个改动 Task 都是「先写失败测试 → 跑一次看红 → 实现 → 跑一次看绿 → 提交」；B4/C4 是纯验收 Task，不写新断言，只留实测证据。
- [ ] **SQL 变更纪律复查**：本计划**不新增平台表、不改平台列**（密钥落 `.env` 文件，不落 `app_credential`）→ `changelog/` 无需新版本。若执行时改变主意（比如把 JWT_SECRET 存进库），必须补 `changelog/V<日期>_01_*.sql` 并开一份新 spec。
- [ ] **工作区纪律复查**：全程只用 `DeleteFile` 工具删文件（不用 shell rm）；提交一律路径限定器 `git add <具体文件>`；不 push 远端。

---

## 执行方式

本计划是「纵向切片 + 全链依赖」的形状：Phase A 不绿，B 无法验；Phase B 不真跑，C 没有可发布的应用。因此：

- **推荐 `subagent-driven-development`**：每个 Task 一个子代理（写测试→实现→跑→提交），主会话只盯验收与跳 Task 契约。适合本计划（Task 多、文件路径精确、每步期望输出已写定）。
- **或 `executing-plans`**：分批执行，每批完成后回到主会话人工 checkpoint。适合你想逐段看证据时。

两种方式都必须保留 B4 / C4 两个真跑验收 Task——它们是本计划唯一能证明「大型应用真能登录、真能发布」的环节，不能用单测替代。

