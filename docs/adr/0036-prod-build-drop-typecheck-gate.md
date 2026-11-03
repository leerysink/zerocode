# 0036 prod 构建摘掉 vue-tsc 类型闸门

演示机上大型应用「童装衣服」发布死在第 2 步：`npm run build` = `vue-tsc -b && vite build`，类型检查
先跑、退出码 2，于是 `vite build` 根本没开始，整次镜像构建作废。报的 20 条里绝大多数是标注偏差——
本地行数组被标成了请求 payload 的类型（`{skuId, quantity, unitPrice?, remark?}`），运行时字段其实在，
`stockQty` / `productName` 取得到值，只是 TS 不认。而预览一切正常：dev 脚本是裸 `vite`，全程不做类型
检查。也就是说这条链上**类型检查只住在一个位置**，恰好是整条链最贵、且用户最没有耐心的那一步。

更不对称的是栈间口径：`vue3-fastapi` 模板的 prod build 一直是 `vite build`，只有 `vue3-springboot`
模板带 `vue-tsc -b`。所以「发布被类型错误挡住」这一整类失败只可能发生在大型应用上，而大型应用恰恰是
AI 一次生成几十个页面、标注噪声最多的那一类。

我们决定把这道闸门从构建期摘掉，与 fastapi 栈对齐：模板的 build 脚本改为 `vite build`；存量应用由发布
preflight 的 `ensure_prod_build_script` 幂等摘掉，与 `ensure_prod_build_mirror`、`_wire_dockerfile_proxy`
同一条保守纪律——只认逐字命中模板的那一行，应用自己改过的构建脚本（哪怕只多一个 flag）原样放过。
`vue-tsc` 留在 devDependencies 里，想自查随时 `npx vue-tsc -b`。

被否决的方案：把类型检查前移到 preflight 并阻断（省一次几分钟的构建，但把「AI 生成的代码有类型噪声」
变成用户非修不可的发布门槛，闸门只会逼人反复点重试）；前移但不阻断、只警告（要新增事件类型、UI 展示
与一键送 AI，改动面比这道闸门本身大得多）；在服务器手改应用的 `frontend/package.json`（改的是用户工程
里的文件，仓库不留痕，正是 ADR-0025 否过的那个形态）；让 AI 把这 20 处修干净再发（治得了这一个应用，
治不了下一个，且把真 bug 与标注噪声混进同一条队列）。

## 后果

- **prod 出包不再保证类型过**。类型正确性从「构建期强制」降级为「开发期自查」：`vite build` 仍会因
  语法错、导入解析失败而红，但类型不匹配会变成运行时的 `undefined` 字段而不是构建失败。这是本次决策
  真正买下的代价，别把它描述成「只是去掉了个检查」。
- 闸门确实抓到过真 bug：同一批报错里 `'"quarter"' is not assignable to OpUnitType` 是有效的——dayjs
  未 `extend(quarter)` 时 `startOf('quarter')` 不抛错，实测原样返回当天，季度报表的区间静默算错。
  但它同样不阻断打包（转译后照样跑），所以它的正确归属是「AI 自查 + 预览走查」，不是「发布时炸一次
  几分钟的构建」。摘闸门不等于这类问题不该修。
- `tsconfig.node.json` 里「不许关 emit」的注释与 `.dockerignore` 兜 `vite.config.js` 那两行**保留**：
  只要还有人跑 `npx vue-tsc -b`，tsc 产物遮蔽 vite 配置那条坑就仍然活着（评审 A5 I-2 的结论没作废，
  只是不再是构建必经路径）。`tests/test_scaffold.py` 那条断言同理继续钉。
- 存量半边写的是**用户工程里的文件**，与应用自己的编辑存在竞争：判据是「那一行逐字命中模板」，改过就
  不再过手，不覆盖。回填无条件执行，所以新应用从模板起就对、老应用等下一次发布自动刷正。
- 生效需要**重出包**：模板住在平台镜像里，回填函数也住在平台镜像里，两条都跟着包走；服务器上手改只能
  救一个应用一次发布。
- 构建时长随之下降，但幅度不由本 ADR 声称：大型栈 prod 构建的耗时大头是 Maven 冷下载（ADR-0025 记过
  实测），`deploy.build_timeout_large` 的 1800s 不因此次改动下调。
