# 聊天图片输入与识别（chat image input）规格说明

**日期**：2026-10-29  **来源**：需求访谈  **状态**：已实现
**相关文件**：[ChatInput.vue](../../frontend/src/components/ChatInput.vue)、[chat.js](../../frontend/src/stores/chat.js)、[ws.py](../../app/api/ws.py)、[loop.py](../../app/agent/loop.py)、[model_resolver.py](../../app/agent/model_resolver.py)

## 1. 问题陈述

聊天页现在只认文字。用户把截图按 Ctrl+V，浏览器不会把图片交给输入框；即使绕过前端把图片塞进来，
整条链路也会把它丢掉：

- [ChatInput.vue](../../frontend/src/components/ChatInput.vue) 只有 `a-textarea`，没有 paste / drop / 选择文件入口；
- [chat.js](../../frontend/src/stores/chat.js) 的 `send(text)` 会 `trim()` 后拒绝空文本，也没有附件概念；
- [ws.py](../../app/api/ws.py) 收到空 `content` 直接 `continue`，纯图片消息在第一跳就被丢弃；
- `message` 表没有附件列，[message_service.py](../../app/services/message_service.py) 只落文本；
- [loop.py](../../app/agent/loop.py) 的 `_history` 把所有消息压成字符串，模型只收得到文字；
- `model_config` 没有「是否支持图片输入」的能力位，默认模型 `deepseek-chat` 本身也是纯文本模型；
- [compaction_service.py](../../app/services/compaction_service.py) 的压缩摘要只读文本。

所以这不是「加一个上传按钮」的单点改动，而是**从输入框到模型请求的一条多模态链路**。本规格把它一次
打通，并明确哪些边界刻意不做。

## 2. 方案概述

- **前端负责取图、压缩、上传**：Ctrl+V / 拖拽 / 文件选择三条入口都汇入同一个附件队列；图片在浏览器里
  用 canvas 重编码（最长边 2048、目标 1.5MB、去 EXIF），然后**立即上传**，拿到附件 ID。
- **WS 只传 ID**：`user_message` 带 `attachment_ids` 与 `client_msg_id`，不把 base64 塞进 WebSocket 帧。
- **平台数据区存文件**：落在 `data/users/{uid}/chat_uploads/{session_id}/`，**不进用户工作区**——避免污染
  文件树、应用打包和 `source_hash`。
- **独立附件表**：`message_attachment` 先以 `message_id = NULL` 落库，消息落库的同一事务里绑定；
  未发送的附件由清理循环按 24 小时回收。
- **请求时拼多模态**：`_history` 读附件文件转 `data:` URL，按 OpenAI 兼容的 `image_url` content part 发给模型；
  **只内联最近 2 条带图 user 消息**，更早的换成文字占位，控制上下文与请求体积。
- **能力位显式声明**：`model_config.supports_vision` + 全局 `llm.supports_vision`；默认 false，必须由用户
  在设置里显式打开。前端发送前拦截，后端 WS 再兜底拒收。
- **压缩仍是纯文本**：每张图按 4000 字符计入压缩阈值，摘要里保留「哪条消息带了几张图」的提示，但摘要器
  永远不收图片字节。
- **插话（steering）同样支持图片**：`hub.pending` 从字符串队列改为对象队列，工具边界注入时绑定附件。

## 3. 用户故事

### A. 输入与上传

- **US-01** 作为用户，我在输入框里按 Ctrl+V 粘贴剪贴板截图，输入框上方出现一张缩略图；已输入的文字不受影响。
- **US-02** 作为用户，我粘贴同时含文字和图片的内容时，文字照常进入输入框，图片进入附件队列，两者都不丢。
- **US-03** 作为用户，我把图片文件拖到输入区，或点回形针按钮多选图片，效果与粘贴一致。
- **US-04** 作为用户，单条消息最多 4 张图；我加第 5 张时收到「最多 4 张」提示，已加的 4 张原样保留。
- **US-05** 作为用户，我选择非图片文件（PDF、SVG、视频）时被即时拒绝并说明原因，不发起上传。
- **US-06** 作为用户，图片粘贴后立刻开始上传，缩略图上显示上传中；全部上传完成前发送按钮禁用。
  某张上传失败时该项显示失败态，可单张重试或移除；只要还有失败项，发送按钮就保持禁用，避免用户把
  一半图片发出去后才发现丢了一张。
- **US-07** 作为用户，我可以在发送前移除任意一张已上传图片；移除后可以继续添加，缩略图使用过的本地
  object URL 被释放。
- **US-08** 作为用户，我带着未发送的附件切换会话时，附件被清空、上传中的请求被中止，不会把上一个会话的
  图片带进新会话；输入框里的文字保持不变。
- **US-09** 作为用户，我粘贴的大图（原文件最大 20MB）会在浏览器里被压缩：最长边不超过 2048px，目标不超过
  1.5MB，重编码为 JPEG（透明区域铺白），EXIF/GPS 在重编码中被剥离；动图只取第一帧。
  压缩后仍超过 5MB 的图片才会被拒绝。
- **US-10** 作为用户，我绕过前端直接调上传接口提交超过 5MB、非图片、伪造 Content-Type 或 SVG 的文件时，
  服务端以 4xx 拒绝，并给出可读原因。

### B. 发送与识别

- **US-11** 作为用户，我发送「文字 + 图片」时，乐观气泡立刻显示文字和缩略图；服务端落库后刷新会话仍能看到。
- **US-12** 作为用户，我可以只发图片不写文字；消息正常发出，气泡里没有空白文字行。
- **US-13** 作为用户，使用支持视觉的模型时，模型能回答关于图片内容的问题（人工验收项，见 §11）。
- **US-14** 作为用户，当前生效模型不支持图片输入时，带附件的发送被本地拦下：输入框和附件都保留，
  提示「当前模型不支持图片输入」；去掉附件后可以正常发文字。
- **US-15** 作为用户，如果前端能力位是旧的（模型刚被切走）而服务端判定不支持，服务端以**非终态**
  `busy` 拒收并带 `client_msg_id`；前端回滚乐观气泡、把文字和附件退回输入框，数据库里不留这条消息。
- **US-16** 作为用户，发送瞬间 WS 断线时，乐观气泡回滚，文字与附件留在输入框，并提示连接未就绪。
- **US-17** 作为用户，一条带图消息在模型侧报错后，我点「重试」，前端会重新拉取并上传这些图片，再连同文字
  一起重发；不是只重发文字，也不是复用已绑定的旧附件 ID。
- **US-18** 作为用户，历史 user 气泡按缩略图网格展示图片，点击可预览；插话排队时「排队中」角标仍然显示在
  图片和文字之后。

### C. 历史、压缩与标题

- **US-19** 作为用户，我刷新页面或重新打开会话，历史 user 消息里的图片从 `/api/attachments/{id}` 正常加载。
- **US-20** 作为用户，模型请求只内联**最近 2 条带附件的 user 消息**的图片；更早的带图消息在模型上下文里
  变成 `[图片 ×N：文件名（较早的图片已省略）]` 文字占位；当前这条消息的图片一定在。
- **US-21** 作为用户，压缩阈值把每张图按 4000 字符计；压缩摘要的输入里带「某条消息有几张图、文件名是什么」，
  但摘要器永远看不到图片字节。
- **US-22** 作为用户，压缩完成后更早的图片不再进入模型请求，摘要正文保留图片存在过的说明。
- **US-23** 作为用户，如果会话的第一条消息只有图片，侧栏标题自动变成「图片：<文件名>」；有文字时仍按原
  文本派生规则命名，用户手动改过的标题永不被覆盖。
- **US-24** 作为用户，我删除会话后，附件行随外键级联删除，存储目录被尽力删除（失败只记日志，不影响删除结果）。

### D. 插话（steering）

- **US-25** 作为用户，会话执行中我粘贴图片并发送，气泡带图片和「排队中」角标；模型在下一个工具边界收到
  图片，`steering_done` 按 `client_msg_id` 摘掉角标。
- **US-26** 作为用户，插话还在排队时刷新页面，会话详情里的 `pending_steering` 带回文字、附件和
  `client_msg_id`，页面恢复成带图的「排队中」气泡。
- **US-27** 作为用户，我点「停止」时，尚未注入的带图插话被服务端丢弃；前端把它们的文字和附件一起退回输入框。
- **US-28** 作为用户，当前运行模型不支持视觉时，带图插话被服务端以 `busy`（`running: true`）拒绝；
  文字插话不受影响。

### E. 模型能力与设置

- **US-29** 作为用户，我在「模型库」新增/编辑对话模型时，可以打开「支持图片输入」开关；向量模型（embedding）
  的该开关强制关闭且不可操作。
- **US-30** 作为用户，模型列表对支持图片的对话模型显示「图片」标签；聊天页模型下拉在这些模型名后显示
  「· 图片」。
- **US-31** 作为用户，我在「基础配置」里看到全局「支持图片输入」开关；它只在没有默认对话模型时生效，
  有默认模型时以模型的 `supports_vision` 为准，页面上写明这条优先级。
- **US-32** 作为用户，会话详情返回 `effective_model`（含 `supports_vision` 与来源）；我切换会话模型后，
  PATCH 响应带回新的 `effective_model`，输入框的图片能力提示立即更新，不需要刷新。
- **US-33** 作为用户，当前模型不支持图片时，附件条上显示「当前模型不支持图片输入」的警示；切换到支持
  视觉的模型后，同一批附件可以直接发送。

### F. 隔离、安全与清理

- **US-34** 作为用户，我访问别人的附件 ID、向别人的会话上传、读取别人的会话消息时，一律 404，不泄露存在性。
- **US-35** 作为用户，附件下载响应带 `X-Content-Type-Options: nosniff` 与 `Cache-Control: private, immutable`，
  没有公开可猜测的静态 URL。
- **US-36** 作为用户，上传后一直没发送的附件在 24 小时后被清理（数据库行和磁盘文件）；已绑定消息的附件
  永不被清理；删除会话后残留的目录由清理循环兜底扫掉。
- **US-37** 作为用户，单条消息的附件总量不超过 10MB；超过时上传或发送被拒绝，不会把超大请求送给模型。
- **US-38** 作为用户，我上传的文件名只用于展示和下载；磁盘文件名是 UUID，SVG 与路径穿越文件名都不可能落盘。

## 4. 已定决策

| 决策点 | 裁定 |
|---|---|
| 识别路径 | OpenAI 兼容的 `image_url` 多模态 content part，**不做 OCR、不做服务端识图** |
| 存储位置 | 平台数据区 `data/users/{uid}/chat_uploads/{session_id}/`，**不进用户工作区** |
| 上传协议 | `POST` 原始字节 + `?filename=`，不引入 `python-multipart` 依赖 |
| 消息与附件 | 独立 `message_attachment` 表，`message_id` 可空（先上传后绑定） |
| WS 上行 | 只传 `attachment_ids` + `client_msg_id`，不传 base64 |
| 送模型 | 请求时读文件转 `data:{mime};base64,...`，不暴露公网 URL |
| 历史图片窗口 | 最近 **2 条带附件的 user 消息**内联图片；更早的用文字占位；当前消息必带 |
| 纯图片消息 | 允许；模型 content 里补一个 `[图片]` 文本 part，兼容要求文本 part 的端点 |
| 压缩 | 纯文本摘要；每图按 4000 字符计；摘要输入带图片数量与文件名，不送图片字节 |
| 能力位 | `model_config.supports_vision` 默认 false；全局 `llm.supports_vision` 默认 false |
| 能力门 | 前端发送前拦截；后端 WS 兜底以非终态 `busy` 拒收；配置层是唯一真相 |
| 图片规格 | 4 张/条；客户端原文件 ≤20MB、压缩后目标 1.5MB / 最长边 2048 / JPEG 0.85→0.5 降质；服务端 5MB/张、10MB/条 |
| 客户端压缩 | 一律 canvas 重编码去 EXIF；透明底铺白；GIF 取首帧 |
| 允许类型 | PNG / JPEG / WebP / GIF；**拒绝 SVG** |
| 上传时机 | 粘贴即上传；未发送的附件 24 小时后回收 |
| 去重 | 不做内容哈希去重；同一张图重复粘贴就是两个附件 |
| 删除会话 | 外键级联删附件行 + 尽力删目录；清理循环兜底 |
| 重试 | 前端重新 GET 原图并重新上传，再用新附件 ID 重发；不在服务端克隆已绑定附件 |
| 插话 | `hub.pending` 改存对象 `{content, attachment_ids, client_msg_id}`；`busy`/`steering_done` 回显 `client_msg_id` |
| 历史图片复用 | 不做「引用历史图片再次提问」的 UI |
| 工作区图片文件 | 不支持用 `@` 引用工作区图片当视觉输入（`read_file` 对二进制仍拒绝） |

**被否决的备选（写码前须知，不要照旧口径实现）**：

1. ~~把图片存进用户工作区~~ → 会污染文件树、应用打包与 `source_hash`；`backend/uploads` 的排除名单已经证明
   这类运行时文件有多难收口。
2. ~~WS 传 base64~~ → 帧体积大、与重连/插话队列耦合、同一份字节在内存里反复搬运。
3. ~~给附件开公开静态 URL~~ → 平台没有匿名访问语义，等于绕过登录隔离。
4. ~~服务端用 Pillow 解码/压缩/剥 EXIF~~ → 多一个系统依赖，且客户端已经必须解码一次做压缩；
   隐私边界放在客户端重编码，服务端只做魔数校验。
5. ~~引入 `python-multipart`~~ → 只需要一个上传端点，原始字节足够，少一个依赖少一处升级面。
6. ~~把图片存进 `message` 的 JSONB~~ → 数据库膨胀、查询/清理都难，且无法给浏览器做 `img` 直读。
7. ~~按模型名猜视觉能力~~ → 同名的兼容端点能力并不可靠；必须是显式开关。
8. ~~每次请求都带全部历史图片~~ → 上下文与费用随轮次线性膨胀；最近 2 条是经过取舍的窗口。
9. ~~重试时复用已绑定的附件 ID~~ → 重新绑定会把旧消息的图片抢走；克隆行又需要新的存储语义，前端重传最干净。

## 5. 数据模型与迁移

新增迁移：`changelog/V20261029_01_chat_image_attachment.sql`（同日后继编号，落笔时按仓库当前最大号复核）。

```sql
-- 1) 模型能力位：默认 false，显式 opt-in
ALTER TABLE model_config
  ADD COLUMN IF NOT EXISTS supports_vision BOOLEAN NOT NULL DEFAULT FALSE;

-- 2) 附件表：先上传（message_id NULL），发送时绑定；删会话/删消息都级联
CREATE TABLE IF NOT EXISTS message_attachment (
  id BIGSERIAL PRIMARY KEY,
  session_id BIGINT NOT NULL REFERENCES chat_session(id) ON DELETE CASCADE,
  message_id BIGINT REFERENCES message(id) ON DELETE CASCADE,
  filename VARCHAR(255) NOT NULL,
  mime VARCHAR(100) NOT NULL,
  size_bytes INTEGER NOT NULL,
  storage_path TEXT NOT NULL,          -- 相对 data_dir() 的 POSIX 路径
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_message_attachment_message
  ON message_attachment (message_id);

CREATE INDEX IF NOT EXISTS idx_message_attachment_session
  ON message_attachment (session_id, message_id);

-- 3) 孤儿扫描：只索引未绑定的行（部分索引，绑定后自动退出）
CREATE INDEX IF NOT EXISTS idx_message_attachment_orphan
  ON message_attachment (created_at) WHERE message_id IS NULL;

-- 4) 全局模型能力默认值（用户级键：owner NULL 是平台默认，账号可覆盖）
INSERT INTO app_config (key, value) VALUES ('llm.supports_vision', 'false')
ON CONFLICT DO NOTHING;

-- 回滚：
-- DROP TABLE message_attachment;
-- ALTER TABLE model_config DROP COLUMN supports_vision;
-- DELETE FROM app_config WHERE key='llm.supports_vision';
```

要点：

- `storage_path` 存相对路径，便于测试用 `ZEROCODE_DATA_DIR` 指向临时目录，也便于迁移数据区。
- `filename` 只用于展示/下载提示；磁盘文件名是 `uuid4().hex + ext`，原文件名不参与路径。
- `size_bytes` 存原始字节数；客户端压缩后的实际大小以它为准。
- 测试库 `tests/conftest.py` 的 `TRUNCATE` 名单显式加入 `message_attachment`（`message` 的 CASCADE 已能带到，
  显式列出是为了让清库口径一眼可见）。
- 存量消息没有附件，`attachments` 一律回空数组，不做回填。

## 6. 接口与协议

### 6.1 HTTP 上传

`POST /api/sessions/{session_id}/attachments?filename=<urlencoded>`

- 鉴权：全局登录 guard + 会话归属校验（`chat_session → workspace.owner_user_id`），非本人 404。
- 请求体：图片原始字节；`Content-Type` 必须为 `image/*`。
- 限制：单文件 ≤ 5MB；同一会话未绑定附件合计 ≤ 10MB（含本次）；空体 400。
  上传时的合计检查是**尽力预检**（并发上传可能同时看到旧值）；发送时以所选附件合计与数量为权威判据。
- 校验：按魔数识别 PNG/JPEG/WebP/GIF；SVG/其它一律 400；`Content-Type` 与魔数不一致时以魔数为准
  （不信任客户端声明）。
- 落盘：`data_dir()/users/{uid}/chat_uploads/{session_id}/{uuid}.{ext}`；先写文件，再插行；插行失败删文件。
- 响应 `201`：

```json
{
  "id": 41,
  "filename": "截图 2026-10-29.png",
  "mime": "image/png",
  "size_bytes": 412345,
  "url": "/api/attachments/41"
}
```

错误：`400`（类型/魔数/文件名非法）、`404`（会话不存在/越权）、`413`（超限）、`500`（数据区不可写）。

### 6.2 HTTP 读取

`GET /api/attachments/{attachment_id}`

- 归属校验：附件 → 会话 → 工作区 → 当前账号；不存在/越权统一 404。
- 响应：文件字节；`Content-Type` 用库里的 `mime`；`Content-Disposition: inline; filename*=UTF-8''...`；
  `X-Content-Type-Options: nosniff`；`Cache-Control: private, max-age=31536000, immutable`。
- 路径安全：`storage_path` 解析后必须仍在 `data_dir()` 下；文件缺失 404。
- 附件不可变，因此可以长缓存；删除会话后 URL 自然 404，浏览器缓存残留可接受。

### 6.3 会话详情与消息

`GET /api/sessions/{session_id}` 增加两个字段：

```json
{
  "effective_model": {
    "id": 3,
    "name": "Qwen VL 官方",
    "model": "qwen-vl-max",
    "supports_vision": true,
    "source": "session"
  },
  "pending_steering": [
    {
      "content": "看看这张图",
      "client_msg_id": "4f1c…",
      "attachments": [
        {"id": 41, "filename": "截图.png", "mime": "image/png", "size_bytes": 412345,
         "url": "/api/attachments/41"}
      ]
    }
  ]
}
```

- `effective_model.source` ∈ `session` / `default` / `global`；`id` 在 `global` 时为 `null`，`name` 为全局
  模型名。`supports_vision` 是该来源的能力位。
- `pending_steering` 由 `hub.pending` 的对象队列 + 附件行拼出；附件行已被删则省略该张，不影响文字。
- `GET /api/sessions/{session_id}/messages` 每条消息增加 `attachments: []`；一次查询按 `message_id` 分组，
  不做 N+1：

```json
{
  "id": 88, "seq": 12, "role": "user", "content": "这是什么？",
  "attachments": [
    {"id": 41, "filename": "截图.png", "mime": "image/png", "size_bytes": 412345,
     "url": "/api/attachments/41"}
  ]
}
```

`PATCH /api/sessions/{session_id}/model` 的响应从 `{"ok": true}` 扩展为：

```json
{"ok": true, "effective_model": {"id": 3, "name": "…", "model": "…", "supports_vision": true, "source": "session"}}
```

### 6.4 模型库

`GET/POST/PUT /api/models` 的模型对象增加 `supports_vision`：

- 新建缺省 false；`kind='embedding'` 时强制 false。
- PUT 的 `supports_vision` 缺省为 `null`（不改），与 `kind` 的「缺省不改」语义一致；显式 false 才关闭。
- 把 `kind` 改成 embedding 时强制 false；从 embedding 改回 chat 时按请求值（缺省保留旧值，旧值必为 false）。

### 6.5 WS 协议

**上行**：

```json
{
  "type": "user_message",
  "content": "这是什么？",
  "attachment_ids": [41, 42],
  "client_msg_id": "4f1c9a1e-…"
}
```

- `content` 可以为空，但 `content` 与 `attachment_ids` 不能同时为空。
- `attachment_ids`：去重后 1~4 个整数；必须属于本会话、`message_id IS NULL`、文件存在、类型允许。
- `client_msg_id`：前端生成的 UUID；服务端原样回显，不参与落库。
- 校验顺序：会话归属（连接建立时已验）→ 附件可绑定性 → 合计大小 ≤ 10MB → 模型能力 → 启动 run。
  任何一步失败都回 `busy`，**不落库、不启动 run**。

**下行**：

- `busy` 增加可选 `client_msg_id` 与 `running`：

```json
{"type": "busy", "message": "当前模型不支持图片输入，请切换支持视觉的模型", "client_msg_id": "4f1c…", "running": false}
```

  `running` 告诉前端这场拒绝之后会话到底在不在跑：执行中拒插话为 `true`，发送前拒收为 `false`。
  前端若收到布尔值就同步 `store.running`；这是对「busy 不得清 running」既有规则的细化——不是无条件清，
  而是以服务端实况为准。
- `steering_done` 增加 `client_msg_id`（附件插话与原文字插话一致）：

```json
{"type": "steering_done", "content": "看看这张图", "client_msg_id": "4f1c…"}
```

**插话队列**：`ChatHub.pending` 从 `list[str]` 改为 `list[dict]`：

```python
{"content": str, "attachment_ids": list[int], "client_msg_id": str | None}
```

`hub.queue(session_id, item, max_pending)` 的容量仍按条数计（默认 20）；`pop_pending` 返回对象列表。
`cancel` 丢弃未注入项的行为不变。

### 6.6 配置键

| 键 | 层级 | 默认 | 含义 |
|---|---|---|---|
| `llm.supports_vision` | 用户级（`llm.` 前缀） | `false` | 无默认对话模型时，全局 `llm.*` 接入是否支持图片 |

模型行上的 `supports_vision` 优先于全局键；有默认模型时全局键不参与判定。

### 6.7 送给模型的 content

```json
{
  "role": "user",
  "content": [
    {"type": "text", "text": "这是什么？"},
    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,/9j/4AAQ…"}}
  ]
}
```

- 文本为空时只发 `[{"type": "text", "text": "[图片]"}, …]`，兼容要求至少一个文本 part 的端点。
- 纯文本消息仍保持字符串 `content`，不改变既有请求形态。
- 较早的带图消息：`content` 字符串末尾追加
  `\n\n[图片 ×2：a.png, b.png（较早的图片已省略）]`，不发送任何图片字节。
- 单次请求内联图片原始字节合计上限 10MB；超过时**优先保留当前消息**，从最旧的历史内联消息开始降级为占位
  （正常客户端压缩后 4×1.5MB=6MB，不会触发；这是给绕过前端的上传兜底）。当前消息本身受 10MB/条上限约束。

## 7. 后端设计

### 7.1 新模块

**`app/services/attachment_service.py`**：

- 常量：`ALLOWED_IMAGE_MIMES`、`MAX_ATTACHMENTS_PER_MESSAGE=4`、`MAX_ATTACHMENT_BYTES=5MB`、
  `MAX_MESSAGE_BYTES=10MB`、`MAX_INLINE_BYTES=10MB`、`ORPHAN_TTL_HOURS=24`、`IMAGE_CHAR_EQUIVALENT=4000`。
- `sniff_image_mime(head: bytes) -> str | None`：纯函数，魔数识别；单测主靶。
- `safe_filename(name: str, mime: str) -> str`：取 basename、去控制字符、截断 120 字符、空名回退。
- `session_upload_dir(uid: int, session_id: int) -> Path`：`data_dir()/users/{uid}/chat_uploads/{sid}`。
- `create_attachment(sf, uid, session_id, filename, data, mime) -> dict`：写文件 + 插行，失败清理。
- `staged_bytes(sf, session_id) -> int`：未绑定附件合计，上传前判 10MB。
- `load_bindable(sf, session_id, ids) -> list[dict]`：校验归属/未绑定/文件存在/大小/类型，返回元数据。
- `for_ids(sf, session_id, ids) -> dict[int, dict]`：按 ID 取元数据（不要求未绑定），用于会话详情的
  `pending_steering` 恢复。
- `for_messages(sf, session_id, message_ids) -> dict[int, list[dict]]`：消息列表接口与历史构建共用。
- `for_llm(sf, message_ids) -> dict[int, list[dict]]`：只对选中的消息读文件字节，返回 `data_url`。
- `bind_to_message(s, session_id, message_id, ids) -> list[dict]`：在调用方事务内条件更新，返回绑定后的行。
- `cleanup_orphans(sf, ttl_hours=24) -> int`：删过期未绑定行+文件；扫会话目录里无行引用的文件；
  删已不存在会话的目录。详见 §7.7。
- `format_attachment(row) -> dict`：统一出参 `{id, filename, mime, size_bytes, url}`。

**`app/api/attachments.py`**：一个 router，两个路径：

- `POST /api/sessions/{session_id}/attachments`
- `GET /api/attachments/{attachment_id}`

按 §6.1/§6.2 实现；原始体读取用 `request.stream()` 累计上限，不能先 `await request.body()` 再判大小。
在 [main.py](../../app/main.py) 注册 router。

### 7.2 消息落库与绑定

[message_service.py](../../app/services/message_service.py) 的 `add_message` 增加 `attachment_ids: list[int] | None`：

- 插入 message 后，在**同一事务**内执行：
  `UPDATE message_attachment SET message_id=:m WHERE id IN :ids AND session_id=:sid AND message_id IS NULL RETURNING …`
- 返回行数与去重后的 ID 数不一致 → `rollback()` 并抛 `ValueError("附件不可用或已被使用")`；
  消息与绑定要么都成，要么都不成。
- 返回值增加 `attachments` 列表（已绑定行），供自动标题直接取文件名，省一次查询。
- `chat_session.updated_at` 的更新保持原样。

### 7.3 多模态历史

[loop.py](../../app/agent/loop.py) 的 `_history`：

1. 取窗口消息（既有逻辑不变），保留 `m.id` 供附件查询。
2. 对窗口内的 user 消息查 `attachment_service.for_messages`，得到 `message_id → attachments`。
3. 按 seq 从新到旧找出**最近 2 条带附件的 user 消息**，记为 `inline_ids`。
4. 逐条构建：
   - 无附件：保持字符串 content。
   - 有附件且在 `inline_ids`：content 变成 parts 数组；文本 part 在前（空文本用 `[图片]`），
     `image_url` part 在后；读不到文件的那张降级成文字占位，不阻断整轮。
   - 有附件但不在 `inline_ids`：字符串 content 末尾追加旧图占位。
5. 单次请求内联原始字节累计超过 `MAX_INLINE_BYTES` 时，**先保留当前消息**，从最旧的历史内联消息开始
   降级为占位；当前消息本身受 10MB/条上限约束。
6. `_build_messages` 里 `_memories_of` 在 `user_text.strip()` 为空（纯图片消息）时直接返回 `[]`，
   不拿空查询去打向量接口。

`_inner` 的用户消息落库改为：

```python
row = await add_message(self.sf, session_id, "user", content=user_text,
                        context_content=expanded or None,
                        attachment_ids=attachment_ids)
titled = await ensure_session_title(self.sf, session_id, user_text,
                                    [a["filename"] for a in row["attachments"]])
```

`run` 签名增加 `attachment_ids: list[int] | None = None`；`ws.py` 的 `run_task` 透传。

### 7.4 插话

- `_drain_steering` 收到对象项：`content`、`attachment_ids`、`client_msg_id`。
- 文本展开仍走 `expand_mentions`；纯图片插话 `expanded` 为 None → `context_content=None`。
- `add_message(..., attachment_ids=item["attachment_ids"])`；绑定失败只回滚这一条并给 `error`，
  不阻断本轮（与工具调用异常的处理边界一致）。
- `steering_done` 带上 `client_msg_id`。
- `hub.pop_pending` 的旧字符串项不再出现；`run_vision` 字典记录本 run 实际使用的模型能力（见 §7.6）。

### 7.5 自动标题

[session_title_service.py](../../app/services/session_title_service.py)：

- `ensure_session_title(sf, session_id, user_text, attachment_names=None)`。
- `derive_title(user_text)` 仍优先；返回 None 且有附件名时用 `derive_title("图片：" + attachment_names[0])`。
- 条件更新 `WHERE title = '新会话'` 与幂等语义不变。

### 7.6 模型能力解析

[model_resolver.py](../../app/agent/model_resolver.py) 的返回值增加：

| 字段 | session/default | global |
|---|---|---|
| `id` | 模型行 id | `null` |
| `name` | 模型行 name | `cfg["llm.model"]` |
| `supports_vision` | 行上的布尔值 | `bool(cfg.get("llm.supports_vision", False))` |
| `source` | `session` / `default` | `global` |

`resolve_model_for_user` 与 `resolve_model` 的所有 SELECT 都补 `id, name, supports_vision`；embedding 链路不受影响。

`ws.py` 的校验与执行：

1. 非插话消息带附件时，在接收循环内先 `load_bindable` + 合计大小 + `resolve_model` 能力判定；
   通过后把 `model_cfg` 传进 `run_task`，避免重复解析。
2. `run_task` 把 `model_cfg["supports_vision"]` 写入 `hub.run_vision[session_id]`，`finally` 清理。
3. 插话带附件时，以 `hub.run_vision.get(session_id) is True` 为准判断（run 使用的是启动时解析的模型，
   中途 PATCH 会话模型不影响本轮）。
4. 拒绝时发 `busy`，带 `client_msg_id` 与 `running`（插话 true / 发送前 false），不落库。

### 7.7 清理循环

`app/services/attachment_service.py::cleanup_orphans`：

1. `DELETE FROM message_attachment WHERE message_id IS NULL AND created_at < now() - interval '24 hours' RETURNING storage_path`，
   逐条尽力 `unlink`。`cleanup_orphans` 的 `ttl_hours` 参数如要绑定进 SQL，按 asyncpg 的要求用 `timedelta`
   传 INTERVAL，不要传字符串；或直接用字面量 `interval '24 hours'`（本期默认值固定，优先后一种）。
2. 扫描 `data/users/*/chat_uploads/*`：
   - 目录对应的 `session_id` 在 `chat_session` 已不存在**且目录 mtime 超过 24 小时** → 删整目录
     （mtime 门槛避免与正在进行的上传/删除竞态）；
   - 单个文件未被任何 `storage_path` 引用且 mtime 超过 24 小时 → 删文件。
3. 任何单步异常只记日志，不抛出；循环本身不阻塞平台启动。

[main.py](../../app/main.py) lifespan 启动 `attachment_cleanup_loop`（先睡 6 小时再跑，测试期不会触发），
退出时 cancel 并等待 CancelledError。

### 7.8 删会话

[sessions.py](../../app/api/sessions.py) 的 `delete_session` 在 DB 删除成功后，按会话归属反查 uid，
尽力 `shutil.rmtree(session_upload_dir(uid, session_id))`；失败只记日志。附件行由外键级联删除。

### 7.9 System prompt

[prompts.py](../../app/agent/prompts.py) 增加一条规则（措辞可微调，语义固定）：

> 10. 用户消息可能直接附带图片（content part 中的 `image_url` 就是原图）。需要看图时直接依据消息里的
>     图片回答，不要尝试用 read_file 去工作区找同名文件；图片附件不占用工作区路径。

## 8. 前端设计

### 8.1 新工具与组合式函数

**`frontend/src/utils/imageAttachments.js`**（纯函数，vitest 主靶）：

- 常量与后端对齐：`MAX_ATTACHMENTS=4`、`MAX_SOURCE_BYTES=20MB`（压缩前）、`MAX_ATTACHMENT_BYTES=5MB`
  （压缩后/服务端）、`MAX_MESSAGE_BYTES=10MB`、`MAX_IMAGE_EDGE=2048`、`TARGET_IMAGE_BYTES=1.5MB`、
  `ACCEPTED_IMAGE_MIMES`。
- `validateImageFile(file, currentCount)` → 错误文案 | `null`：数量、类型、原始体积（≤20MB）。
- `compressImage(file)` → `Promise<Blob>`：**一律** `createImageBitmap`（回退 `Image` + object URL）→ canvas →
  JPEG；最长边 2048；空白/透明区域铺白；先按 0.85 编码，产物 >1.5MB 时按 0.7→0.5 降质重试；
  解码失败抛可读错误。压缩产物 >5MB 时由调用方拒绝。
- `validateCompressed(blob, items)` → 错误文案 | `null`：压缩后单张 ≤5MB、本批合计 ≤10MB。
- `newClientMsgId()`：`crypto.randomUUID()`，缺失时用时间戳+随机数兜底（测试环境）。
- `formatBytes(n)`：附件条上的体积文案。

**`frontend/src/composables/useImageAttachments.js`**：

- 状态：`items`（`{key, id?, filename, mime, size_bytes, url?, previewUrl, status, error, controller}`）。
- `addFiles(files)`：按当前 `currentSessionId` 捕获会话；校验数量/类型/大小 → 压缩 → 上传 → 写回 `id/url`。
- `remove(key)`、`retry(key)`、`clear()`、`readyItems()`、`uploading`、`count`。
- 会话切换时 `clear()` 并 `abort()` 在途请求；晚到的上传结果如果会话已变，直接忽略并释放 object URL。
- 附件项来自 `rejectedAttachments`（已有服务端 URL）时直接标 `ready`，不再上传。
- `readyItems()` 只返回 `status === 'ready'` 的服务端字段（id/filename/mime/size_bytes/url）。

### 8.2 ChatInput.vue

- 三条入口：输入区根节点的 `@paste`（paste 事件冒泡，不依赖 `a-textarea` 的事件透传）、根节点的
  `@dragover/@drop`、回形针按钮触发的隐藏
  `<input type="file" accept="image/png,image/jpeg,image/webp,image/gif" multiple>`。
- 粘贴判定：clipboard 里同时有图片与 `text/plain` 时**不 preventDefault**（文字照常粘贴），图片另行入队；
  只有图片时 preventDefault。
- 附件条：缩略图 56px、移除按钮、上传中 spinner、失败态「重试/移除」、`n/4` 计数。
- 当前模型不支持图片时，附件条顶部显示警示；发送按钮禁用。
- `canSend`：`(text.trim() || readyCount > 0) && !uploading && !hasFailed && !compacting &&
  (readyCount === 0 || visionSupported)`。失败项必须先重试或移除，否则发送按钮一直禁用。
- `doSend` 改为 `chatStore.send({ text, attachments: readyItems() })`；只有返回 true 才清空文字与附件。
- 监听 `chatStore.rejectedAttachments`：合并回附件条（按 id 去重），然后清空该 store 字段。
- 监听 `chatStore.currentSessionId`：附件条清空（文字保留）。
- 回形针按钮放在 `.zc-input-buttons` 内、`<chat-app-actions />` 之前。
- 发送按钮文案与「插话」逻辑不变。

### 8.3 chat store

**状态新增**：

- `effectiveModel: null`、`visionSupported: false`
- `rejectedAttachments: []`
- `pendingOutbox: {}`（`clientMsgId → {sessionId, text, attachments}`，仅用于异步拒收时回滚）
- `retrying: false`

**动作**：

- `send({ text = '', attachments = [] })`：
  - 空文本且无附件 → false。
  - `compacting` → 设 `rejectedDraft`/hint，false（文字与附件都留在 ChatInput，不设 outbox、不回填）。
  - 有附件且 `!visionSupported` → hint「当前模型不支持图片输入…」，false（同上，留在输入框）。
  - 生成 `clientMsgId`，乐观气泡带 `content`、`attachments`、`client_msg_id`、`queued`（running 时）。
  - `pendingOutbox[clientMsgId] = {sessionId: currentSessionId, text, attachments}`。
  - 上行 `{type:'user_message', content, attachment_ids, client_msg_id}`。**上行失败**（`_uplink` 返回 false）：
    回滚气泡、删除 outbox 项、`running=false`、只设 `rejectedDraft` 与 `OFFLINE_HINT`；**不**设
    `rejectedAttachments`——`send` 返回 false，ChatInput 不会清空本地附件，再回填会重复。
  - 异步 `busy` 到达时：按 `client_msg_id` 找气泡（找不到再按 `pendingEcho` 内容回退）→ 移除气泡 →
    从 `pendingOutbox` 恢复文字与附件 → 删除 outbox 项 → 按 `ev.running` 同步执行态。若 outbox 项的
    `sessionId` 与当前会话不同（用户已切走），只删 outbox，不把旧会话的草稿回填到新会话。
- `steering_done`：优先按 `client_msg_id` 摘 `queued`，旧协议按 `content` 回退。
- `openSession`：写入 `effectiveModel` 与 `visionSupported`；清空 `pendingOutbox`/`rejectedAttachments`；
  `pending_steering` 的对象项直接映射为带 `attachments` 的排队气泡。
- `setModel`：使用 PATCH 返回的 `effective_model` 更新 `effectiveModel`/`visionSupported`。
- `cancel`：收集所有 `queued` 气泡的文字与附件；文字按现有规则 join 进 `rejectedDraft`，附件进
  `rejectedAttachments`；清理 `pendingOutbox`。
- `retry()`（async）：
  - 取最后一条 user 消息；设 `retrying=true`。
  - 有附件：逐个 `fetchAttachmentBlob(url)` → `uploadAttachment(sessionId, blob, filename)` 换新 ID；
    任一失败 → 设 `rejectedDraft`/`rejectedAttachments`/hint，返回。
  - 调 `send({text, attachments: 新附件})`；`send` 返回 false 时把文字与附件退回。
  - `finally` 复位 `retrying`。
- `applyEvent` 的 `status` 终态清空 `pendingOutbox`（消息已被服务端接收或本轮已结束，不再需要回滚依据）。

**API 封装**（`frontend/src/api/index.js`）：

```js
export const uploadAttachment = (sessionId, blob, filename) =>
  http.post(`/sessions/${sessionId}/attachments`, blob, {
    params: { filename },
    headers: { 'Content-Type': blob.type || 'image/jpeg' }, // 压缩产物恒为 JPEG
  })
export const fetchAttachmentBlob = (url) =>
  http.get(url, { responseType: 'blob', baseURL: '' }) // url 已是 /api/... 全路径
```

### 8.4 ChatMessages.vue

- user 气泡内、文字之前渲染附件网格：

```html
<div v-if="m.attachments && m.attachments.length" class="zc-bubble-images">
  <a-image-preview-group>
    <a-image v-for="a in m.attachments" :key="a.id" :src="a.url" :alt="a.filename" :width="160" />
  </a-image-preview-group>
</div>
<!-- 原文字节点改为 v-if="m.content"，避免纯图片消息留下空白行 -->
```

- 网格两列、160px 方形、`object-fit: cover`、圆角与气泡内边距协调；预览使用 Ant Design Vue 自带的图片预览。
- 「排队中」角标仍跟在图片/文字之后；纯图片排队气泡只有图片+角标。
- `a-image` 已随全局 Ant Design Vue 注册，无需单独 import；若组件库版本不支持 preview-group，则退化为
  单张 `a-image`，行为不变（实现计划按 4.2.6 的 `a-image-preview-group` 落地）。

### 8.5 设置页

**模型库**：

- `modelForm.js`：`emptyModelForm()` 加 `supports_vision: false`；`buildModelPayload()` 对
  `kind==='embedding'` 强制 false，其余取布尔值。
- `SettingsView.vue` 模型弹框加「支持图片输入」开关，`extra` 文案说明「开启后该模型可用于聊天图片识别」；
  `kind` 切到 embedding 时开关禁用并复位 false（watch），切回 chat 不自动打开。
- 模型表格新增「能力」列：`kind==='chat' && supports_vision` 显示「图片」标签；其余显示「—」。
  embedding 即使脏数据里 `supports_vision=true` 也不显示（后端已强制 false）。

**基础配置**：

- `stores/config.js` 的 `FIELD_TO_KEY` 加 `supportsVision: 'llm.supports_vision'`，`form.supportsVision` 默认 false。
- 「模型接入」卡片加「支持图片输入」开关，`extra` 写明「仅在未配置默认对话模型时生效；有默认模型时以其
  模型库设置为准」。

**聊天页模型下拉**：

- `models` 列表已带 `supports_vision`；下拉项对视觉模型追加「· 图片」后缀。
- 默认模型项（哨兵 0）不追加，能力以 `effective_model` 为准。

## 9. 错误处理与边界

| 场景 | 行为 |
|---|---|
| 客户端选到 SVG / 非图片 | 本地拒绝，附件条不新增，hint 说明支持的格式 |
| 单文件 > 5MB | 本地拒绝；绕过前端时服务端 413 |
| 单条消息 > 4 张 | 本地拒绝第 5 张；服务端再验一次（去重后 > 4 → busy） |
| 单条消息合计 > 10MB | 服务端上传时 413 / 发送时 busy；本地按 10MB 预检 |
| 上传中发送 | 发送按钮禁用，hint「图片上传中」 |
| 上传失败 | 单项失败态，可重试/移除；不阻塞其它附件 |
| 上传返回 404（会话被删/越权） | 单项失败，hint 原文；切会话时整体清空 |
| 数据区不可写 | 上传 500，hint 服务端 detail；不产生半条消息 |
| WS 未就绪 | `send` 返回 false，气泡回滚，文字留在输入框；附件仍在输入框 |
| 非视觉模型 + 附件 | 本地发送禁用 + 警示；服务端兜底 `busy(running:false)`，回滚并保留草稿 |
| 附件 ID 已绑定/不属于本会话/文件丢失 | 服务端 `busy`，回滚；前端提示重新粘贴 |
| 运行中发带图插话但本轮模型不支持 | 服务端 `busy(running:true)`，队列不入；已有 run 不受影响 |
| 运行中切换会话模型 | 本轮仍用启动时的模型；`run_vision` 决定插话能否带图；前端能力位可能超前，服务端为准 |
| 历史附件文件丢失 | `_history` 用占位文本，不阻断本轮；消息接口仍返回该行的 URL（GET 404） |
| 压缩期间发带图消息 | 本地按现有 `compacting` 规则拦截；服务端也有 busy 兜底 |
| 重试时原图 GET 失败 | 不发送，文字+附件退回输入框并提示 |
| 清理循环异常 | 只记日志，不影响平台与聊天 |
| 同一附件 ID 被重复发送 | 第二次绑定行数为 0 → busy/error，不产生半条消息 |

## 10. 测试决策

测试遵守两条既有纪律：后端 pytest **串行**（共享 `zerocode_test` 库，并发 TRUNCATE 会死锁）；
前端 vitest 用 happy-dom，组件测试注意二次 mount 与共享监听器清理。

### 10.1 后端

新增 `tests/test_attachments_api.py`：

- 上传：合法 PNG/JPEG/WebP/GIF 各一；魔数与声明不一致时按魔数落库；SVG/文本/空体 400；
  单文件 5MB+ 与合计 10MB+ 413；文件名 `../x.png`、超长名、空名被清洗。
- 隔离：他人会话上传 404；他人附件 GET 404；不存在 404。
- 读取：`Content-Type`、`nosniff`、`Cache-Control`、`Content-Disposition`；文件缺失 404。
- 测试用 `ZEROCODE_DATA_DIR=tmp_path` 隔离落盘。

新增 `tests/test_agent_image_history.py`（用 `ScriptedLLM` 捕获 messages）：

- 当前 user 消息带图 → content parts 含 `image_url` 且是 `data:` URL。
- 最近 2 条带图 user 消息内联；第 3 条旧图变占位文本。
- 纯图片消息 → 文本 part 为 `[图片]`，无空字符串。
- 附件文件缺失 → 占位，不抛异常。
- 超过 `MAX_INLINE_BYTES` → 先降级最旧的历史图片，**当前消息的图片保留**。

扩展 `tests/test_compaction.py`：

- `estimate_chars` 按 4000/图计数。
- 摘要 digest 含 `[图片 ×N: …]`；`ScriptedLLM` 收到的摘要输入不含 `image_url`。
- 图片只存在于 head 时摘要仍能生成；tail 保留原文时图片仍在内联窗口。

扩展 `tests/test_ws.py`：

- `hub.queue`/`pop_pending` 对象协议；容量仍按条数。
- `pending_steering` 返回对象（含附件与 `client_msg_id`）。
- 纯图片 `user_message` 能启动 run（不再被空 content 丢弃）。
- 附件不属于本会话 → `busy`，不落库，带 `client_msg_id`、`running:false`。
- 非视觉模型 + 附件 → `busy`；切到视觉模型后同一批附件可发。
- 带图插话：工具边界落库并绑定，`steering_done` 带 `client_msg_id`；不支持视觉的 run → `busy(running:true)`。
- 取消带图插话 → hub 队列清空，前端侧由 store 测试覆盖。

扩展 `tests/test_sessions.py`：

- 详情 `effective_model` 三种 source + `supports_vision`。
- PATCH model 返回新的 `effective_model`。
- messages 每条带 `attachments`，跨会话不串。

扩展 `tests/test_models_api.py` / `tests/test_model_resolver.py`：

- `supports_vision` 创建/更新/默认 false；PUT 缺省不改；embedding 强制 false。
- resolver 的严格字典断言更新为含 `id/name/supports_vision` 的新形状（global 的 `id` 为 null）。
- 全局 `llm.supports_vision` 生效；默认模型行覆盖全局。

新增 `tests/test_attachment_cleanup.py`：

- 24h+ 未绑定行+文件被删；未满 24h 保留；已绑定保留。
- 目录对应会话已删 → 整目录删；未引用且超龄文件删。
- 清理异常不抛出。

扩展 `tests/test_agent_loop.py` 或新增标题用例：

- 纯图片首消息 → 标题「图片：<文件名>」；文本优先；手动标题不覆盖。

迁移测试（`tests/test_migrations.py`）：

- `model_config.supports_vision` 存在且 NOT NULL DEFAULT FALSE。
- `message_attachment` 表、外键、三索引、部分索引条件。
- `llm.supports_vision` 平台行存在；重放迁移幂等。

### 10.2 前端

新增 `frontend/src/utils/imageAttachments.test.js`：

- 类型/原始体积/数量校验；`validateCompressed` 的单张 5MB 与合计 10MB 边界；`formatBytes`；
  `newClientMsgId` 唯一性。
- 压缩函数不做 jsdom canvas 断言（happy-dom 无真实 canvas），压缩路径靠手工验收；纯决策函数测干净。

新增 `frontend/src/composables/useImageAttachments.test.js`：

- `addFiles` 调压缩+上传（mock `api`），成功后 `readyItems` 只含服务端字段。
- 超过 4 张不新增；非图片拒绝；上传失败可重试；会话切换 abort 并清空。
- `rejectedAttachments` 注入为 ready 项，不重复上传。

扩展 `frontend/src/stores/chat.test.js`：

- `send`：纯图片允许；非视觉模型 + 附件本地拦截；compacting 拦截且附件不回填。
- `busy` 带 `client_msg_id`：回滚对应气泡、恢复文字+附件、按 `running` 同步执行态。
- `steering_done` 带 `client_msg_id`：只摘对应气泡的角标。
- `openSession`：`effective_model`/`visionSupported`、`pending_steering` 对象还原。
- `cancel`：带图排队气泡的文字与附件一起退回。
- `retry`：mock GET blob + 重新上传 + send，断言新附件 ID 被使用。

新增 `frontend/src/components/ChatInput.test.js`（仿 `ChatAppActions.test.js` 的 mock 风格）：

- paste 图片 → 调 `addFiles`；纯文字粘贴不拦截。
- 上传中 / 非视觉模型 + 附件 → 发送按钮 disabled。
- 发送成功清空文字与附件；失败保留。
- 切换会话清空附件、保留文字。

新增 `frontend/src/components/ChatMessages.test.js`：

- user 消息带附件渲染 `img`（`a-image` 的 src）；纯图片消息不渲染空文本行；
  排队角标与图片共存。

扩展 `frontend/src/utils/modelForm.test.js`、`frontend/src/stores/config.test.js`：

- `supports_vision` 初值/载荷；embedding 强制 false；FIELD_TO_KEY 映射。

**「完成」的标志**：后端相关 pytest 全绿（串行跑）；`cd frontend && npm run test -- --run` 全绿；
`npm run build` 成功；本地手工走一遍 §11 的主路径。

## 11. 验收标准

1. **主路径**：配置一个支持视觉的模型（或把默认模型能力打开）→ 在聊天页粘贴截图 → 缩略图出现 →
   输入「这是什么」→ 发送 → 模型回答图片内容。
2. **纯图片**：不输入文字只发图 → 消息正常落库、气泡无空白行；首条消息的侧栏标题为「图片：<文件名>」。
3. **能力门**：切到非视觉模型 → 带附件发送被禁用并提示 → 去掉附件可发文字 → 切回视觉模型后同一批附件可发。
4. **历史**：刷新页面 → 历史气泡显示图片缩略图并可预览；最新两条带图消息进入模型上下文，第三条旧图在
   模型请求里是占位文字（用 ScriptedLLM/日志验证）。
5. **插话**：执行中粘贴图片发送 → 气泡带图与「排队中」→ 工具边界后角标消失，模型能回答图片问题；
   刷新时排队气泡仍在。
6. **失败与回滚**：停掉 WS 后发送 → 气泡回滚、文字与附件回到输入框；带图消息报错后「重试」能重新上传并重发。
7. **隔离**：用另一个账号访问附件/上传到他人会话 → 404；上传 SVG/6MB 文件 → 4xx。
8. **清理**：把孤儿附件的 `created_at` 改到 25 小时前并跑一次清理 → 行与文件消失；已绑定附件不受影响。
9. **设置**：模型库开关、图片标签、全局开关与优先级说明可见；embedding 的开关不可打开。

## 12. 明确不做

- **不做服务端 OCR / 图片理解服务**：识别完全交给用户配置的多模态模型。
- **不做 PDF / Word / 音视频 / 压缩包附件**：本期只收 PNG/JPEG/WebP/GIF。
- **不做图片编辑、标注、裁剪、旋转**；不提供多图排序。
- **不做历史图片复用入口**（「把上一张图再发给模型」）：用户需要时重新粘贴/上传。
- **不把图片存进用户工作区**，也不让 `@` 引用工作区图片作为视觉输入。
- **不做内容哈希去重/秒传**。
- **不做公开分享链接、匿名访问、签名 URL**。
- **不做图片生成 / 图片输出**。
- **不把图片送进压缩摘要器**，也不改变压缩的纯文本语义。
- **不新增 WebSocket 帧类型**：复用 `user_message` / `busy` / `steering_done`，只加字段。
- **不做服务端图片解码/转码**（无 Pillow 依赖）；EXIF 剥离由客户端重编码保证。
- **不改变文字-only 的既有链路**：无附件时请求形态、事件、落库与现在一致。

## 13. 风险与取舍

| 风险 | 影响 | 取舍/缓解 |
|---|---|---|
| 默认模型 `deepseek-chat` 不支持视觉 | 开箱即用时图片功能不可用 | 规格要求显式开关；设置页文案点明；不自动改默认模型 |
| base64 让请求体积增长约 33% | 4×1.5MB ≈ 8MB 请求，部分端点可能限流/超时 | 客户端压到 1.5MB、最长边 2048；服务端 10MB/条；10MB 内联上限、优先保留当前消息 |
| 历史图片导致上下文膨胀 | 多轮带图对话成本上升 | 只内联最近 2 条带图消息；压缩把图按 4000 字符计 |
| 客户端重编码有损 | 小字/细线截图可能变糊 | JPEG 0.85 + 2048 长边；需要原图时可另行上传工作区文件（本期不做） |
| 只在客户端剥 EXIF | 绕过前端的上传可能保留 GPS | 服务端不做解码是明确取舍；UI 是唯一支持入口；安全边界在登录隔离 |
| 附件存平台数据区 | 数据区备份/迁移需包含 `data/users` | `storage_path` 存相对路径；清理循环自洽；部署文档已有数据区概念 |
| 运行中切模型 | 前端能力位可能与本轮实际模型不一致 | 服务端 `run_vision` 为准；前端被拒时回滚并提示 |
| 断开/刷新时乐观气泡与服务端不一致 | 可能短暂出现重复或丢失 | `client_msg_id` 精确对账；`pending_steering` 恢复；孤儿清理兜底 |
| 图片 URL 长缓存 | 删除会话后浏览器仍显示缓存图 | `private` 缓存 + 会话删除后 404；本期接受本地缓存残留 |

## 14. 下游拆票建议

实现计划按垂直切片推进，每片都能独立验证：

1. **数据与能力位**：迁移 + `model_config.supports_vision` + 全局配置键 + 模型库/设置页开关 + resolver 字段。
2. **上传与读取**：`attachment_service` + `attachments.py` + 隔离/魔数/限额测试 + 清理循环。
3. **消息落库与展示**：`add_message` 绑定 + messages/detail 接口 + ChatMessages 图片渲染 + 历史回放。
4. **多模态请求**：`_history` 最近 2 条窗口 + 占位 + 纯图片文本 part + 10MB 内联上限（优先当前消息）+
   ScriptedLLM 测试。
5. **前端输入链路**：`imageAttachments`/`useImageAttachments` + ChatInput 三入口 + store 发送/回滚/重试。
6. **插话与协议收口**：hub 对象队列 + `client_msg_id` 回显 + `busy.running` + 队列/取消/刷新测试。
7. **压缩与标题**：图片字符计数 + 摘要占位 + 纯图片标题 + 测试。
8. **验收与文档**：手工主路径、设置页文案、CONTEXT 术语补充（附件 / 视觉模型）、最终回归。
