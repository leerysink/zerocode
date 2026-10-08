# 0012 工作区记忆的语义检索用 pgvector + 独立 embedding 模型

平台 Agent 要新增「工作区记忆」——按 workspace 隔离的跨会话原子记忆条目，每轮对话按当前
任务语义召回 topK 注入 system。我们决定检索底座用 **Postgres pgvector 扩展 + 一个独立的
embedding 模型**：写入记忆时算向量存 `vector` 列，召回时把当前任务向量化做相似度检索。

理由：记忆的价值随条目增长而依赖「召回得准」，纯关键词/全量塞进 system 都无法在条目变多
后保持精度。平台库本就是 Postgres，pgvector 是同栈内最自然的向量存储，不额外引入独立向量
数据库；OpenAI 兼容端点（现有 LLM 底座多为这类）通常同时提供 embeddings，只需在
`model_config` 之外补一份 embedding 模型配置即可，不改变对话链路。

## 备选方案

- **LLM 自选（复用对话模型）**：把记忆标题/摘要索引全塞进 system 让对话模型自己挑相关项。
  零新依赖，但吃 token、记忆一多精度掉、且每次召回都多花一次推理。
- **纯全文/关键词检索（tsvector / pg_trgm）**：不需 embedding 模型，但非真语义，同义词与
  中文分词需额外处理，与「记住经验并按语义调起」的需求不符。

## 后果

- 新增一份 **embedding 模型配置**（账户级或全局，独立于对话 `model_config`）；未配置时
  召回**降级为关键词/全文检索**，功能不阻断，配置好后自动切回语义检索（降级路径不另立
  平行实现，复用同一 recall 入口）。
- 平台库迁移需 `CREATE EXTENSION IF NOT EXISTS vector`，落在顶层 `changelog/` 的 SQL 版本
  文件里（受 `run_migrations` 自动应用、`schema_migration` 登记）。若目标库无 pgvector
  扩展可用，安装会失败——这是部署前置条件，需在文档标注。
- 记忆写入多一次 embedding 调用（算向量），带来额外延迟与成本；凭据/密钥等敏感值在写工具
  层面拦截 + 存入前脱敏，**绝不入库、不进向量**（对齐 `deploy/**` 凭据不入模型上下文的红线）。
