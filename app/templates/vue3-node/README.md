# {{APP_NAME}}

zerocode 应用（小型 · Vue3 + Express 5 + TypeScript）。

- 本机访问（dev 预览）：http://localhost:{{DEV_PORT}}
- 数据库：dev = {{DEV_DB}}，prod = {{PROD_DB}}（由平台自动供给，连接串走环境变量 `DATABASE_URL`，代码里不写口令）

## 目录

- `backend/` —— Express 5 + TypeScript 服务：`src/index.ts`（入口 + health + 错误出口）、
  `src/db.ts`（pg-promise 连接）、`src/routes/`（业务路由）。dev 由 `npm run dev`（tsx watch）起，
  发布镜像先 `tsc` 再跑 `node dist/index.js`，容器内监听 8000。
- `frontend/` —— Vue3 + ant-design-vue（Vite）。dev 代理 `/api → http://backend:8000`，
  发布后由 `nginx.conf` 反代同一个 upstream。
- `schema/schema.sql` —— 表结构的**唯一真相**（声明式、幂等、可重放）。
- `sqlchange/` —— 数据轨（仅种子/回填/刷值）：顶层公共段 + `dev/` + `prod/` 两个环境段。

## 本栈的两条写法红线

1. SQL 一律用 `$1`、`$2` 占位符参数化（pg-promise 原生支持），**不做字符串拼接**。
2. 通配路由写 Express 5 的 `/{*path}` 形态；写旧版 `'*'` 会在启动期抛 PathError。

## 变更纪律

- 改表结构 = 编辑 `schema/schema.sql` 的目标态；补列的写法红线见 `schema/README.md`。
- 数据变更写 `sqlchange/D<日期>_<序号>_<说明>.sql`，**只追加**：已应用到库的文件不得修改也不得删除
  （平台按相对路径 + 校验和记账，改了会被拒启动预览）。
- 需要改列类型/拆表/删列时**分两个版本两步走**：第一版加新列并双写，第二版迁数据后删旧列。
- 上架到应用商店后，本应用的迁移序列就是其他实例的升级基线，破坏性变更未走两步会让订阅实例无法升级。
- `deploy/` 目录由 zerocode 平台生成与管理，请勿手改。
