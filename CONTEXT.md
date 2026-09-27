# CONTEXT — 领域术语表

项目内统一术语，拷问对齐时敲定，实现与文档必须使用这些规范词。

## 平台网关（gateway）

独立的 OpenResty 容器，仓库顶层 `gateway/` 目录编排，人工 `docker compose up -d` 长驻。
职责：终止 TLS（自签通配证书）+ 按应用域名动态路由到各应用 prod 端口。
平台后端不管它的启停，只喂它路由数据。应用容器本身不感知 HTTPS。

## 应用域名（app domain）

`{slug}.zerocode.com`。slug 是应用的稳定标识——改名（rename_app）不动 slug，
所以域名一经发布永不漂移。通配证书只能匹配**一级**（`*.zerocode.com` 不匹配
`a.b.zerocode.com`），slug 为单段字符串，恰好吻合此约束。

## routes.json（网关路由表）

平台导出的 `{slug: prod_port}` 映射文件，volume 挂进网关容器；OpenResty 的 lua
按文件 mtime 缓存刷新。导出时机：发布成功、prod 启动、prod 停止、删除应用。
它是网关与平台之间唯一的契约——网关不依赖平台 API 存活。

## 自签通配证书

openssl 脚本（仓库内 make-cert）生成的本地 CA + `*.zerocode.com` 证书，
落在 `gateway/certs/`（gitignore 排除，不入库）。浏览器需手动信任该 CA。
上服务器时重跑脚本、换真 DNS 解析即可。

## miss（路由未命中）

请求的 slug 不在 routes.json 中（应用未发布/已停/已删）→ 网关返回 404 提示页，
告知用户去控制台启动应用。不重定向、不区分 503。

## 技能（skill）

给**平台 Agent** 用的指令扩展包，遵循 Anthropic Agent Skills 开放标准
（SKILL.md + YAML frontmatter），可从 GitHub 仓库/URL 安装。Agent 在工作区干活时
自动遵循已启用技能。它不是生成应用的功能素材——应用打包/运行时不感知技能文件，
技能只活在平台 Agent 的上下文里。技能归属**用户**（账户级技能库），依赖登录体系，
顺序上先做登录/用户体系改造、后做技能功能。

## 创始人（founder）

第一个注册的账号（`actor.is_founder`），注册同事务承接全部无主存量数据。
职责边界：平台内唯一的**商店审批人**——商店安装申请只由创始人审批，
版本发布者不再有审批权（集中管控裁定）。审批人跟着角色走而非写死昵称，
生产环境首注册者天然成为审批人。

## 控制台（console）

zerocode 平台自身的管理界面。**不走网关**，继续以
http://localhost:端口 直连访问。网关只服务应用域名。
