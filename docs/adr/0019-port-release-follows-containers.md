# 0019 端口以容器为准：删除应用收口两个栈，平台启动清扫残留栈

线上演示服 2026-10-28 报 `Bind for 0.0.0.0:8213 failed: port is already allocated`，应用 finance 的
dev 栈永远起不来，而且**重试无效**——端口是建应用时一次分配、之后每次起栈原样复用（`engine.start_dev`
只读 `app_project.dev_port`），库里那个值不会自己挪窝。

真因是端口有两个事实来源而它们会分叉：`app_project` 的端口列只是**分配记录**，Docker 的端口发布才是
**占用事实**。两道本该拦住分叉的闸各自漏：

- `delete_app` 只删记录（M1 裁定：不删目录不删库），删除路径上的停栈闸门是 `status_dev in STOPPABLE`，
  而 `error` 不在里面——起栈半途失败（后端容器起来了、前端绑不上端口）恰好停在 error 且容器活着；
  prod 栈更是从来不在删除路径上收口。记录一删，UNIQUE 行消失，端口在库里「释放」，下一个应用被分到
  同一个端口，此后它每次起栈都撞。
- `allocate_pair` 的 TCP 探测写死 `127.0.0.1`，而平台跑在容器里时那是它自己的 loopback，宿主上已发布的
  应用端口全都「探起来空闲」——容器化部署下这道闸等于没有。同一个坑健康探针先踩过（ADR 之外那条
  `ZEROCODE_PREVIEW_PROBE_HOST`），当时只修了探针，没顺手管分配这一侧。

我们决定把**占用事实**提到优先于分配记录：

1. 删除应用无条件 `compose -p zc-<slug>-dev down` + `-prod down`，不看状态列，走新增的
   `engine.down_app_stacks`。
2. 平台启动的 `cleanup_orphans` 在原有「按库 down 全部 dev 栈」之后加一遍残留清扫：`docker ps` 按
   compose 项目标签取项目名，形状匹配 `zc-<slug>-(dev|prod)` 且该 slug 已不在 `app_project` 里的一律 down。
3. 分配端口的默认探针改连 `preview_probe_host()`，与健康探针共用同一个宿主地址。
4. 起栈失败时解析 daemon 原文里的 `Bind for ...:<port> failed`，用 `docker ps --filter publish=<port>`
   点名占用者，第一行直接给出可执行命令（dev 与 prod 三条起栈路径共用 `ports.explain_start_failure`）。
5. **删工作区**（`DELETE /workspaces/{id}`，应用记录消失的第二扇门——`app_project` 靠 FK CASCADE 跟走）
   与删应用同源收口，并补上原先完全没有的三道：发布中拒删（共用 `ensure_deletable`）、停栈、
   摘网关路由。那条应用查询与 `owner_user_id` 一起 join，是为了让他人的工作区照样 404 而不是 409——
   「发布中拒删」这句话等于承认那个 id 后面真有个应用。

被否决的方案：

- **给 app_project 加一列「端口是否仍被容器占用」**——那是两个事实来源之上再加第三个，而它照样会在
  平台崩溃与 Docker 失联时漂。
- **绑端口失败时自动换一对端口重试**——端口已经渲染进 `deploy/*.yml`、网关 `routes.json` 与预览地址，
  静默换号会让界面上的地址和实际听的对不上；更糟的是它把「库里已无主的容器」这个真问题藏起来，
  残留会一直攒。
- **删除时按 `deploy/*.yml` 走 `compose down`**——模板里密钥位带 `${VAR:?}`，那是 compose 的**文件解析期**
  闸门，`.env` 被清空时连 `down` 都 exit=1（`stop_dev` 正是为此要先补位）。删除路径补位毫无意义，
  按项目名 down 不吃文件。
- **只做删除收口、不做启动清扫**——缺陷存在期间每次删应用都可能留一个残留，线上此刻已经躺着若干个；
  要用户逐个 `docker rm -f` 才算修完，而平台自己看得见它们。

## 后果

- **删除应用从此会停掉它的 prod 栈**，这是语义变更：以前删记录后正式环境还在跑——一个没有主人的容器，
  网关路由已被 `gateway_routes.refresh` 摘掉，只剩 IP:端口直连可达。现在按「记录即所有权」收口。
- 停 prod 栈必须**同步摘网关路由**。`delete_app` 本来就 `gateway_routes.refresh`，工作区那条门原先不摘：
  在「只删记录、容器不动」的时代无所谓（域名照旧指向还活着的容器），现在栈停了，不摘就是把一个还能
  访问的域名变成 502。
- **已知不对称**：`DELETE /api/apps` 还有一道 `_reject_if_task_running`（该应用有部署任务在跑则 409），
  工作区那条门没有——它的后台起栈任务可能在 down 之后才把容器起来，那一对容器要等下次平台启动的清扫
  才走。
- 清扫按**名字形状**认平台栈，于是 `zc-<合法 slug>-dev|prod` 这个名字段被平台保留。用户自己
  `docker compose up -d` 一个恰好叫这个名字的项目，会在平台下次启动时被停掉。
- `down_app_stacks` 走 `-p` 而不读 yaml，因此不需要 `--remove-orphans`：compose 按项目标签移除全部容器，
  模板改过服务名留下的旧名容器也一起走。
- 探针改连 probe host 后，容器化部署下 `allocate_pair` 才真的会拒绝宿主已占用的端口。代价是裸 IP 被防火墙
  静默丢包时每个候选端口要等满 0.2s 超时（整段扫满是几十秒量级）；正常情形下拒连即回。
- 预览日志只渲染错误消息的**第一行 200 字**（`engine._step` 取 `str(e).splitlines()[0][:200]`），所以点名
  和 `docker rm -f` 必须写在第一行，daemon 原文一律附在 `\n原始输出:` 之后。改这条文案时别把它拆成两行。
- 状态列 `STOPPABLE` 随本次删除路径改造失去唯一引用，已删。它当初的假设——「状态列能代表 Docker 里
  有没有真容器」——正是这次要推翻的那个。
