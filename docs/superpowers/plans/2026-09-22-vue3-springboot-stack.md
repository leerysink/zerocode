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
    location /api/ {
        proxy_pass http://backend:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        # 与 spring.servlet.multipart.max-request-size 对齐，否则大文件先在网关层 413
        client_max_body_size 50m;
    }

    # 上传件回读（prod 落 named volume，后端挂载后经此路径暴露）
    location /uploads/ {
        proxy_pass http://backend:8080;
        proxy_set_header Host $host;
    }

    # SPA 路由回落（history 模式刷新不 404）
    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

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
```

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
> **记账（故意不改）**：`style/theme.css:2` 的首行注释与 `backend/pom.xml` 的 `<description>` 仍含
> 「基础管理后台」字样——都非用户可见、都在应用工程内（AI 可改），改它们超出本 Task 的 Files 边界；
> 用户可见位已全部收敛到 `{{APP_NAME}}`。

- [x] **Step 6: `package.json` 的 name 吃 slug**

`"name"` 现值 → `"name": "{{SLUG}}-frontend"`（与 fastapi 栈同形）。实跑连带改了 `package-lock.json` 的
两处 root `name`（Step 3 的 `npm ci` 同步要求）。

- [x] **Step 7: 提交** — `feat(template): wire vue3-springboot frontend to compose service names and prod nginx reverse proxy`

**验收实跑**（证据，不纸面）：
- 新增 `tests/test_scaffold.py::test_render_springboot_frontend_wiring`（先红：vite 仍 `8082` → 实现后绿）；
  `pytest tests/test_scaffold.py -q` → **18 passed**。
- **全模板**违规字样体检归零（口径比 A4 Step 8 更严：`D:/project|8082|PG_PASSWORD|application-dev|application-local|leerysink|base-pro|snfyxm`）——A4 当时留在
  `vite.config.ts:29-30` 的两行已随本 Task 消失，**backend 与 frontend 都是零命中**；
- 占位符清单（大小写敏感）：前端只剩 `{{APP_NAME}}`×2（index.html / config）与 `{{SLUG}}`×3（package.json + lock 两处），
  全在注册键里（整栈渲染绿即证明 `PLACEHOLDER` 不报 `KeyError`、`RESIDUE` 无残留）。

```bash
git add app/templates/vue3-springboot/frontend
git commit -m "feat(template): wire vue3-springboot frontend to compose service names and prod nginx reverse proxy"
```

---

### Task A6: 数据库双轨拆分（结构轨 + RBAC 种子轨）

**Files:**
- Create: `app/templates/vue3-springboot/schema/{schema.sql,README.md}`
- Create: `app/templates/vue3-springboot/sqlchange/{D20260101_01_rbac_seed.sql,README.md}`

来源 `$ref\database\init-postgres.sql`（298 行、12 表 + 索引 + COMMENT + 种子 + 6 条 `setval`）。基线**已是幂等形态**（`CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS` / `ON CONFLICT DO NOTHING`），所以本 Task 是「按边界切两刀」而不是重写 DDL。

- [ ] **Step 1: 按文本标记切两段（不手数行号）**

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

- [ ] **Step 2: 换掉结构轨文件头**（原头注释讲的是 base-pro 自己的台账机制）

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

- [ ] **Step 3: 给数据轨种子补文件头**（保留原有中文注释）

```sql
-- D20260101_01_rbac_seed.sql —— 基座 RBAC 种子（幂等可重放；一经应用不得修改，只能追加新 D 文件）
-- 固定日期 20260101：文件名不经占位替换（渲染器只改内容不改路径），保证永远排在本应用新增文件之前。
```

- [ ] **Step 4: 平台账本唯一性体检（规格 §4 的收口）**

```powershell
Get-ChildItem app\templates\vue3-springboot\schema\schema.sql, app\templates\vue3-springboot\sqlchange\*.sql | Select-String -Pattern 'schema_history' | ForEach-Object { $_.Filename + ":" + $_.LineNumber }
Select-String -Path app\templates\vue3-springboot\schema\schema.sql -Pattern 'INSERT|DROP'
```

Expected: 两条都**无输出**——base-pro 自带的第 12 张 `schema_history` 表与其台账 INSERT 已剔除（与平台 `sqlchange_history` 双记账不可共存）；结构轨无数据、无 DROP。

- [ ] **Step 5: 幂等性实测（纸面通过不算通过）**

本机 `zerocode-pg16` 需为运行态（不在则 `docker start zerocode-pg16`）：

```powershell
docker exec zerocode-pg16 psql -U postgres -c "CREATE DATABASE zc_tpl_probe"
Get-Content app\templates\vue3-springboot\schema\schema.sql -Raw | docker exec -i zerocode-pg16 psql -q -v ON_ERROR_STOP=1 -U postgres -d zc_tpl_probe -f -
Get-Content app\templates\vue3-springboot\sqlchange\D20260101_01_rbac_seed.sql -Raw | docker exec -i zerocode-pg16 psql -q -v ON_ERROR_STOP=1 -U postgres -d zc_tpl_probe -f -
Get-Content app\templates\vue3-springboot\schema\schema.sql -Raw | docker exec -i zerocode-pg16 psql -q -v ON_ERROR_STOP=1 -U postgres -d zc_tpl_probe -f -
docker exec zerocode-pg16 psql -U postgres -d zc_tpl_probe -tAc "SELECT count(*) FROM sys_menu"
```

Expected: 三次 `-f` 退出码 0、无 ERROR；最后一条输出 `30`（10 目录/菜单 + 20 按钮）。
探针库 `zc_tpl_probe` 是一次性验证物，用完由人工 `DROP DATABASE zc_tpl_probe`（它是探针，不是平台产物）。

- [ ] **Step 6: 两份 README 沿用 fastapi 口径 + 补本栈特有的菜单条款**

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

- [ ] **Step 7: 提交**

```bash
git add app/templates/vue3-springboot/schema app/templates/vue3-springboot/sqlchange
git commit -m "feat(template): split base-pro baseline into schema track and idempotent RBAC seed track"
```

---

### Task A7: 模板 README + 整栈渲染回归测试

**Files:**
- Create: `app/templates/vue3-springboot/README.md`
- Test: `tests/test_springboot_template.py`（新建）

- [ ] **Step 1: 写失败测试**（新建 `tests/test_springboot_template.py`）

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

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_springboot_template.py -q`
Expected: `test_render_replaces_coordicates_and_title` FAIL（缺 `README.md` → FileNotFoundError），其余已绿。

- [ ] **Step 3: 写 `app/templates/vue3-springboot/README.md`**

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

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_springboot_template.py tests/test_scaffold.py -q`
Expected: PASS

- [ ] **Step 5: 全量回归**

Run: `pytest tests -q`
Expected: 全绿（Phase A 未碰编排，任何红都是本相引入，就地修）。

- [ ] **Step 6: 提交**

```bash
git add app/templates/vue3-springboot/README.md tests/test_springboot_template.py
git commit -m "feat(template): stack README and full-render contract tests for vue3-springboot template"
```

## Phase B — dev 预览链路

### Task B1: dev compose 的 springboot 分支 + `.env` 运行密钥

**Files:**
- Modify: `app/deploy/compose.py`
- Test: `tests/test_compose_render.py`

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_compose_render.py`）

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

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_compose_render.py -q`
Expected: 新用例 FAIL（`栈模板未支持: vue3-springboot`）；旧 `test_render_dev_compose_unknown_stack_raises` 同时变红——它拿 vue3-springboot 当未知栈，属预期改判，Step 4 处理。

- [ ] **Step 3: 实现卷常量与栈分支**

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

- [ ] **Step 4: 改掉旧栈闸门用例的改判**

`tests/test_compose_render.py`：删除旧 `test_render_dev_compose_unknown_stack_raises`（已被 Step 1 最后一条取代）；`test_render_dev_compose_no_marker_residue` 的栈元组扩为 `("vue3-fastapi", "static", "vue3-springboot")`；`test_templates_still_carry_markers` 的模板元组扩为 `(compose._DEV_COMPOSE, compose._FE_ONLY, compose._DEV_COMPOSE_SB)`。

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/test_compose_render.py -q`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add app/deploy/compose.py tests/test_compose_render.py
git commit -m "feat(compose): render vue3-springboot dev stack (private redis, .m2 volume, mvn spring-boot:run) with generated runtime secrets"
```

---

### Task B2: `HEALTH_BY_STACK` 收敛到 `/api/health` + 大型栈创建放行

**Files:**
- Modify: `app/services/app_service.py:22-23,58-61`
- Test: `tests/test_app_service.py`

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_app_service.py` 末尾）

```python
async def test_create_large_app_opened_with_api_health_probe():
    """大型栈放行（模板已落地），健康路径统一 /api/health（决策 D-7：actuator base-path 改为 /）。

    本用例真的建库、真的落盘（由 _clean 夹具回收），所以 Phase B 的编排接线在此已被一次性钉住。
    """
    sf = make_session_factory(make_engine())
    app = await app_service.create_app_project(
        sf, name="大型", slug=SLUG, scale="large", stack="vue3-springboot", root=str(ROOT))
    assert app["scale"] == "large" and app["stack"] == "vue3-springboot"
    assert (ROOT / "backend" / "pom.xml").is_file() and (ROOT / "schema" / "schema.sql").is_file()
    assert not list((ROOT / "sqlchange").glob(f"D{datetime.date.today():%Y%m%d}*"))   # 不追加 demo 种子
    meta = json.loads((ROOT / "app.json").read_text(encoding="utf-8"))
    assert meta["health"] == {"frontend": "/", "backend": "/api/health"}
    dev_y = (ROOT / "deploy" / "dev-compose.yml").read_text(encoding="utf-8")
    assert "maven:3.9-eclipse-temurin-17" in dev_y and "redis:7-alpine" in dev_y
    env = (ROOT / "deploy" / ".env").read_text(encoding="utf-8")
    assert compose._env_get(env, "JWT_SECRET") and compose._env_get(env, "REDIS_PASSWORD")
```

模块头补齐（文件里已有 `import shutil`、`from pathlib import Path`）：

```python
import datetime, json
from app.deploy import compose
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_app_service.py::test_create_large_app_opened_with_api_health_probe -q`

> 勘误（A4c 已收口）：`HEALTH_BY_STACK` 已改 `/api/health`（与常量同形的窄断言已进 `test_app_service.py`），
> 本 Step 不再是「health 路径 FAIL」；现在跑不过的是 **Step 1 里依赖 B1 的那半**（dev-compose 的 maven/redis
> 服务与 `.env` 里的 `JWT_SECRET`/`REDIS_PASSWORD`）。B2 实际验证的是「整栈真创建能走通到落盘」。

- [ ] **Step 3: 改实现**

```python
HEALTH_BY_STACK = {"vue3-fastapi": "/api/health", "vue3-node": "/api/health",
                   "vue3-springboot": "/api/health", "static": ""}
```

> 已随 **Task A4c** 提前完成（I-2 必修），本 Step 只剩下面那条注释。

`_check_scale_stack` 的 R-C 闸门**不删码**（模板目录落地后 `is_dir()` 自然为真，`vue3-node`/`static` 继续 400），只把注释改成当下事实：

```python
    # 裁定 R-C：模板未交付的栈在此提前 400（不放行则 scaffold.render 会冒「栈模板不存在 500」）。
    # vue3-springboot 已由 base-pro 移植交付；vue3-node / static 仍未开放。
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_app_service.py tests/test_apps_api.py tests/test_contract.py -q`
Expected: PASS（`vue3-springboot` 无存量应用，`app.json` 无历史值需迁移）

- [ ] **Step 5: 提交**

```bash
git add app/services/app_service.py tests/test_app_service.py
git commit -m "feat(app): open vue3-springboot creation and unify backend health path to /api/health"
```

---

### Task B3: dev 联合健康判定 + 按栈超时

**Files:**
- Modify: `app/deploy/settings.py`（DEFAULTS + `health_timeout`）
- Modify: `app/deploy/engine.py:147-157,207-214`
- Test: `tests/test_engine.py`

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_engine.py`；纯逻辑用例，**不要**加 `needs_docker` 标记）

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

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_engine.py -q -k "dev_health or health_timeout"`
Expected: FAIL — `AttributeError: ... has no attribute 'wait_dev_health'` / `ImportError: cannot import name 'health_timeout'`

- [ ] **Step 3: 实现按栈超时**

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

- [ ] **Step 4: 实现联合探针**

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

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/test_engine.py tests/test_dev_logs.py tests/test_deploy_basics.py -q`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add app/deploy/engine.py app/deploy/settings.py tests/test_engine.py
git commit -m "feat(dev): joint frontend+backend health gate and per-stack timeout for large apps"
```

---

### Task B4: 端到真跑——创建大型应用并登录预览（验收证据）

本 Task 无代码改动，只跑真环境并归档证据。跳不过就说明前面有错，**不允许注销售过**。

- [ ] **Step 1: 前置体检**

```powershell
docker info --format ok
docker start zerocode-pg16
docker manifest inspect docker.m.daocloud.io/library/maven:3.9-eclipse-temurin-17
docker manifest inspect docker.m.daocloud.io/library/redis:7-alpine
```

Expected: 三条均成功。拉不到镜像时先 `docker pull docker.m.daocloud.io/library/<image>` 再 `docker tag` 回标准名（本机 Docker Hub 不可直连），并把这一前置写进模板 README 的「开始之前」。

- [ ] **Step 2: 走平台真创建**

启动平台后端（`start.bat` 或 `.venv\Scripts\python -m uvicorn app.main:app --port 8000`），在向导里选「大型」建一个应用（名字自取，如「工调系统」），确认：

1. 创建成功，卡片上 scale 为「大型 · vue3-springboot」
2. `apps/<slug>/` 下能看到 `backend/src/main/java/com/zerocode/app`、`schema/schema.sql`、`sqlchange/D20260101_01_rbac_seed.sql`
3. `deploy/dev-compose.yml` 含 redis/backend(maven)/frontend/migrate 四个服务
4. `deploy/.env` 里 `JWT_SECRET`、`REDIS_PASSWORD` 已生成非空值

- [ ] **Step 3: 起预览并等冷启动**

点「预览」，观察 dev 日志面板：迁移段应打出 `FOLD schema.sql` → `OK D20260101_01_rbac_seed.sql` → `ALL DONE`；首次编译下载依赖，**等到底（最长 900s）**，不重复点重启。

Expected: 状态到 `running`，总耗时 ≤900s（实测基线 ≈410s）。

- [ ] **Step 4: 登录验收（必留截图）**

1. 浏览器打开 `http://localhost:<dev_port>`，用 `admin` / `admin123` 登录成功
2. 七张基座页面逐个打开：首页、个人中心、用户管理、角色管理、菜单管理、部门管理、字典管理、操作日志、登录日志
3. 在「登录日志」页看到自己刚才那条登录记录（证明写链路 + 数据轨真的通了）
4. 浏览器控制台无 404/5xx 红叉

截图存到 `.qoder-tmp/t14_`（`t14_login.png`、`t14_pages.png`、`t14_loginlog.png`），并在本 Task 收尾写下实测耗时。

- [ ] **Step 5: 热重启计时**

停栈→再起栈，记录耗时（实测基线 77s）写入模板 README 的「开始之前」，替换估计值「之后约 1 分钟」为实测数字。

- [ ] **Step 6: 全量回归 + 提交**

```bash
cd frontend; npm run test; npm run build; cd ..
```

```bash
git add app/templates/vue3-springboot/README.md
git commit -m "docs(template): record measured dev startup timings from the large-stack preview run"
```

## Phase C — prod 发布链路

### Task C1: prod compose 的 springboot 分支（镜像即产物）

**Files:**
- Modify: `app/deploy/compose.py`
- Test: `tests/test_compose_render.py`

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

> `/uploads` 由模板 `backend/Dockerfile` 的 `ENV UPLOAD_DIR=/uploads` 与 `WebMvcConfig` 的资源处理器（`/uploads/**`，SecurityConfig 已 permitAll）对齐；nginx 的 `/uploads/` 反代在 Task A5 已接。dev 侧走 `/src/uploads`（挂载卷），两个环境都不把上传件写进容器层。

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

- [ ] **Step 6: 跑测试确认通过**

Run: `pytest tests/test_compose_render.py -q`
Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add app/deploy/compose.py tests/test_compose_render.py
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
| D-1 模板完整移植 | A3（复制）+ A4/A5（平台约定改造）+ A6（双轨）+ A7（渲染体检） |
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

