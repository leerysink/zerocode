# 0047 vue3-node 栈选型：Express 5 + TypeScript + pg-promise，后端仍监听 8000

规格 02 §4.2/§4.3 原定 `vue3-node` 是「Express 4 + 裸 pg」，本 ADR 改为 **Express 5 + TypeScript + pg-promise**，
并让后端容器内监听 **8000**（与 `vue3-fastapi` 同形）而不是 Node 惯例的 3000。四条理由：

- **pg-promise 而不是 ORM**：平台把「结构真相 = `schema/schema.sql` 声明式快照、数据变更 = `sqlchange/` 三段」
  当成双轨闸门（ADR-0028/0040），由 `zerocode-sqlrunner` 一次性容器执行。Sequelize/Prisma 这类 ORM 自带
  migration/schema 推送能力，等于给应用第二条写库通路，且绕过 `schemadiff`/破坏性变更确认；pg-promise 保持
  SQL 是唯一出口，依赖树小、`node:20-alpine` 里无原生编译，且公开语料最多——AI 生成正确率是模板的第一优先级。
- **TypeScript 而不是纯 JS**：`run_check` 的定位是「AI 一个回合内自己发现类型/编译错误」（ADR-0036/0046），
  后端预设要落到 `tsc --noEmit` 才有意义。纯 JS 只能做逐文件 `node --check` 语法检查——列名写错、参数个数不对、
  引用未定义变量全都检不出来，那等于给新栈挂了一个看起来能自检、实际检不出东西的预设。pg-promise 自带类型声明，
  不额外欠 `@types` 债。
- **Express 5 而不是 4.19**：5.x 把 async handler 的 rejection 自动转给 error middleware。4.x 下 AI 漏写一处
  `try/catch` 就是未捕获 rejection → node 进程退出 → 预览容器反复重启，而 `JOINT_HEALTH_STACKS` 不含小栈，
  dev 就绪探针只看前端 `/`，用户看到的是一页「后端未就绪」和半行日志。这是生成式代码最常见的坏产物形态。
- **端口沿 8000**：`frontend/nginx.conf` 的 upstream、`vite.config.js` 的 proxy target、compose 的 `APP_PORT`
  就能与 `vue3-fastapi` 逐字节同形；`prod.py::_ensure_prod_proxy_conf` 是按栈反查反代模板的，端口分叉会逼出
  第二份 `nginx.conf`，还要担心给存量应用刷错 upstream。

## 备选方案

- **守 spec 原锁（Express 4.19 + 裸 `pg` + JS）**：文档一字不改、AI 语料最熟，代价是上面第二条的自检落空 +
  每条路由手写 try/catch，需要把它升级成契约硬规则。
- **Drizzle/Sequelize/Prisma**：开发体验最好。否决的真实理由是它与平台迁移闸门争夺写库权，除非契约里明令禁用
  migration 并排除相关目录，而这种「靠提示词约束模型不用一半的库」正是本仓库反复避开的那类围栏。
- **监听 3000**：符合社区直觉。代价见上，最终用一条注释与本文换掉两份栈分支模板。
- **路由通配**：Express 5 的 path-to-regexp 不再接受 `'*'`，通配要写 `/{*path}`。模板与契约里给死写法，
  否则 404 兜底路由会在生产报「Cannot GET」。

## 后果

- 规格 02 的 §1 表格、§4.2、§4.3、FR2-06 需按本文勘误（版本锁从 `express ^4.19` 改为 `express ^5`，
  并新增 `typescript` / `tsx` 到锁定清单）；「锁定」的语义照旧指不得降级替换。
- dev 栈的 backend 需要**自己的** `node_modules` 具名卷：它与管理端前端是两个独立的工程目录，共用一个卷会让
  两边依赖互相覆盖，也会让 `run_check` 的一次性容器读到另一半的包。
- Express 5 的 async 错误转发使后端崩溃率下降，但**不改变**小栈「不联合探后端」的口径：预览报就绪而后端半死
  这个已知局限继续由模板首页的 banner 与 AI 走查承担，`JOINT_HEALTH_STACKS` 本期只增不改。
- `static` 栈的「不铺数据轨」判据不在本文范围内（改主意只需撤掉 scaffold 的一条栈判据，不构成不可逆决策），
  记在 `CONTEXT.md` 的「铺数据轨 / 不铺数据轨」与本期规格里。
