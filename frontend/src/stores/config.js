// config store：系统配置加载/保存，负责表单字段 ↔ 后端配置键（app_config 表 llm.*/agent.*/clarify.*/memory.*/fs.*）的字段映射
import { defineStore } from 'pinia'
import { configMeta, getConfig, putConfig } from '../api'
import { useAuthStore } from './auth'

// 表单字段 → 后端配置键（键固定，见 Global Constraints）
const FIELD_TO_KEY = {
  baseUrl: 'llm.base_url',
  apiKey: 'llm.api_key',
  model: 'llm.model',
  supportsVision: 'llm.supports_vision',
  writeConfirm: 'agent.write_confirm',
  askTimeoutSeconds: 'agent.ask_timeout_seconds',
  maxToolRounds: 'agent.max_tool_rounds',
  previewMaxRestarts: 'agent.preview_max_restarts', // <=0 不限，见 app/agent/preview_tools.py::_max_restarts
  historyWindow: 'agent.history_window',
  openFolderMode: 'fs.open_folder_mode', // auto | system | web，见 utils/openFolder.js
  // clarify.* 是账户级键（ADR-0032）：门禁拷问的是「这个人」，关掉只影响自己。
  // 默认值与后端 app/agent/clarify.py 的 DEFAULT_ENABLED 同口径——库里没这个键时算开。
  clarifyEnabled: 'clarify.enabled',
  askRemindAt: 'clarify.ask_remind_at', // 第 N 题的止损提醒，<=0 关闭；默认值与 clarify.py::DEFAULT_REMIND_AT 同口径
  // ---- 上下文压缩与执行中插话（agent.*：平台级，保存影响所有账号）----
  autoCompactChars: 'agent.auto_compact_chars',
  compactMinNew: 'agent.compact_min_new',
  compactKeepRecent: 'agent.compact_keep_recent',
  steeringQueueMax: 'agent.steering_queue_max',
  // ---- 预览工具（agent.preview_*，区间与 preview_tools.py 的夹取常量一致）----
  previewWaitSeconds: 'agent.preview_wait_seconds',
  previewLogLines: 'agent.preview_log_lines',
  previewConfirmRunning: 'agent.preview_confirm_running',
  // ---- 浏览器工具（agent.browser_*，区间见 browser_tools.py / browser_session_tools.py）----
  browserMaxPerRun: 'agent.browser_max_per_run',
  browserMaxStepsPerRun: 'agent.browser_max_steps_per_run',
  browserTimeoutSeconds: 'agent.browser_timeout_seconds',
  browserExpectTimeoutMs: 'agent.browser_expect_timeout_ms',
  browserSettleMs: 'agent.browser_settle_ms',
  browserInteractiveMax: 'agent.browser_interactive_max',
  // ---- 联网工具（agent.web_*；公网走查的步数沿用 agent.browser_max_steps_per_run，ADR-0053）----
  webMaxFetchesPerRun: 'agent.web_max_fetches_per_run',
  // ---- 数据库工具（agent.db_rows_*，区间见 db_tools.py）----
  dbRowsLimit: 'agent.db_rows_limit',
  dbRowsAssertMax: 'agent.db_rows_assert_max',
  // ---- 记忆召回（memory.*：账户级，只影响自己；区间见 memory_service.py）----
  memoryEnabled: 'memory.enabled',
  memoryTopK: 'memory.top_k',
  memoryMinSimilarity: 'memory.min_similarity',
  memoryMaxTokens: 'memory.max_tokens',
  // ---- 子任务委派（subrun.*：账户级，封的是「我自己烧的钱」；夹取见 app/agent/subrun.py）----
  subrunMaxTasks: 'subrun.max_tasks',
  subrunMaxRounds: 'subrun.max_rounds',
}

export const useConfigStore = defineStore('config', {
  state: () => ({
    form: {
      baseUrl: '',
      apiKey: '',
      model: '',
      supportsVision: false, // 仅无默认对话模型时生效（model_resolver 回落全局 llm.*）
      writeConfirm: false,
      askTimeoutSeconds: 0, // 0 = 不限制
      maxToolRounds: 30,
      previewMaxRestarts: 3, // 与 preview_tools.py 的 DEFAULT_MAX_RESTARTS 同值；库里没这行时表单的落点
      historyWindow: 40,
      openFolderMode: 'web', // 没配过这个键时的默认：一律弹内置文件管理器，本机直开属于显式选择（ADR-0009）
      clarifyEnabled: true, // 需求澄清模式默认开（ADR-0032）：库里没这行时后端也按开处理
      askRemindAt: 6, // 与 clarify.py 的 DEFAULT_REMIND_AT 同值；0=不提醒
      // 下面每一项都必须是后端那份代码默认值的副本：库里没这行时 loadConfig 不覆盖表单，
      // 表单值就是保存出去的值，抄错等于用一次保存把默认值改掉。
      autoCompactChars: 50000, // loop.py 阈值，0=关闭自动压缩
      compactMinNew: 6,
      compactKeepRecent: 8, // 后端按 rows > keep 判定，0 会让压缩摘不到头，最小给 1
      steeringQueueMax: 20, // 执行中插话条数上限，0=禁用插话
      previewWaitSeconds: 240, // 0..900
      previewLogLines: 80, // 10..300
      previewConfirmRunning: true, // 中断运行中的预览要先确认
      browserMaxPerRun: 6, // 0..100，0=不限
      browserMaxStepsPerRun: 12, // 0..200，0=不限
      browserTimeoutSeconds: 45, // 10..120
      browserExpectTimeoutMs: 3000, // 500..8000
      browserSettleMs: 1200, // 0..5000
      browserInteractiveMax: 40, // 1..200
      webMaxFetchesPerRun: 20, // 0..200，0=不限；与 web_tools.py 的 DEFAULT_MAX_FETCHES 同值
      dbRowsLimit: 200, // 10..1000
      dbRowsAssertMax: 200, // 1..1000
      memoryEnabled: true,
      memoryTopK: 5, // 1..100
      memoryMinSimilarity: -1, // -1..1，-1=一条不因阈值丢掉
      memoryMaxTokens: 600, // 1..20000
      subrunMaxTasks: 5, // 1..8，与 subrun.py 的 DEFAULT_MAX_TASKS 同值
      subrunMaxRounds: 10, // 0..50，0=不限，与 subrun.py 的 DEFAULT_MAX_ROUNDS 同值
    },
    saving: false,
    loaded: false, // 是否已从后端拉到过配置（ensureLoaded 的去重依据）
    // 账号级前缀清单，来自 GET /api/config/meta（后端 USER_PREFIXES 是唯一真源）。
    // 空数组 = 还没拉到 = 前端不做任何猜测，整批发出去由后端裁决；拉到之后才按键分「可编辑 / 只读」。
    // 刻意不在这里抄一份前缀常量：抄的那份与后端分叉时，症状是普通账号一点保存就整批 403。
    userPrefixes: [],
  }),
  getters: {
    /** 这个配置键当前调用者可写吗（ADR-0068：平台级键归管理员，`quota.` 也归管理员）。 */
    canEditKey: (state) => (key) => {
      if (!key || !state.userPrefixes.length) return true   // 未知清单：不猜，交给后端
      if (key.startsWith('quota.')) return false            // 形态是账号级、语义是治理权
      return state.userPrefixes.some((p) => key.startsWith(p))
    },
    /** 表单字段对当前账号是否只读。管理员恒可写（他改平台键就是改平台默认）。 */
    fieldLocked() {
      return (field) => {
        if (useAuthStore().user?.is_founder) return false
        return !this.canEditKey(FIELD_TO_KEY[field])
      }
    },
  },
  actions: {
    async loadConfig() {
      const data = (await getConfig()).data
      for (const [field, key] of Object.entries(FIELD_TO_KEY)) {
        if (data[key] !== undefined) this.form[field] = data[key]
      }
      // 与配置同批拉清单：设置页要么两者都新，要么都不过滤，不会按一份过期清单置灰
      try {
        this.userPrefixes = (await configMeta()).data.user_prefixes || []
      } catch {
        this.userPrefixes = []   // 辅助请求失败不该锁死设置页：退回「不过滤，由后端裁决」
      }
      this.loaded = true
    },
    // 聊天页也要认这个配置，但没人会在打开文件夹前先访问设置页：
    // 按需拉一次（拿到过就不再打接口），保存过直接用内存里的新值 —— 改值即时生效。
    async ensureLoaded() {
      if (!this.loaded) await this.loadConfig()
    },
    async save() {
      this.saving = true
      try {
        const payload = {}
        for (const [field, key] of Object.entries(FIELD_TO_KEY)) {
          // 只读键不发送：后端对越权批次是**整批 403**（不做半写），夹带一个平台键会连同
          // 用户自己那几项改动一起失败——那是最让人摸不着头脑的报错方式。
          if (this.fieldLocked(field)) continue
          payload[key] = this.form[field]
        }
        // 掩码密钥（sk-***）由后端过滤，不会覆盖真实 api_key
        await putConfig(payload)
      } finally {
        this.saving = false
      }
    },
  },
})
