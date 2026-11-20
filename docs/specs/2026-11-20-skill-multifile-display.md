# Spec：技能多文件展示升级（skill multifile display）

日期：2026-11-20 ｜ 状态：待评审
前置共识：ADR-0004（技能账户级、母本对 Agent 只读）、ADR-0060（技能脚本经沙箱执行）、
ADR-0063（本 spec 的决策记录：只读详情抽屉 + 快照式安装预览 + 图片预览面）、ADR-0056（run_python 回执卡）
术语：见 `CONTEXT.md`「技能（skill）」——**技能元数据**（user_skill 表行）vs **技能母本**（磁盘多文件包）；
「技能详情」＝对母本的只读浏览视图
前置项目：账户级技能库（`docs/specs/2026-09-26-user-skills.md`，已交付）、技能脚本执行
（`docs/specs/2026-11-20-skill-script-execution.md`，已交付）

## 1. 问题陈述

技能早已是整目录多文件包（SKILL.md + references/assets + scripts，上限 600 文件/10MB，
`app/skills/library.py`），ADR-0060 又让 scripts 可经沙箱执行。但技能管理页
（`frontend/src/views/SkillsView.vue`）仍只展示一行元数据（名称/描述/来源/启用/删除），
后端 `list_skills` 只回 DB 行（`app/api/skills.py` `_COLUMNS`），**没有任何文件清单/内容接口**。

用户看不见技能内部有哪些文件/脚本，无法在安装后、执行前审视自己装了什么（尤其第三方
GitHub 技能带可执行脚本）。展示要跟上「技能≠单个 readme」的事实，补多文件化后的透明度/可审视性。

## 2. 方案概述

四面升级（ADR-0063），全部**只读**、不开管理页写面：

- **A 列表增强**：`list_skills` 补聚合字段（文件数/总大小/含脚本标记）；前端名称单元格下
  加副信息「N 文件 · X KB · 含脚本」，不新增列。
- **B 技能详情**：列表行「详情」开右侧抽屉，左文件树右内容预览（SKILL.md 渲染、scripts 源码、
  references 文本、图片内联预览）。后端新增列目录/读文本/读图片三接口。
- **C 安装前预览**：URL/GitHub 通道改两段式——`preview` 拉取整包暂存为快照（token→包），
  回文件清单+内容供审视；`install` 从快照落盘（preview==install）。与 AI 草稿同构人在环。
- **D 对话回执**：`RunPythonCard` 在 skill 模式（结果带 `script` 字段）区分「技能脚本」标题+脚本名。

后端文件接口复用技能受控读面围栏（`resolve_skill_path` 语义）+ owner 隔离（`request.state.actor`），
越权 404，不开第二读面。

## 3. 用户故事

### A 列表增强
1. 作为用户，技能列表每行名称下显示副信息「N 文件 · X KB · 含脚本」（含脚本＝母本内有 `.py`），
   一眼看出技能不是单文件、哪些带可执行脚本；不新增表格列。
2. 作为用户，`GET /api/skills` 每行附 `file_count`/`total_bytes`/`has_scripts` 聚合字段，
   由扫母本目录算出；母本缺失（残行）时聚合为 0/false 不报错。

### B 技能详情（只读抽屉）
3. 作为用户，我点列表行「详情」开右侧抽屉，左侧文件树列出母本全部文件（相对路径+大小+类型），
   不离开列表页。
4. 作为用户，我点文件树里的文本文件（.md/.txt/.json/.csv/.yaml/.yml/.py），右侧预览其内容；
   SKILL.md 以渲染态展示，其余文本以源码态展示。
5. 作为用户，我点图片文件（png/jpg/jpeg/webp/gif），右侧内联预览图片；其它二进制只显示
   名/大小/类型，不预览不下载。
6. 作为用户，详情抽屉只读——没有编辑/删除单文件的入口（母本对平台展示侧同样只读）。
7. 作为用户 A，我永远看不到用户 B 的技能文件：列目录/读文本/读图片均按 actor 归属过滤，
   越权返 404（沿用登录项目裁定，不区分 403）。
8. 作为平台，文件接口路径收敛到该用户 `skills_root` 内，相对路径逃逸/越界一律拒，
   复用 `resolve_skill_path` 围栏语义，不开第二读面。

### C 安装前预览（两段式快照）
9. 作为用户，我粘贴 URL/GitHub 链接点「预览」，系统拉取整包但**不落地**，回文件清单+
   SKILL.md 内容（+脚本源码）供我审视，再决定装不装。
10. 作为用户，GitHub 仓库多命中时预览仍回候选清单（`candidates`），我带 subpath 二次预览。
11. 作为用户，我确认安装后，系统从**预览快照**落盘落库——装的就是我预览的那一份
    （preview==install），不重新拉取，防 upstream 变化/调包。
12. 作为用户，预览快照有过期时间（TTL），过期后确认安装得到明确报错（请重新预览），
    不静默重新拉取。
13. 作为平台，快照按 owner 隔离（token 绑定 uid），体积沿现有单文件 1MB/整包 10MB/600 文件
    上限；超限预览即拒。
14. 作为用户，AI 生成通道仍是草稿两段式（generate→adopt），语义不变；其草稿预览对齐展示
    文件清单（AI 产物当前为单 SKILL.md）。

### D 对话脚本回执
15. 作为用户，Agent 执行技能脚本后，RunPythonCard 标题显示「技能脚本已执行」（非零退出同理），
    并展示脚本相对路径（结果 `script` 字段，ADR-0060 已回）；自由代码模式标题不变。

### 后端围栏与口径
16. 作为平台，读文本沿 Agent 受控读面文本白名单（`SKILL_READ_EXTS`）；读图片经独立类型围栏
    （png/jpg/jpeg/webp/gif），其它后缀拒；列目录递归但跳过越界/符号链接逃逸。
17. 作为平台，所有文件接口只读，不提供任何写/删单文件端点。

## 4. 已定决策（ADR-0063，避免翻案）

| 维度 | 决策 |
|---|---|
| 范围 | A+B+C+D 四面全做 |
| 深度 | 结构+可读内容，只读不开写面 |
| B 形态 | 右侧抽屉（左文件树右内容），非独立页/模态 |
| C 形态 | 两段式 preview→install，快照一致性（preview==install） |
| 二进制 | 图片可预览（类型围栏），其它仅元数据；文本沿白名单 |
| A 呈现 | 名称下副信息，不新增列 |
| D 呈现 | RunPythonCard 区分技能脚本标题+脚本名 |
| 后端围栏 | owner 隔离 + 复用 resolve_skill_path，越权 404，不开第二读面 |
| 快照暂存 | disk staging + TTL + owner-scoped token（实现期定具体目录/清理） |

## 5. 接缝与测试决策

**最高接缝＝HTTP API（/api/skills/*）与前端 SkillsView**，优先复用既有接缝：

- **后端 API 接缝**（`tests/test_skills_api.py` 风格，app client）：
  - 文件接口：列目录/读文本/读图片的 owner 隔离（A 看 B 的 404）、路径围栏（逃逸/越界拒）、
    文本白名单、图片类型围栏、其它二进制拒。
  - 预览快照：preview 不落地（母本目录无新增）、preview==install（装的内容==预览快照）、
    token 过期报错、candidates 二次预览、owner 隔离 token、体积超限拒。
  - list 聚合：file_count/total_bytes/has_scripts 正确，残行母本缺失降级 0/false。
  - **守卫接线用变异测试钉住**（owner 过滤/围栏纯函数有测试≠被调用）。
- **前端 vitest 接缝**（VTU，`SkillsView.test.js` + 新组件测试）：
  - 列表副信息渲染（N 文件/体积/含脚本标记）；详情抽屉文件树+文本/图片预览+只读（无写入口）；
    安装预览态（预览→确认→install 带 token）；RunPythonCard skill 模式标题。
  - 本仓库 VTU 坑沿记忆：二次 mount、共享监听器 autoUnmount、桩按 id、happy-dom 不规范化 CSS。
- **完成定义**：新增测试全绿 + 受影响既有测试回归绿 + 前端冒烟。
- **环境约束（诚实口径）**：后端 API 测试启动 app lifespan，会触发 `stop_all_dev_on_exit`
  扫掉宿主 live `zc-*-dev` 栈，且共享 `zerocode_test` 库须**串行**（并行会话争用会挂起/死锁）。
  全量 pytest 约 27 分钟。实现期先跑不碰 DB 的单元/前端测试；API/全量待并行会话跑完、
  dev 栈可安全 down 时补，或以旧绿+导入/构建为据并说明。

## 6. 明确不做

- 管理页编辑/删除单文件（写面）——母本只读口径不开。
- 非图片二进制的预览/下载。
- 技能版本检测/一键更新、技能市场、跨用户共享/发布（沿 user-skills 范围外）。
- 子 agent 展示面改动。
- `<available_skills>` 索引块改动（ADR-0060 已更新脚本执行说明）。
- AI generate/adopt 语义改动（已是两段式，仅对齐预览展示）。
- 图片以外的 assets 渲染（如 PDF/office 内联预览）。

## 7. 补充说明

- **快照暂存机制**：preview 把取回的 `payload.files`（relpath→bytes）暂存到 data 区 staging
  目录（token 键，绑定 uid），TTL 过期清理（访问时惰性 sweep + 可选周期）；install 凭 token
  读快照→`_persist_skill` 落盘落库→消费后清理。token 不可跨 owner 使用。具体目录/清理周期
  实现期定，约束：体积沿 10MB/600、不入库、不留永久孤儿。
- **图片类型围栏集合**：png/jpg/jpeg/webp/gif（与前端 `<img>` 可渲染对齐）；Content-Type 按
  后缀映射，nosniff；只读流出，owner 隔离。
- **聚合字段口径**：`file_count`＝母本文件总数；`total_bytes`＝总字节；`has_scripts`＝母本内
  存在 `.py`（与 ADR-0060 可执行面一致）。list 时扫目录计算（库小，IO 可接受）；是否落库缓存
  实现期视性能定，默认实时扫。
- **与 ADR-0060 衔接**：run_python skill 模式结果已回 `script` 字段，D 直接消费，无需后端改动。
- **安装流改造影响**：`installSkill({url})` 前端调用改为 `previewSkill({url})`→`installSkill({token})`；
  后端 install 端点兼容 token 入参（url 入参是否保留直装由实现期定，默认改两段式、保留 candidates
  在 preview 阶段）。