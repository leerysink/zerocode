# {{APP_NAME}}

zerocode 应用（小型 · 纯前端静态页）。

- 本机访问（dev 预览）：http://localhost:{{DEV_PORT}}
- 发布后访问：由平台网关按 `{{PROD_PORT}}` 提供，整站是一个 nginx 静态站点

## 本栈的能力边界（先读这段）

**没有后端、没有数据库、没有迁移。** 页面跑在浏览器里，平台构建的只有一个 nginx 静态站点镜像。

- 数据只能落在**浏览器本机**：`localStorage`（模板首页就是这么做的）、`IndexedDB`，或导出成文件。
- 刷新页面、换浏览器、换设备、清缓存——数据就没了。这是栈的物理限制，不是可以修 bug 修掉的。
- 因此产物里**没有** `backend/`、`schema/`、`sqlchange/` 三个目录，平台也不会给本栈应用补首轮种子。
  平台仍然为应用建了库（`{{DEV_DB}}` / `{{PROD_DB}}`）并发了凭据，但纯前端够不着它，别去连。
- 需要服务端存储、跨设备同步、登录或多用户协作时，改用 `vue3-fastapi` / `vue3-node` / `vue3-springboot`，
  而不是在这里手写一个假的后端。
- 纯前端应用**暂不支持上架应用商店**（商店要求有后端镜像与 prod 迁移历史）。

## 目录

- `frontend/` —— Vue3 + ant-design-vue（Vite）。dev 起 vite，发布时先 `vite build` 再把 `dist` 烘进 nginx 镜像。
  - `src/App.vue` —— 首页与 localStorage 读写示例（AI 从这里起步改造）。
  - `nginx.conf` —— 生产站点配置。**不要**加 `/api` 反代：本栈没有可指向的 upstream，
    nginx 启动期解析不到主机名会直接起不来。
- `app.json` —— 平台读的元数据（端口、栈、健康路径），勿手改。
- `deploy/` —— 由 zerocode 平台生成与管理，勿手改。

## 变更纪律

- 依赖只在 `frontend/package.json` 上追加，基线版本（vue 3.4 / vite 5 / ant-design-vue 4 / pinia 2）不降级不替换。
- `npm run build` 必须零错误：发布镜像的最后一步就是它，构建失败 = 发布失败。
- 想加后端能力时，先停手跟用户确认要不要换栈——换栈是重新建应用，不是在原地加目录。
