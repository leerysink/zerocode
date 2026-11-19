# Spec：技能脚本执行最小切片（skill script execution）

日期：2026-11-20 ｜ 状态：待评审
前置共识：ADR-0004（技能账户级、渐进披露）、ADR-0056（run_python 隔离沙箱）、ADR-0046（红线
「无通用命令执行面，只允许 preset + 隔离 Python 沙箱」）、ADR-0052（子 run 只读白名单）、
ADR-0027（不可回滚操作恒确认先例）
术语：见 `CONTEXT.md`（技能 skill、母本、沙箱、确认卡）
前置项目：账户级技能库（`docs/specs/2026-09-26-user-skills.md`，已交付）、run_python 沙箱
（ADR-0056，已交付）

## 1. 问题陈述

技能 v1 刻意只消费「指令性内容」：SKILL.md 与 references/assets 文本可读，`scripts/` 子树拒读、
技能内脚本不可执行（user-skills spec §3 故事 11、§4 口径表「脚本执行」、§7 范围外）。当时的前提
是「Agent 无 shell 工具」，拒读是「无执行能力」的配套双保险。

ADR-0056 落地后前提已变：平台有了隔离 Python 沙箱 `run_python`（一次性容器、工作区只读挂载、
写只进 scratch、非 root、限资源、当前 `--network none` 离线）。其它平台的技能普遍带 Python 等
可执行脚本，本 spec 把技能脚本接进现有沙箱，开「技能脚本执行」最小切片：依赖先限纯标准库，
网络出口加宽（ADR-0056 预留的 005）不在本票。

## 2. 方案概述

**扩展 `run_python` 为双模工具**，不另立新工具：

- **code 模式**（现状不变）：`code` 源码字符串，`python -c code`。
- **skill 模式**（新增）：`skill_dir`（`<available_skills>` 索引给的绝对路径）+ `script`
  （技能目录内相对路径的 `.py`）+ 可选 `argv`（字符串数组）。把该技能目录**只读**挂载进容器
  `/skill`，执行 `python /skill/<script> [argv...]`，跑的是母本原件。

沙箱安全口径（离线、非 root、限资源、工作区只读、deploy 遮蔽、scratch 可写、产物扫描）全部沿用，
仅追加一条只读挂载。确认沿 `CONFIRM_WRITE` 档（受 `agent.write_confirm` 开关控制，与文件写入同级）。
读面放开 `.py`（含 `scripts/`），模型可先读源码再执行、出错对照修。前端零改动（ConfirmCard 通用
args JSON 已能展示新参数，RunPythonCard 只渲染结果）。子 agent 不开放（白名单不含 `run_python`）。

## 3. 用户故事

### 执行
1. 作为 Agent，SKILL.md 指明跑某个脚本时，我用 `run_python(skill_dir=..., script="scripts/run.py",
   argv=[...])` 执行；技能目录只读挂进沙箱 `/skill`，跑的是母本原件，脚本改不了技能本身。
2. 作为 Agent，脚本的退出码、stdout+stderr 尾部、scratch 产物口径与 code 模式完全一致；文本产物
   经 `write_file` 落盘工作区，二进制/超限只回元数据。
3. 作为 Agent，脚本异常是正常结果（`ok=True` + 非零 `exit_code` + traceback），我据 output 自修；
   只有平台侧故障（docker 不可用/超时/取消）才 `ok=False`。
4. 作为 Agent，沙箱离线无网络、无库凭据：脚本里 import 第三方库会 ImportError，这是正常结果；
   本切片技能脚本应只用纯标准库（SKILL.md 自行声明）。

### 读取与调试
5. 作为 Agent，我能用 `read_file` 传技能目录内绝对路径读 `.py` 源码（含 `scripts/` 子树），先理解
   再决定跑不跑、出错对照源码修；200KB 上限、二进制拒绝、越界围栏不变。
6. 作为用户，技能母本对 Agent 仍**只读**：写类工具（write/edit/delete/move）不开放技能目录。

### 确认与门禁
7. 作为用户，执行技能脚本前弹确认卡（受 `agent.write_confirm` 开关控制，与文件写入/现有 run_python
   同级），卡上可见 `skill_dir/script/argv`；拒绝则不执行。
8. 作为用户，澄清模式收口前技能脚本执行同样被 clarify 门禁挡住（与写工具一致，门禁在确认之前）。

### 围栏
9. 作为 Agent，`code` 与 `skill_dir/script` 互斥：混用、都缺、skill 模式缺其一，都得到明确报错。
10. 作为平台，`skill_dir` 越出当前用户技能根（`data/users/<uid>/skills`）或不是目录、`script`
    是绝对路径/逃逸出 skill_dir/后缀非 `.py`/不是文件，一律拒，**不开第二读面**（复用
    `resolve_skill_path`）。
11. 作为平台，`argv` 必须是字符串数组（≤32 个、单个 ≤2000 字符）；以 docker exec 形直接传 argv，
    无 shell 拼接，无命令注入面。
12. 作为用户，子 agent（delegate）不能执行技能脚本：`SUBRUN_TOOL_NAMES` 不含 `run_python`，
    沿用 ADR-0052 只读语义，本票不改白名单。

## 4. 口径表

| 维度 | 口径 |
|---|---|
| 执行入口 | 扩展 `run_python` 双模（code / skill_dir+script+argv），不立新工具 |
| 互斥 | `code` 与 `skill_dir/script` 二选一；schema required 清空，互斥由 handler 判 |
| 挂载 | 单技能目录只读挂 `/skill:ro`（不挂整个技能根，避免跨技能读面）；工作区 `/workspace:ro`、deploy 遮蔽、`/scratch` 可写不变 |
| 执行命令 | `python /skill/<script> [argv...]`，工作目录仍 `/workspace` |
| 确认 | `CONFIRM_WRITE`（受 `agent.write_confirm` 控制），clarify 门禁在前；零新增配置 |
| 读面 | `SKILL_READ_EXTS` 加 `.py`；删 `is_skill_scripts` 守卫（`scripts/` 不再拒读）；二进制/越界/200KB/写面不开不变 |
| 网络/依赖 | 当前 `--network none` 离线（ADR-0056 的 005 网络出口未落地）；纯标准库，第三方依赖范围外 |
| 资源/超时 | 沿用：`python:3.12-slim`、非 root 1000、1g/1cpu/256pids、120s |
| 产物 | 沿用 `_scan_artifacts`：文本 under cap 回全文，二进制/超限回元数据 |
| 结果 | 沿用 ok/exit_code/output/artifacts；skill 模式多回 `script` 字段供留痕 |
| 子 run | 不开放（白名单不含 run_python） |
| 前端 | 零改动（ConfirmCard 通用 args JSON + 已有 run_python 文案；RunPythonCard 只渲染结果） |
| 挂载权限 | 容器 uid 1000 须可读技能目录；Linux 上实现期验证，必要时挂载前 `a+rX`（只读挂载仍挡写） |

## 5. 接缝

- **schema**：`app/agent/registry.py` 的 `_RUN_PYTHON`——`code` 改可选，新增 `skill_dir/script/argv`，
  required 由 `["code"]` 改 `[]`，description 写明双模与互斥。
- **handler**：`app/agent/python_tools.py::run_python_tool`——模式判定 + 围栏（复用
  `app/agent/tools.py::resolve_skill_path`）+ docker args 追加 `-v <skill_dir>:/skill:ro` 与
  `python /skill/<script> *argv`；code 分支不动。
- **读面**：`app/agent/tools.py`——`SKILL_READ_EXTS` 加 `.py`；`_handle_read_file` 去掉
  `is_skill_scripts` 拦截；`is_skill_scripts` 函数删除（生产侧仅 tools.py:334 一处消费；
  `test_fs_tools.py` 对它的直接单测随函数一并删除/改写）。
- **prompt**：`app/agent/prompts.py::_render_available_skills` 规则补脚本执行说明；
  `app/agent/python_tools.py::build_python_prompt` 补 skill 模式段。
- **确认/门禁/子 run**：`loop.py` 确认链与 `SUBRUN_TOOL_NAMES` 均不改（策略读注册表，自动沿用）。

## 6. 测试

- `tests/test_python_tools.py`：模式互斥/成对校验；围栏矩阵（skill_dir 越界、script 绝对/逃逸/
  非 `.py`/不存在、argv 超限/非字符串）；monkeypatch `engine.docker` 断言 `/skill:ro` 挂载与
  `python /skill/<script> argv` 命令；code 模式回归；产物扫描不变。
- `tests/test_fs_tools.py`：story-11 钉改写——`scripts/.py` 可读、`.py` 进白名单、二进制/越界仍拒、
  写面仍不开。
- schema 钉：`run_python` required 改 `[]` 的相关断言（`test_llm` 等工具清单钉）同步。
- prompt 钉：`<available_skills>`/`<python_sandbox>` 新文案断言。
- 守卫接线用变异测试钉住（纯函数有测试 ≠ 被调用）。
- 全量 pytest **串行**（共享 `zerocode_test` 库，并发 TRUNCATE 死锁）；前端 vitest `--maxWorkers=4`。

## 7. 范围外

- 第三方依赖 / pip install / 预烘依赖镜像（沙箱离线，待 ADR-0056 的 005 网络出口加宽后再议）。
- frontmatter 高级字段消费（`allowed-tools` 等）。
- 独立新工具 `run_skill_script`（本票取扩展，见 ADR-0060 备选）。
- 前端回执卡区分「技能脚本」标题的抛光（最小切片前端零改动）。
- 子 agent 开放技能脚本执行。
- 技能市场 / 版本更新 / 跨用户共享（沿 user-skills spec 范围外）。

## 8. 取代关系

本 spec 取代 `docs/specs/2026-09-26-user-skills.md` 的以下口径：
- §3 故事 11「读 `scripts/` 子目录……一律被工具层拒绝」→ 改为 `.py` 可读（故事 5/6）。
- §4 口径表「脚本执行：v1 只消费指令性内容……技能内脚本不可执行」→ 改为经 run_python skill 模式执行。
- §7 范围外「脚本执行」条目移出范围外。
其余（账户级隔离、母本只读、渐进披露、安装通道、尺寸守卫）不变。