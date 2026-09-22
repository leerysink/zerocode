// 桌面配置：数据库持久化（/api/settings），失败时回落 localStorage，保证离线/后端未就绪仍可用
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import axios from 'axios'

const LS_KEY = '***'
const DEFAULTS = {
  theme: 'light',
  accent: '#0078d4',
  wallpaper: 'bloom',
  taskbarAlign: 'center',
  iconLayout: {},
  showDesktopIcons: true,
  brightness: 100,
  nightLight: false,
}

export const useSettings = defineStore('settings', () => {
  const cfg = ref({ ...DEFAULTS })
  const source = ref('default') // db | default | local
  const saving = ref(false)
  const lastError = ref('')

  const isDark = computed(() => cfg.value.theme === 'dark')

  let timer = null
  let queued = {}

  function applyCssVars() {
    const el = document.documentElement
    el.classList.toggle('theme-dark', cfg.value.theme === 'dark')
    el.classList.toggle('theme-light', cfg.value.theme !== 'dark')
    el.style.setProperty('--accent', cfg.value.accent)
    // 亮度（快速设置滑块）与夜间模式（暖色滤镜）作用于壁纸与桌面层
    const bright = Math.min(Math.max(Number(cfg.value.brightness) || 100, 30), 100) / 100
    el.style.setProperty('--screen-bright', String(bright))
    el.style.setProperty('--night-tint', cfg.value.nightLight ? 'rgba(255, 160, 60, 0.22)' : 'rgba(0, 0, 0, 0)')
  }

  function patch(next, persist = true) {
    cfg.value = { ...cfg.value, ...next }
    applyCssVars()
    try {
      localStorage.setItem(LS_KEY, JSON.stringify(cfg.value))
    } catch { /* 忽略隐私模式异常 */ }
    if (persist) queueSave(next)
  }

  function queueSave(part) {
    queued = { ...queued, ...part }
    if (timer) clearTimeout(timer)
    timer = setTimeout(flush, 700)
  }

  async function flush() {
    timer = null
    const body = queued
    queued = {}
    if (!Object.keys(body).length) return
    saving.value = true
    try {
      const { data } = await axios.put('/api/settings', { patch: body })
      if (data?.data) cfg.value = { ...DEFAULTS, ...data.data }
      source.value = 'db'
      lastError.value = ''
    } catch (e) {
      lastError.value = '设置已保存到本机，后端未就绪无法同步'
      source.value = 'local'
    } finally {
      saving.value = false
    }
  }

  async function load() {
    applyCssVars()
    try {
      const { data } = await axios.get('/api/settings', { timeout: 6000 })
      cfg.value = { ...DEFAULTS, ...(data?.data || {}) }
      source.value = data?.source || 'db'
    } catch {
      const raw = localStorage.getItem(LS_KEY)
      if (raw) {
        try {
          cfg.value = { ...DEFAULTS, ...JSON.parse(raw) }
          source.value = 'local'
        } catch { /* 数据损坏则用默认值 */ }
      }
    }
    applyCssVars()
  }

  async function reload() {
    await load()
  }

  return { cfg, source, saving, lastError, isDark, load, reload, patch, flush }
})
