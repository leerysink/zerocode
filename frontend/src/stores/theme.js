// 控制台主题 store：唯一真源（ADR-0055）。状态取 utils/theme 纯函数读数（含旧键一次性继承），
// toggle 写存储并落到 <html data-theme>；跨标签页经 storage 事件同步，只认新键。
// 组件只读本 store，不各自读 localStorage。
import { defineStore } from 'pinia'
import {
  CONSOLE_THEME_KEY, nextConsoleTheme, normalizeConsoleTheme, readConsoleTheme, writeConsoleTheme,
} from '../utils/theme'

export function applyTheme(theme) {
  document.documentElement.dataset.theme = theme
}

export const useThemeStore = defineStore('theme', {
  state: () => ({ theme: readConsoleTheme() }),
  actions: {
    toggle() {
      this.theme = nextConsoleTheme(this.theme)
      writeConsoleTheme(this.theme)
      applyTheme(this.theme)
    },
    syncFromStorage(event) {
      if (event.key !== CONSOLE_THEME_KEY) return
      this.theme = normalizeConsoleTheme(event.newValue)
      applyTheme(this.theme)
    },
  },
})