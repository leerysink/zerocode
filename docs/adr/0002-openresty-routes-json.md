# 0002 网关用 OpenResty 动态路由，数据源是平台导出的 routes.json

网关要把 `{slug}.zerocode.com` 路由到该应用的 prod 宿主端口。原生 nginx 无法从主机名动态算出上游端口，只能靠配置或脚本。我们决定用 OpenResty，lua 从 Host 取出 slug 后查一张内存路由表；表的内容来自平台导出的 `routes.json`（`{slug: prod_port}`），以 volume 挂进网关容器，lua 按文件 mtime 判断是否重载。这样新应用上线只需平台改写这个文件，网关不重启、不 reload。被否决的方案：平台每次重新渲染 nginx conf 再 `nginx -s reload`（声明式但改配置面大、reload 有抖动）；lua 实时调平台 API 查端口（最实时，但网关的可用性被后端绑定，还要给网关配一套内部鉴权）。

## 后果

- 网关与平台的唯一契约是 routes.json 的格式，改格式要两侧同步。
- routes.json 只在发布成功、prod 启动、prod 停止、删除应用四个时机导出（改名不动 slug，故改名无需导出）。
- 后端挂掉时，已缓存在网关内存里的路由仍可转发；新上线的应用在后端恢复前访问不到。
- 技术栈引入 OpenResty/lua，仅限网关这一处，不扩散到应用模板。
