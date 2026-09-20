// config store：系统配置加载/保存，负责表单字段 ↔ 后端配置键（app_config 表 llm.* / agent.*）的字段映射
import { defineStore } from 'pinia'
import { getConfig, putConfig } from '../api'

// 表单字段 → 后端配置键（键固定，见 Global Constraints）
const FIELD_TO_KEY = {
  baseUrl: 'llm.base_url',
  apiKey: 'llm.api_key',
  model: 'llm.model',
  writeConfirm: 'agent.write_confirm',
  askTimeoutSeconds: 'agent.ask_timeout_seconds',
  maxToolRounds: 'agent.max_tool_rounds',
  historyWindow: 'agent.history_window',
  openFolderMode: 'fs.open_folder_mode', // auto | system | web，见 utils/openFolder.js
}

export const useConfigStore = defineStore('config', {
  state: () => ({
    form: {
      baseUrl: '',
      apiKey: '',
      model: '',
      writeConfirm: false,
      askTimeoutSeconds: 0, // 0 = 不限制
      maxToolRounds: 30,
      historyWindow: 40,
      openFolderMode: 'auto', // 老库里没这个键时的默认：按访问地址自动判定
    },
    saving: false,
    loaded: false, // 是否已从后端拉到过配置（ensureLoaded 的去重依据）
  }),
  actions: {
    async loadConfig() {
      const data = (await getConfig()).data
      for (const [field, key] of Object.entries(FIELD_TO_KEY)) {
        if (data[key] !== undefined) this.form[field] = data[key]
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
        for (const [field, key] of Object.entries(FIELD_TO_KEY)) payload[key] = this.form[field]
        // 掩码密钥（sk-***）由后端过滤，不会覆盖真实 api_key
        await putConfig(payload)
      } finally {
        this.saving = false
      }
    },
  },
})
