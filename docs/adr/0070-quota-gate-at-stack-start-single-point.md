# 0070 配额闸钉在服务层起栈单点，硬闸不追溯存量，管理员豁免

- 状态：Accepted
- 日期：2026-11-23
- 关联：ADR-0068（配额住 app_config 两级）、ADR-0067（管理员=创始人）、CONTEXT「账号配额」「实例」「治理台」

## 背景

启动上限要拦的是「起一套容器栈」这个动作。但入口不止 `prod/start`：`publish` 与 `rollback`
内部同样 `docker compose up -d` 起 prod 栈（`app/deploy/prod.py:133`），商店安装也直接调
`app_service.create_app_project`（`app/store/install.py:97`）。闸钉错位置等于给最重的动作留了后门。

## 决策

1. **判定是一处共用逻辑，接线在受理前的同步闸门**：七个入口——`dev/start`、`dev/restart`、
   `prod/start`、`prod/restart`、`publish`、`rollback`、`publish-requests/{id}/approve`（ADR-0050
   的批准即执行）——共用同一个纯判定函数（不依赖 `Request`）。拒绝发生在同步闸门阶段：
   `_dev_task`（四个 start/restart 共用）与 `_publish_gates`。**不放服务层起栈函数内部**：
   `engine.start_dev` / `prod.prod_start` 拿不到 actor，且会把拒绝埋进后台日志流，
   用户只能从预览日志看到失败。
2. **创建数按 `app_project` 行数计，商店安装天然计入**：安装产物就是一个 app_project 行，
   不另开「安装额度」第三把闸（分开计会重复计数）。
3. **硬闸只挡新增动作，不追溯存量**：管理员下调额度导致某人已超时，不自动停栈、不删任何东西，
   他只是在下一次启动前得自己降下来。避免「改一个数字，别人正在跑的活凭空消失」。
4. **管理员自己不受两把闸约束**：与 founder 既有的四处 owner 过滤 bypass 同族。治理页须标明
   「本额度不影响管理员」，否则那个读数骗人。

## 两条附带裁定

- **判定主体是应用所有者，不是当前操作者。** 被共享者发起发布、所有者批准即执行（ADR-0050），
  起的是所有者的栈、占的是他的资源；管理员豁免同样按所有者的 `is_founder` 判。
- **闸判「净增 running」而非「起栈」。** `restart_dev` / `prod_restart` 先 down 再 up，
  发布与回滚常在替换一套已 running 的 prod 栈；该应用该环境已是 running 时不占新增额度。
  否则刚好跑满额度的账号永远无法重启自己已有的任何东西——那是锁死，不是治理。

## 后果

- 全局端口池（约 94 对，不分账号）仍是比 per-账号 配额更硬的天花板：配额全开也可能有人撞
  `端口段已用尽`。两层闸并存，本期不去合并口径。
- `publish` 的失败路径多一类「启动数已达上限」，它发生在同步预检而非后台日志里，
  与既有预检契约同构：零 `deploy_hub` 事件、不落 `running` 的 `deploy_record`。
- 「起栈入口」的真实数量曾被低估（六 → 七）：漏掉的 `批准发布申请` 只有在读 ADR-0050
  之后才会想到去找。将来新增任何会 `compose up` 的路径，都必须同时接这道闸。
