# 0066 沙箱扩容为工作台：run_shell 任意 argv、仅公网、OOXML 三件、产物经 import_artifact 落盘

- 状态：Accepted
- 日期：2026-11-23
- 关联：ADR-0046（红线，本票再次改写）、ADR-0056（沙箱，本票修订其「只 Python」与「二进制导入另票」）、
  ADR-0053（SSRF 网段定义）、ADR-0060（技能脚本经沙箱跑）、ADR-0031/0037（sidecar 隔离范式）、
  CONTEXT「Python 沙箱」「沙箱命令面」「办公文档」「沙箱依赖安装」

## 背景

Agent 能跑 Python 却办不了真实交付物：镜像是裸 `python:3.12-slim`（实探确认无 openpyxl），
`--network none` 让 pip 与取公网数据双双断掉，二进制产物只回 name/bytes 元数据且 scratch 跑完即
`rmtree`——**生成了也拿不到**。而规格 `2026-11-19-run-python-sandbox.md` §4 原本就写了「镜像预装
pandas/openpyxl」「允许脚本内 pip install」，实现期先落 fail-closed，005 出口过滤未做。所以本轮
大半不是新方向，是既定规格未实现的部分。

另有一处落差需要诚实收口：沙箱里 `/bin/sh` + coreutils **现在就能用**，模型写 `subprocess.run([...])`
即刻能跑终端命令（实探确认）。红线管住的是**工具面入口**，不是沙箱内能力；藏着的执行面让确认卡
展示的意图不透明。

## 决策

1. **新增 `run_shell`，载体与围栏与 `run_python` 同一套**：同镜像、一次性容器、工作区只读挂载、
   `deploy/` tmpfs 遮蔽、无库凭据、无 docker.sock、非 root、限资源、写只进 scratch。红线措辞由
   「无通用命令执行能力，只允许 preset 与隔离 Python 沙箱」改写为
   **「命令与代码执行只允许在隔离沙箱内，或平台写死的 preset」**。
2. **`run_shell` 接任意 argv，不做命令白名单**。沙箱内 Python 图灵完备且 `subprocess` 可用，命令
   白名单拦不住它，属假围栏（同规格 §7 对「应用层 URL 闸拦不住 raw socket」的论证）。边界由容器
   触达面成立，不由命令名成立。同步修订 `tests/test_preview_tools.py::test_no_command_execution_tool_exists`。
3. **网络出口落地 005：容器 netns 内网络层过滤，仅公网**，网段定义与 `guard.reject_internal_host`
   共用一份。pip 与 curl 由此可用；宿主、容器别名、应用 dev 端口仍拒。
4. **办公文档只支持 OOXML 三件**（`.docx`/`.xlsx`/`.pptx`），镜像预装
   python-docx / openpyxl / python-pptx + pandas / numpy。老 `.doc`/`.xls` 明确拒绝并引导另存为新格式，
   不引入 LibreOffice 这类转换依赖。
5. **依赖＝镜像预装 + 运行期 pip 装到 scratch，每次重装**；载体仍是一次性容器，保 ADR-0056
   「跑完即销毁、无状态残留」。
6. **系统级安装（apt）运行期不支持**——非 root，实探报 `Unable to acquire the dpkg frontend lock`。
   git / curl / zip / unzip 等常用 CLI **预装进镜像**（实探确认裸镜像里没有 git/curl/gcc）。新增系统
   工具要改镜像并重建，沿 ADR-0046「新增能力必须改平台代码」的刻意代价。
7. **二进制产物经新增 `import_artifact` 工具落工作区**，套 `CONFIRM_WRITE` 确认卡 + `file_change`
   留痕。入参只接受**本次 run 登记过的 scratch 内的相对名**，不接受宿主绝对路径、不接受内容直传
   ——否则它就成了绕过 `write_file` 的任意文件读写通道。**二进制导入不可回滚，结果必须明说**，
   沿 `delete_file` 既有口径：`_record_change` 的 `before/after=None` 表示「未存原文」，而
   `_handle_revert_change` 要求 `before_content IS NOT NULL` 且按 UTF-8 文本比对磁盘现状
   （[app/agent/tools.py:489](../../app/agent/tools.py#L489)），二进制过不了那道比对。落进工作区后
   既能 `/fs/download` 下载，也能参与预览与发布。
8. **scratch 目录生命周期延长到 run 收口**（修订 ADR-0056 的「跑完即销毁」）。`import_artifact` 是
   独立的一次工具调用，产生它的那次执行早已返回，scratch 若当场 `rmtree` 则产物必然取不到。
   改为 per-run 一个 scratch、登记在 `ctx.run_state`，run 收口时清理，进程启动清扫孤儿。
   **容器仍是一次性**——「无状态残留」保的是容器与解释器，不是产物目录。

## 备选方案

- **宿主 / 平台容器开 shell**：能力最强，但 `deploy/.env` 可读、`rm -rf` 绕过写确认与回滚、源码可
  外发，ADR-0046 已否，本轮再次否决。
- **命令白名单 / 只读命令白名单**：在已有 `run_python` 的沙箱里可被 `subprocess` 绕过，是假绿；
  且只读白名单会挡住 pip 自身写盘，与「支持安装」自相矛盾。被否。
- **与 browser sidecar 合并成一个常驻容器**：netns 只有一个，无法同时成立「Chromium 能访问
  host-gateway 私网」与「脚本不能」；且工作区挂载会与浏览器里的验证 JWT（ADR-0038）合流到同一
  uid，两个原本互相隔离的风险面变成一个；`mem_limit 1g` 下跑飞的脚本会 OOM 掉别人的走查会话。被否。
- **可写 pip 缓存卷 / 常驻沙箱 sidecar**：省重装与冷启动，但跨调用状态残留，且缓存可被脚本投毒
  （一次装恶意包，后续每次调用都吃到）。被否，保一次性容器。
- **给容器 root 以开 apt**：破 ADR-0056 决策 3 的非 root 围栏，脚本可改容器内任意文件与挂载权限。被否。
- **产物跑完自动全落工作区（免确认）**：作废 ADR-0056 决策 2 的写围栏——一次执行能往工作区写
  任意多个二进制文件，无确认无留痕。被否。
- **只给下载链接、不落工作区**：零写围栏改动，但产物不参与预览/发布，且 scratch 现在跑完即删，
  要另造临时下载区 + TTL + 清理。被否。
- **老 .doc/.xls 只读（LibreOffice 转换）**：镜像 +几百 MB 到 1GB，构建期换源与时长上一个量级。被否。

## 后果

- **渗出面扩大，是明确接受的取舍**：公网 + 预装 curl + 只读工作区意味着源码外发比 ADR-0056 时
  更顺手（`curl -T` 一条命令）。`deploy/` 凭据、应用库/平台库凭据、docker.sock 仍不可触达，
  写工作区仍走确认围栏。
- **红线测试语义再扩容**：从「preset 白名单 + 沙箱豁免」到「preset + 沙箱内任意 argv」。新增工具
  仍须同步该测试与注册表，防漂移强度不低于从前。
- **CONTEXT 术语修订**：「Python 沙箱」的「只跑 Python，名实相符，不预留多语言」改为「除 Python 外
  也承载沙箱命令面，但不预留其他语言运行时」。
- **pip 装大包可能撞 120s 超时**：安装计时与超时上限留 spec 定默认值。
- **镜像体积与构建期换源复杂度上升**（预装库 + CLI），交付包构建时长相应变长。
- **ADR-0060 技能脚本的天花板抬高**：技能脚本不再被限定「只用纯标准库」，`build_python_prompt`
  里那句口径要同步改。