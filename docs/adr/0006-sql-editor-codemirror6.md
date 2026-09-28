# 0006 DB 操作台的 SQL 编辑器内核选 CodeMirror 6

查询控制台要做到语法高亮、格式化、多页签、选区执行，`a-textarea` 这条路走不通。我们决定引入 CodeMirror 6（`codemirror` + `@codemirror/lang-sql`）作为编辑器内核，`sql-formatter`（PostgreSQL 方言）做格式化，二者只在 `frontend/` 增加依赖。

被否决的方案：textarea 背后叠一层 highlight.js 只读着色层（零新依赖，但滚动与换行对齐脆弱，拿不到可靠选区，多页签和格式化仍要自己搭，属于一定被推翻的中间态）；Monaco Editor（体验最接近 VSCode，但产物 3~5MB 且要配 Web Worker，对一个管控台页面过重）。CodeMirror 6 的体积适中、原生支持多文档（页签）与选区语义，且我们把**语句拆分做成了与编辑器无关的纯函数**，未来即使换内核，拆分逻辑和执行契约都不用动。

## 后果

- 现有 `DbAdminView.test.js` 里 `.zc-db-sql-input` 的 `setValue` 打法失效，测试需改为对编辑器模型/包装组件注入文本。
- 高亮着色由 CodeMirror theme 承担，需与 antd 的 `colorPrimary: #5b7a9d` 视觉基调对齐，避免管控台出现两套配色。
- 表名/列名自动补全复用已有的 `dbTables` / `dbTableSchema` 数据，不新增后端接口。
