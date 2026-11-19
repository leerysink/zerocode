# Python 沙箱 run_python（2026-11-19）

- 决策依据：[ADR-0056](../adr/0056-run-python-isolated-sandbox.md)（红线改写为「preset + 沙箱」）、
  ADR-0046（原红线，本票修订）、ADR-0031/0037（browser sidecar 隔离范式）、ADR-0053（web SSRF 判定）、
  ADR-0052（子 run 只读白名单）、ADR-0027（db 工具受控通道）
- 术语：CONTEXT.md「Python 沙箱（run_python）」「预设自检（run_check / preset）」

## 1. 问题陈述

Agent 能写脚本却跑不了：数据处理、算法验证、临时计算这类活，现状只能靠 `db_query` + 目测，或写个
.py 文件干看着。`run_check`（[app/agent/check_tools.py](../../app/agent/check_tools.py)）只能跑平台
写死的检查 preset，不能传脚本。缺的是**受控的任意脚本执行能力**。

但 ADR-0046 红线封死通用命令执行面——`run_shell` 被否，因 shell 可 `cat deploy/.env`（绕过 M-3
凭据读禁）、`rm -rf`（绕过写保护与确认卡）、`curl` 外发用户代码。Python 图灵完备，逐字适用同一理由。
所以本票不是「开一个 shell」，而是**开一个触达面被围栏锁死的隔离沙箱**：给执行能力，同时让 M-3、
写保护、确认卡、回滚全部围栏原样成立。

## 2. 方案概述

Agent 工具面新增 `run_python`：入参 `code`（Python 源码字符串）。服务端在**一次性容器**里跑
（`docker run --rm`，与 `run_check`/`sqlrun` 同构但**不**复用应用 dev 栈），容器：

- **工作区只读挂载**到固定路径（如 `/workspace`），`deploy/` 用空挂载遮蔽（保 M-3）；
- 独立可写 **scratch 输出区**（如 `/scratch`），脚本写只进这里，**不直接写工作区**；
- **无应用库/平台库凭据、无 docker.sock、非 root、限资源**（同 browser sidecar 范式）；
- **网络仅公网**：容器网络层出口过滤禁私网/保留网段，共用 `guard.reject_internal_host` 的网段定义。

跑完回 stdout/stderr（尾部裁剪）+ scratch 产物清单/内容；模型经 `write_file` 把文本产物落盘，
确认/留痕/回滚沿 fs 工具既有接缝。确认沿 `CONFIRM_WRITE` 档（`agent.write_confirm`）。

典型链路：`read_file` 看数据 → `run_python` 跑处理脚本（读 `/workspace` 数据、写 `/scratch/out.csv`）
→ 工具回产物内容 → `write_file` 落盘 `out.csv` → `edit_file`/预览接线。

### 接缝（测试靶心）

最高接缝是 `execute_tool(ctx, "run_python", args)`——与 web_download/fs 工具同一条 dispatch 缝：

- 容器执行复用 `engine.docker`（[app/deploy/engine.py:39](../../app/deploy/engine.py#L39)）+
  `engine.preflight_docker`（[:128](../../app/deploy/engine.py#L128)），不另起 docker 客户端；
- 私网网段定义复用 `guard.reject_internal_host`（[app/source/guard.py:36](../../app/source/guard.py#L36)，
  判定在 [:53](../../app/source/guard.py#L53)）——**网络层强制**，不是应用层 URL 闸（见 §7）；
- 产物落盘复用 `write_file` 既有接缝（`tools.resolve_in_workspace` + `deploy.guard.is_protected` +
  `tools._record_change`），run_python 自身**不写工作区**；
- 确认闸由注册表 `CONFIRM_WRITE` 驱动，loop 通用分支自动接管，**不改 loop 的 if 链**。

新 handler `python_tools.run_python_tool(ctx, args, *, confirmed=False)`（新模块
`app/agent/python_tools.py`），注册表惰性引用；`run_check` 一行不动。

## 3. 用户故事

- **US-01 执行成功**：合法 `code`，容器 `python` 跑完 exit 0 → `ok:true`，带 `exit_code`、
  `duration_seconds`、`stdout`/`stderr`（尾部裁剪）、`artifacts` 清单。
- **US-02 读工作区**：脚本 `open('/workspace/<项目文件>')` 可读源码/数据文件（只读挂载）。
- **US-03 deploy 凭据不可读**：`/workspace/deploy` 被空挂载遮蔽，脚本读 `deploy/.env` →
  不存在/空内容；M-3 读禁在挂载层成立，不靠提示词约束。
- **US-04 写只进 scratch**：脚本写 `/workspace/*` → 只读文件系统报错（EROFS）；写 `/scratch/*`
  成功。工作区零写入。
- **US-05 产物回读**：工具扫 scratch，回 `artifacts:[{name,bytes,content?}]`——文本且 under cap
  回全文，超 cap/二进制只回元数据 + 截断说明；模型经 `write_file` 落盘文本产物。
- **US-06 私网出口网络层拒绝**：脚本 `socket.connect` 私网/本机/容器别名 IP → 网络层拒绝（连接
  失败），网段定义共用 `guard.reject_internal_host`；**不是**应用层 URL 闸。
- **US-07 公网出口放行**：脚本访问公网（PyPI、公网 API）→ 放行。
- **US-08 无凭据/无 docker.sock**：容器内无平台库/应用库凭据、无 docker.sock；脚本连平台库、
  起容器 → 失败。
- **US-09 确认流**：`agent.write_confirm` 开 → 发 `confirm_request` 展示脚本全文，批准才跑；
  拒绝 → `ok:false` + `confirm:"denied"`，不跑；关 → 直接跑。docs/ 新建 + 本轮问过用户的豁免
  （ADR-0033）沿通用分支自动适用，不特判。
- **US-10 资源上限**：mem/cpu/pids-limit 超限 → 容器被杀，`ok:false` 带原因（OOM/超 pid）。
- **US-11 超时**：超 timeout（默认 120s）→ 容器终止，`ok:false` 带原因，scratch 清理。
- **US-12 取消**：`ctx.cancel` → 终止容器、清理 scratch，`ok:false` 带原因。
- **US-13 失败零副作用**：脚本异常/平台故障（docker 不可用等）→ 工作区原样（只读）、scratch
  清理，`ok:false` 带原因；沿 run_check/web_fetch 失败语义，不抛 ToolError。
- **US-14 非应用工作区可用**：`SCOPE_ALWAYS`，不依赖 `_app_of`；workspace 由 `ctx.root` 服务端
  解析，schema 无 `app_id`/`slug`/`path` 参数（沿 ADR-0037/38 身份口径）。
- **US-15 子 run 不挂载**：`SUBRUN_TOOL_NAMES` 不收录（执行能力，非只读语义，ADR-0052）。
- **US-16 红线测试修订**：`test_no_command_execution_tool_exists`
  （[tests/test_preview_tools.py:1167](../../tests/test_preview_tools.py#L1167)）——names 集合加
  `run_python`；`run_python` **豁免** forbidden-props 断言（它是 sanctioned 沙箱，`code` 参数合法）；
  其余工具仍禁 `command/cmd/argv/shell/script`；`run_check` preset-only 断言不动；forbidden
  `execute_tool` 名单（run_shell/exec/terminal/run_command/preview_stop）**不含** run_python。
- **US-17 注册表钉住**：`run_python` 进新组（`python`）；`llm` 名单与前端名单同名同序
  （`tests/test_agent_registry.py` 既有钉法）。
- **US-18 前端卡片**：`run_python` 执行卡（脚本摘要、exit_code、duration、stdout 尾部、artifacts；
  实时态「正在执行…」），实时/已答/刷新后历史行三态一致；确认卡文案「执行确认 / 确认执行 /
  已批准执行」。
- **US-19 提示词**：增补 run_python 用法——何时用（数据处理/计算/验证）、读 `/workspace`、写
  `/scratch`、产物经 write_file 落盘、仅公网、不记凭据、改完跑一次不反复跑。
- **US-20 结果预算**：stdout/stderr 尾部裁剪 + artifacts 内容 cap，套 `result_policy`
  （CHECK_POLICY 族，16k 量级）。

## 4. 已定决策（拷问共识，不翻案）

| 决策点 | 结论 |
|---|---|
| 形态 | 隔离沙箱跑模型给的任意 Python 脚本（不是 preset） |
| 工具名 / 术语 | `run_python` / 「Python 沙箱」 |
| 文件触达 | 工作区**只读**挂载 + 独立 scratch 输出区；不直接写工作区 |
| deploy | 凭据目录排除（空挂载遮蔽），保 M-3 读禁 |
| 产物落盘 | 经 `write_file`（确认/留痕/回滚围栏原样成立） |
| 库凭据 | 不给（db_query/db_write 已受控）；无 docker.sock、无平台库凭据、非 root |
| 网络 | 仅公网，复用 web SSRF 私网网段定义（网络层强制） |
| 载体 | 一次性容器（`docker run --rm`），跑完即销毁 |
| 确认 | `CONFIRM_WRITE`，受 `agent.write_confirm` 控制，零新增配置 |
| 挂载范围 | `SCOPE_ALWAYS`（不依赖应用身份） |
| 子 run | 不收录只读白名单 |
| 失败语义 | 带原因的正常结果，不抛 ToolError |
| 红线 | ADR-0046 改写为「preset + 隔离 Python 沙箱」，测试同步修订（ADR-0056） |

规格综合时定的实现口径（非新访谈）：

- **参数名 `code`**：Python 源码字符串。刻意避开红线测试 forbidden 的 `script` 字面名；
  run_python 在测试里显式豁免（US-16），不靠改名绕断言。
- **镜像与依赖**：`python:3.x-slim`（与平台后端运行时对齐，实现期核对版本）；镜像**预装常用
  数据处理库**（pandas/numpy/openpyxl/requests/python-dateutil 等）开箱即用，**允许脚本内
  pip install** 补装（公网 PyPI，沿平台 dependency mirror 的 PyPI 换源位）。
- **网络层出口过滤**：容器 netns 内 iptables/nftables 禁私网/保留网段（具体机制 tickets 定），
  网段定义与 `guard.reject_internal_host` 共用同一份（is_private/is_loopback/is_link_local/
  is_reserved/is_unspecified/is_multicast + localhost）。应用层 URL 闸不足以约束任意脚本（§7）。
- **deploy 遮蔽**：只读挂载工作区后，用空 tmpfs/bind 遮蔽 `/workspace/deploy`，真遮蔽而非约定。
- **资源默认**：mem 1g、cpu 1、pids-limit 256（防 fork bomb）、timeout 120s、scratch 配额 100MB
  （防磁盘满）、stdout/stderr 尾部 16k 字符。均为技术兜底默认值，实现期可调。
- **产物落盘限文本**：`write_file` 是 UTF-8 文本通道，v1 文本产物（CSV/JSON/SQL/.py/.md）可落盘；
  二进制产物（图片/xlsx）导入不在本票（§6）。
- **结果预算**：CHECK_POLICY 族（16k 量级），artifacts 内容单独 cap。

## 5. 测试决策

docker 一律替身桩（沿 `tests/test_agent_check_tools.py` 既有桩法），真容器走手工矩阵。

| 接缝 | 测试 | 覆盖 |
|---|---|---|
| `run_python_tool` / `execute_tool` | `tests/test_python_tools.py`（新） | US-01..13：执行/只读挂载/deploy 遮蔽/scratch 隔离/产物回读/网络层私网拒/资源/超时/取消/失败零副作用（docker 桩验 argv 与挂载参数） |
| 注册表 | `tests/test_agent_registry.py` | US-15/17：python 组成员、confirm 档、scope、pass_confirmed 接线、前端名单同名同序 |
| 红线测试 | `tests/test_preview_tools.py::test_no_command_execution_tool_exists` | US-16：names 加 run_python、豁免断言、其余工具仍禁命令参数、run_check 不动 |
| loop 确认流 | `tests/test_agent_loop.py` | US-09：开关开/关、批准/拒绝三态（**不给 AgentRunner 加 kwarg**，既有 Spy 签名不动） |
| 前端组件 | `ChatMessages.test.js` / `ConfirmCard` | US-18：执行卡三态路由、确认卡文案 |
| 网络层过滤 | 手工矩阵 + 桩 | US-06/07：真容器私网连接被拒、公网通 |

手工矩阵（真容器）：读项目文件、deploy/.env 不可读、写工作区报 EROFS、写 scratch 成功、
私网 socket 被拒、公网 pip install 通、超时被杀、OOM 被杀、fork bomb 被 pids 拦、执行中途取消、
产物经 write_file 落盘。

**完成定义**：pytest 全量**串行**绿（共享测试库并发会死锁）＋ vitest `--maxWorkers=4` 绿＋
手工矩阵过＋浏览器实测一遍典型链路（read_file → run_python → 产物 write_file 落盘 → 预览/编辑见结果）。

## 6. 明确不做

- 通用 shell / 任意语言运行时（只 Python；不预留多语言）
- 工作区直接写（写只进 scratch；产出经 write_file）
- **二进制产物导入工作区**（write_file 文本限；图片/xlsx 导入通道另票）
- 应用库/平台库访问、docker.sock、宿主文件系统
- 私网/本机/容器别名网络
- 子 run 收录
- 持久沙箱会话、跨执行状态保留、GPU
- 脚本内逐文件写确认（确认是执行粒度，不是文件粒度）
- 脚本内容静态审查/消毒（与 write_file 能写任意代码同口径）
- run_check preset 扩容（backend_pytest 等不在本票）

## 7. 补充说明

- **与 ADR-0046 的关系**：红线从「无通用命令执行面，只允许 preset」改写为「…只允许 preset 与
  隔离 Python 沙箱」，以 ADR-0056 为准。红线保护的是「围栏不可绕过」，不是「禁止执行」本身——
  沙箱靠最小触达面让围栏成立，而非靠禁用执行。
- **网络层 vs 应用层（关键设计点）**：`web_fetch` 的 SSRF 闸是应用层 URL 检查
  （`normalize_web_url`/`_check_host`），只管住「平台发起的 HTTP 请求」。沙箱里的脚本有 raw
  socket，能绕开任何 URL 闸直连私网。所以「仅公网」**必须在容器网络层强制**（出口过滤），
  共用 guard 的私网网段定义，而不是调用 URL 函数。这是本票最容易被实现成假绿的地方。
- **渗出面是明确接受的取舍**：只读挂载 + 公网出网意味着项目源码可被脚本外发；但 deploy 凭据、
  应用库/平台库凭据、docker.sock 均不可触达，写工作区仍走 write_file 围栏。
- **二进制产物限制**：v1 数据可视化出图（matplotlib PNG）等二进制产物只能留在 scratch 被报告，
  不能经 write_file 落盘——已知限制，需另设 guarded import 通道（未来票）。数据处理产出文本
  （CSV/JSON/SQL）不受影响。
- **冷启动**：Python 镜像拉起秒级，远快于 mvn；预装常用库避免每次 pip install 冷启动重装。
- **deploy 遮蔽须真遮蔽**：空挂载覆盖 `/workspace/deploy`，不能靠提示词约束脚本不读——
  与 M-3 在 read_file 层的硬禁同口径。
- **实现期注意**：`app/agent/loop.py` 与 `tests/test_agent_loop.py` 可能有另一会话的未提交 WIP
  （沿 web-download spec 提醒），动这两处前先复核归属；本票预期不改 loop.py——确认闸走注册表
  通用分支。新增工具须同步注册表、llm 名单、前端名单、红线测试四处（防漂移）。