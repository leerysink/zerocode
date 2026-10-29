# 0023 工作台编辑引擎选 Monaco，与 SQL 编辑器的 CodeMirror 并存

工作台的代码编辑用 **Monaco Editor**，不沿用仓库已依赖的 CodeMirror 6。两者并存：DB 查询控制台的 SQL 编辑器继续用 CodeMirror，工作台不迁移。

理由：工作台的目标观感是 VSCode 本身，Monaco 是 VSCode 的编辑器内核，语法高亮、括号匹配、多光标、minimap、查找替换与 VSCode 一致，不需要自己拼装；CodeMirror 6 虽然已用于 SQL、包体更小，但默认观感与 VSCode 有明显差距，要达到同等体验需要持续拼装扩展与主题，反而更难维护「像 VSCode」这个唯一要求。

被否决的方案：CodeMirror 6（包体小、零新依赖，但观感不达预期）；两个编辑器都上（维护两套）；textarea 换皮（无语法高亮，不成立）。

## 后果

- 新增 `monaco-editor` 依赖与 Vite worker 配置；工作台路由按需加载，控制台主包不背 Monaco 体积。
- 两套编辑器并存会在仓库里形成「SQL 用 CodeMirror、代码用 Monaco」的口径；新代码要按场景选，不强行统一。
- Vitest/happy-dom 下 Monaco 无法真实运行，组件测试需桩掉编辑器，只测页签/保存/冲突等逻辑。
