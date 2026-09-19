# {{APP_NAME}}

zerocode 应用（小型 · Vue3 + FastAPI）。

- 本机访问（dev 预览）：http://localhost:{{DEV_PORT}}
- 数据库：dev = {{DEV_DB}}，prod = {{PROD_DB}}（由平台自动供给）
- 数据变更：一律写入 `sqlchange/V<日期>_<序号>_<说明>.sql`
- `deploy/` 目录由 zerocode 平台生成与管理，请勿手改
