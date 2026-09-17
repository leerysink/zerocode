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
    },
    saving: false,
  }),
  actions: {
    async loadConfig() {
      const data = (await getConfig()).data
      for (const [field, key] of Object.entries(FIELD_TO_KEY)) {
        if (data[key] !== undefined) this.form[field] = data[key]
      }
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
