# 沙箱扩容为工作台：run_shell / 仅公网 / OOXML 三件 / import_artifact（2026-11-23）

- 决策依据：[ADR-0066](../adr/0066-sandbox-workbench-shell-ooxml-artifact-import.md)（本票章程）、
  [ADR-0056](../adr/0056-run-python-isolated-sandbox.md)（沙箱，本票修订其「只 Python」「跑完即销毁」
  「二进制导入另票」三条）、ADR-0046（原红线，本票再次改写）、ADR-0053（SSRF 网段）、
  ADR-0060（技能脚本经沙箱跑）、ADR-0031/0037（sidecar 隔离范式与 run 收口清理）
- 术语：CONTEXT.md「Python 沙箱（run_python）」「沙箱命令面（run_shell）」「办公文档（OOXML 三件）」
  「沙箱依赖安装」
- 前序规格：[2026-11-19-run-python-sandbox.md](2026-11-19-run-python-sandbox.md)（本票是它的续票，
  不重复其已成立的部分）

## 1. 问题陈述

沙箱能算数，但办不了真实交付物。三处断点，都已实探确认：

1. **没有库**：`SANDBOX_IMAGE = "python:3.12-slim"` 是裸官方镜像，`import openpyxl` 报
   `ModuleNotFoundError`。读一份 xlsx 资料、生成一份 docx 报告都做不到。而前序规格 §4 原本就写了
   「镜像预装 pandas/openpyxl」「允许脚本内 pip install」——**实现期没落地**。
2. **没有网络**：实现落的是 `--network none` fail-closed，005 出口过滤未做，于是 pip 与取公网数据
   双双断掉。技能脚本因此被限定「只用纯标准库」（`build_python_prompt`）。
3. **产物拿不到**：二进制产物只回 `{name, bytes}` 元数据，且 scratch 在 `finally` 里当场
   `shutil.rmtree`（[python_tools.py:131](../../app/agent/python_tools.py#L131)）。**生成了也当场消失**。
   前序规格 §6 把「二进制产物导入工作区」列为另票——就是本票。

另有一处名实落差要诚实收口：沙箱里 `/bin/sh` + coreutils **现在就能用**，模型写
`subprocess.run([...])` 即刻能跑终端命令。红线管住的是工具面入口，不是沙箱内能力；藏着的执行面
让确认卡展示的意图不透明——用户批准的是「跑这段 Python」，里面藏着 `curl -T` 看不出来。

## 2. 方案概述

沙箱从「纯计算容器」升级为**围栏不变的工作台**：容器触达面一条不放宽，放宽的是容器内的能力。

- **新增 `run_shell`**：入参 `command`（命令行字符串，经 `sh -c` 跑，因此管道与重定向可用）。
  与 `run_python` 同镜像、同一次性容器、同一组围栏。**接任意命令行，不做命令白名单**。
- **网络出口落地 005**：容器网络层强制仅公网，`run_python` 与 `run_shell` 共用同一条过滤。
- **自建沙箱镜像**：预装办公三件库 + 数据处理库 + 常用 CLI（git/curl/zip/unzip）。apt 运行期
  不支持（非 root 硬约束），系统工具一律走镜像预装。
- **新增 `import_artifact`**：把 scratch 里的产物（含二进制）落进工作区，套 `CONFIRM_WRITE`
  确认卡 + `file_change` 留痕。
- **scratch 生命周期延长到 run 收口**：`import_artifact` 是独立的一次工具调用，产物必须活过
  产生它的那次执行。容器仍一次性。

典型链路：`read_file` 看数据 → `run_python` 读 `/workspace/资料.xlsx`、生成 `/scratch/报告.docx`
→ `import_artifact` 落盘 `报告.docx` → 用户在文件树看到并下载。

### 接缝（测试靶心）

**最高接缝仍是 `execute_tool(ctx, name, args)`**——三个工具全走这一条 dispatch 缝，不新建接缝：

| 关注点 | 复用的既有接缝 |
|---|---|
| 容器执行 | `engine.docker` + `engine.preflight_docker`（[engine.py:39](../../app/deploy/engine.py#L39) / [:128](../../app/deploy/engine.py#L128)），不另起 docker 客户端 |
| 私网网段 | `guard.is_internal_ip`（[guard.py:36](../../app/source/guard.py#L36)）——其 docstring 已声明「与沙箱网络层出口过滤共用同一份」，本票是它的**首个真实调用方** |
| 产物落盘围栏 | `tools.resolve_in_workspace` + `deploy.guard.is_protected` + `tools._record_change`（与 `write_file` 逐字同一套） |
| 确认闸 | 注册表 `CONFIRM_WRITE` 驱动，loop 通用分支自动接管，**不改 loop 的 if 链** |
| run 收口清理 | `self._tool_ctx` 收口位（[loop.py:269](../../app/agent/loop.py#L269)），与 `close_browser_session` 并列，覆盖正常/取消/报错三条出口 |
| 打包白名单 | `scripts/build_output.py` 的 `BROWSER_REQUIRED` 范式 + `tests/test_build_output.py` 的遍历不变式 |

新 handler 落在既有 `app/agent/python_tools.py`（`run_shell_tool` / `import_artifact_tool`），
不新建模块——三者共用 scratch 与镜像常量，拆开会让「同一套围栏」变成两份要同步的代码。

## 3. 用户故事

### run_shell 命令面

- **US-01 执行成功**：合法 `command` → `ok:true`，带 `exit_code`、`duration_seconds`、`output`
  （stdout+stderr 合流尾部裁剪）。脚本非零退出是**正常结果**（`ok:true`），沿 `run_python` 语义。
- **US-02 任意命令行放行**：`ls -la /workspace`、`git --version`、`cat a | grep b > /scratch/c`
  一律放行，无命令名白名单。管道/重定向可用（经 `sh -c`）。
- **US-03 工作区只读**：`echo x > /workspace/a.txt` → 只读文件系统报错（EROFS），工作区零写入。
- **US-04 deploy 凭据不可读**：`cat /workspace/deploy/.env` → 不存在/空内容。遮蔽在挂载层成立。
- **US-05 写只进 scratch**：写 `/scratch/*` 成功；产物出现在 `artifacts` 清单里。
- **US-06 非 root**：`apt-get install -y curl` → dpkg lock 权限拒，工具回**可读引导**
  「系统包由平台预装进镜像，运行期不支持 apt」，不是裸 traceback。
- **US-07 无凭据/无 docker.sock**：容器内无平台库/应用库凭据、无 docker.sock；`docker ps` → 失败。
- **US-08 确认流**：`agent.write_confirm` 开 → `confirm_request` 展示**完整命令行原文**，批准才跑；
  拒绝 → `ok:false` + `confirm:"denied"`，不跑；关 → 直接跑。docs/ 新建与本轮问过用户的豁免
  （ADR-0033）沿通用分支自动适用，不特判。
- **US-09 预装 CLI 可用**：`git`、`curl`、`zip`、`unzip` 在镜像内可执行（裸 slim 镜像里 git/curl
  都没有，实探确认）。
- **US-10 资源/超时/取消/失败零副作用**：mem/cpu/pids 超限被杀、超 timeout 被终止、`ctx.cancel`
  终止容器 → 均 `ok:false` 带原因；平台故障（docker 不可用）同样 `ok:false`，不抛 ToolError。
- **US-11 身份与子 run**：`SCOPE_ALWAYS`，schema 无 `app_id`/`slug`/`path`；`SUBRUN_TOOL_NAMES`
  **不收录**（执行能力，非只读语义，ADR-0052）。

### 网络出口（005 落地，两个工具共用）

- **US-12 私网出口网络层拒绝**：脚本用 **raw socket 直连私网 IP**（不经任何 URL 函数）→ 连接失败。
  宿主端口（5433/8100）、容器别名、应用 dev 端口、loopback、link-local、保留网段全拒。
- **US-13 公网出口放行**：`pip install`、`curl https://<公网>` 通。
- **US-14 网段定义同源**：出口过滤的网段表由 `guard.is_internal_ip` 生成，**不另抄一份**。
  改判定则两处同步，由测试钉成一条等式。
- **US-15 run_python 一并受益**：出口过滤对 `run_python` 同样生效（不是只给 shell 开），
  且 `--network none` 从两个工具的 argv 里移除。
- **US-16 容器无需 NET_ADMIN**：过滤在**宿主侧**成立，容器仍以 `--user 1000:1000` 跑、不加 cap。
  脚本自己改不了过滤规则。

### 依赖与办公文档

- **US-17 预装库开箱可用**：`import docx / openpyxl / pptx / pandas / numpy` 无需安装即成功。
- **US-18 运行期 pip 可用且不残留**：`pip install <冷门库>` 装到 scratch 内的目标目录后成功执行；
  **下一次调用该库不存在**（容器一次性，无跨调用状态）。
- **US-19 pip 换源**：沿平台 dependency mirror 的 PyPI 换源位，网络受限服务器可装。
- **US-20 读办公文档**：读 `/workspace/<x>.xlsx` / `.docx` 并回文本结果（CSV/JSON）→ 经
  `write_file` 落盘，链路不变。
- **US-21 生成办公文档**：生成 `.docx`/`.xlsx`/`.pptx` 到 scratch → `artifacts` 回元数据 →
  经 `import_artifact` 落工作区。
- **US-22 老格式明确拒**：`.doc`/`.xls` → 明确拒绝并引导「另存为 .docx/.xlsx」，
  **不静默降级解析**、不引入 LibreOffice。

### 产物导入

- **US-23 导入成功**：`import_artifact(name="报告.docx", path="out/报告.docx")` → 文件落工作区、
  `file_change` 留痕（action=create/edit）、回 `{ok, path, bytes}`。
- **US-24 入参只能是本次 scratch 内的相对名**：`name` 为绝对路径、含 `..` 越界、或指向本次 run
  未登记过的 scratch → 拒。**它不得成为绕过 `write_file` 的任意文件读写通道**（本票最关键的
  安全断言：不收内容直传、不收宿主路径）。
- **US-25 目标路径围栏**：`path` 越界/绝对路径 → `resolve_in_workspace` 拒；`app.json`/`deploy/`
  → `is_protected` 拒。与 `write_file` 逐字同一套。
- **US-26 二进制不可回滚，明说**：结果带「未存原文，不可回滚」，沿 `delete_file` 既有口径。
  `_record_change` 传 `before/after=None`；`revert_change` 对该文件报「那条记录未存原文→不可回滚」。
- **US-27 确认流**：`CONFIRM_WRITE` 档，确认卡展示产物名、体积、目标路径；拒绝则不落盘、scratch 原样。
- **US-28 跨 run 取不到**：上一个 run 的 scratch 已随收口清理 → 明确拒「产物已随上次执行结束清理，
  请重新生成」，不是裸 FileNotFoundError。
- **US-29 落盘后可下载**：`/fs/download` 能取到该二进制（既有端点，零改动）。
- **US-30 身份与子 run**：`SCOPE_ALWAYS`；`SUBRUN_TOOL_NAMES` 不收录（写能力）。

### scratch 生命周期

- **US-31 per-run 一个 scratch**：同一 run 内多次 `run_python`/`run_shell` 共用一个 scratch 目录，
  登记在 `ctx.run_state`；产物可分步累积。
- **US-32 收口清理三条出口**：run 正常结束/取消/报错都清理 scratch，挂在既有 `self._tool_ctx`
  收口位；清理失败只记日志，不把对话结束的读数拖住。
- **US-33 孤儿清扫**：进程启动时清扫残留的 `zc-pysandbox-*` 目录（崩溃/kill -9 后的泄漏）。
- **US-34 配额**：scratch 超配额（默认 100MB）→ 拒写并回可读原因，防磁盘满。

### 红线 / 注册表 / 前端 / 提示词 / 打包

- **US-35 红线测试修订**（[test_preview_tools.py:1167](../../tests/test_preview_tools.py#L1167)）：
  names 集合加 `run_shell`、`import_artifact`；**`run_shell` 从 forbidden `execute_tool` 名单里移除**
  （该名单现为 `run_shell/exec/terminal/run_command/preview_stop`，其余四个不动）；
  `run_shell` 豁免 forbidden-props 断言（`command` 合法）；`run_python` 现有豁免不动；
  `run_check` preset-only 断言不动；其余工具仍禁 `command/cmd/argv/shell/script`。
  红线 docstring 改写为「命令与代码执行只允许在隔离沙箱内，或平台写死的 preset」。
- **US-36 注册表钉住**：`run_shell` 进既有 `python` 组（组 id 不改，见 §7）；`import_artifact`
  进 `fs` 组（写语义，与 `write_file` 同列）；两者 `scope=SCOPE_ALWAYS`、`confirm=CONFIRM_WRITE`、
  `pass_confirmed=True`、`result_policy=CHECK_POLICY`；`llm` 名单与前端名单**同名同序**。
- **US-37 前端卡片**：`run_shell` 执行卡（命令行原文、exit_code、duration、输出尾部）；
  `import_artifact` 卡（产物名、体积、落盘路径、不可回滚提示）。实时态/已答态/刷新后历史行
  **三态一致**；确认卡文案沿执行确认族。
- **US-38 提示词**：新增 `run_shell` 用法（何时用命令而非 Python、可用 CLI 以镜像内为限、
  apt 不支持）；`build_python_prompt` 里「容器离线」「只用纯标准库」两句**必须改**——
  ADR-0060 技能脚本的天花板由此抬高。
- **US-39 结果预算**：`output` 尾部裁剪 + `artifacts` 内容 cap，套 `CHECK_POLICY` 族。
- **US-40 自建镜像与打包**：新增沙箱镜像目录（沿 `browser/` 范式），`scripts/build_output.py`
  加同源白名单，`tests/test_build_output.py` 加**遍历该目录 `*.py`/Dockerfile 的不变式**；
  交付 compose 声明该镜像的 build、预热 `docker pull`、部署文档同步。

## 4. 已定决策（拷问共识，不翻案）

| 决策点 | 结论 |
|---|---|
| 命令面位置 | 沙箱容器内，**不**在宿主/平台容器、**不**在应用 dev 容器 exec |
| 工具名 / 入参 | `run_shell` / `command`（命令行字符串，经 `sh -c`） |
| 命令宽度 | 任意命令行，**不做白名单**（白名单可被 `subprocess` 绕过，属假围栏） |
| 红线措辞 | 「命令与代码执行只允许在隔离沙箱内，或平台写死的 preset」 |
| 网络 | 仅公网，**容器网络层**强制；网段与 `guard.is_internal_ip` 同源；两个工具共用 |
| 过滤位置 | 宿主侧（容器无需 NET_ADMIN、不加 cap、仍非 root） |
| 办公格式 | 只 OOXML 三件；老 `.doc`/`.xls` 明确拒 + 引导另存 |
| 依赖 | 镜像预装 + 运行期 pip 装到 scratch，每次重装 |
| 系统包 | apt 运行期不支持；git/curl/zip/unzip 等预装进镜像 |
| 载体 | **仍是一次性容器**（`docker run --rm`），不改常驻、不与 browser 合并 |
| scratch | per-run 一个，活到 run 收口；容器无状态 ≠ 产物无状态 |
| 产物导入 | 新增 `import_artifact`，套 `CONFIRM_WRITE` + `file_change`；只收本次 scratch 内相对名 |
| 二进制回滚 | **不可回滚，结果明说**（沿 `delete_file` 口径，不改 `revert_change`） |
| 确认 | 三个工具全走 `CONFIRM_WRITE`，零新增配置 |
| 子 run | `run_shell`/`import_artifact` 均不收录 |

技术兜底默认值（实现期可调，非访谈结论）：timeout 240s（pip 计入同一预算）、scratch 配额 100MB、
mem 1g / cpu 1 / pids 256（沿前序规格）、`output` 尾部 16k 字符。

## 5. 测试决策

docker 一律替身桩（沿 `tests/test_python_tools.py` 既有桩法：`monkeypatch.setattr(engine, "docker", spy)`
验 argv 与挂载参数），真容器走手工矩阵。

| 接缝 | 测试 | 覆盖 |
|---|---|---|
| `execute_tool(ctx, "run_shell"/"import_artifact", args)` | `tests/test_python_tools.py`（扩） | US-01..11、US-23..30：argv 与挂载、EROFS、deploy 遮蔽、非 root 引导、确认三态、入参围栏矩阵 |
| 出口过滤规则生成 | `tests/test_python_tools.py`（扩） | US-14/16：网段表与 `guard.is_internal_ip` 同源等式；容器 argv 不含 `--network none`、不含 cap-add |
| scratch 生命周期 | `tests/test_python_tools.py` + `tests/test_agent_loop.py` | US-31..34：per-run 登记、三条出口清理、孤儿清扫、配额 |
| 注册表 | `tests/test_agent_registry.py` | US-36：组成员、confirm 档、scope、pass_confirmed、前端名单同名同序 |
| 红线 | `tests/test_preview_tools.py::test_no_command_execution_tool_exists` | US-35：names 加两个、forbidden 名单移除 run_shell、props 豁免、其余工具仍禁 |
| 打包 | `tests/test_build_output.py` | US-40：白名单三处同源 + 遍历目录不变式 |
| 前端 | `ChatMessages.test.js` / `ConfirmCard` | US-37：两张卡三态路由、确认卡文案 |
| 网络层真实生效 | **手工矩阵（真容器）** | US-12/13：raw socket 直连私网被拒、公网通 |

手工矩阵（真容器，逐项留证）：`ls`/管道/重定向可用 → `echo > /workspace` 报 EROFS →
`cat deploy/.env` 空 → `apt-get install` 权限拒且回可读引导 → `git --version`/`curl` 可执行 →
`import openpyxl/docx/pptx` 成功 → `pip install` 成功后**下次调用不存在** →
raw socket 连宿主 5433 被拒 → 公网 pip 通 → 生成 docx/xlsx/pptx 三件 →
`import_artifact` 落盘后文件树可见、`/fs/download` 可下载 → `revert_change` 报不可回滚 →
run 取消后 scratch 已清 → kill -9 后重启清扫孤儿。

**完成定义**：pytest 全量**串行**绿（共享 `zerocode_test` 库并发会死锁）＋ vitest `--maxWorkers=4` 绿
＋手工矩阵逐项过＋浏览器实测一遍典型链路（读 xlsx → 生成 docx → import_artifact → 文件树下载）。

**最容易实现成假绿的三处**（审查重点）：

1. **出口过滤做成应用层闸**：调 `reject_internal_host` 校验 URL 不算数——脚本有 raw socket。
   必须真容器验直连私网 IP 被拒。
2. **`import_artifact` 收宿主路径或内容直传**：那它就是绕过 `write_file` 的任意读写通道。
   入参围栏矩阵必须覆盖绝对路径、`..` 越界、未登记 scratch。
3. **守卫接线只测纯函数**：网段表生成有测试 ≠ 它被真的装进容器启动路径。按变异测试钉住接线
   （改坏规则生成，测试必须红）。

## 6. 明确不做

- 宿主/平台容器 shell、应用 dev 容器 exec
- 命令白名单、只读命令白名单
- 与 browser sidecar 合并、常驻沙箱、可写 pip 缓存卷、跨 run 状态保留
- 给容器 root、运行期 apt
- 老 `.doc`/`.xls`、LibreOffice、PDF 读写
- 其他语言运行时（node/go/jvm）
- 二进制产物的字节级回滚（不改 `revert_change` 的文本比对）
- 产物自动全落工作区（免确认）、临时下载区 + TTL
- 工作区直接写（写仍只进 scratch，落盘仍经工具确认）
- 应用库/平台库凭据、docker.sock、宿主文件系统
- 私网/本机/容器别名网络
- 子 run 收录、GPU、脚本内容静态审查

## 7. 补充说明

- **与前序规格的关系**：本票不改 `run_python` 的入参契约与技能脚本模式（ADR-0060），只改它的
  网络 argv、镜像、scratch 生命周期与提示词口径。前序规格 §6 的「二进制产物导入另票」由本票兑现。
- **`python` 组 id 名实不符是刻意保留**：`run_shell` 进 `python` 组后组名不再精确，但改组 id 会
  牵动前端分组与 `llm` 名单的同名同序钉法。术语上它现在是「沙箱组」，代码 id 不动。
- **镜像从「拉官方」变成「平台自建」是本票最大的下游影响**：多一个镜像要构建、要进交付包、
  要在服务器预热，构建期换源沿用 `ZEROCODE_APT_MIRROR`/`ZEROCODE_PYPI_MIRROR` 那套三处同源约束。
  `browser/` 踩过的「漏一个源文件 → 容器起来即崩」在这里同构，所以打包期宁可拒包也不出包。
- **宿主侧过滤对交付部署的要求**：需要专用固定子网 network + 宿主防火墙链规则，服务器上要有
  相应权限。这是运维可见的新前置，必须写进部署文档与自检探针，不能只在开发机成立。
- **渗出面扩大是明确接受的取舍**：公网 + 预装 curl + 只读工作区意味着源码外发一条 `curl -T` 就够，
  比 ADR-0056 时更顺手。`deploy/` 凭据、库凭据、docker.sock 仍不可触达，写工作区仍走确认围栏。
- **二进制不可回滚的理由要留在代码注释里**：`_handle_revert_change` 要求
  `before_content IS NOT NULL` 且按 UTF-8 文本比对磁盘现状，二进制过不了那道比对。这不是遗漏，
  是沿 `delete_file` 的既有口径；想支持字节级回滚需要另设备份通道与迁移，不在本票。