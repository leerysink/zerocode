// 控制台主题（ADR-0055 统一管辖）：默认浅色，只认精确的 'dark'，其余（未设置、大写、带空格、脏值）
// 一律浅色。storage 可注入，纯函数测试不需要挂 localStorage；存储不可用（隐私模式/配额满）时静默降级。
// THEME_KEY 是工作台旧键——独立主题已退役，它只在首读继承时被读一次，永不写。

export const THEME_KEY = 'zc.workbench.theme.v1'
export const DEFAULT_CONSOLE_THEME = 'light'
export const CONSOLE_THEME_KEY = 'zc.console.theme.v1'

export function normalizeConsoleTheme(value) {
  return value === 'dark' ? 'dark' : DEFAULT_CONSOLE_THEME
}

export function readConsoleTheme(storage) {
  try {
    const s = storage ?? globalThis.localStorage
    const raw = s.getItem(CONSOLE_THEME_KEY)
    if (raw !== null) return normalizeConsoleTheme(raw)
    // 一次性继承（US-14）：新键缺席时旧工作台键的合法显式值成为全局初值；
    // 写入新键后旧键永不再读。继承写失败不影响本次读数（内存里仍生效）。
    const legacy = s.getItem(THEME_KEY)
    if (legacy === 'light' || legacy === 'dark') {
      try { s.setItem(CONSOLE_THEME_KEY, legacy) } catch { /* 记不住就算了 */ }
      return legacy
    }
    return DEFAULT_CONSOLE_THEME
  } catch {
    return DEFAULT_CONSOLE_THEME
  }
}

export function writeConsoleTheme(value, storage) {
  try {
    const s = storage ?? globalThis.localStorage
    s.setItem(CONSOLE_THEME_KEY, normalizeConsoleTheme(value))
    return true
  } catch {
    return false
  }
}

export function nextConsoleTheme(theme) {
  return normalizeConsoleTheme(theme) === 'dark' ? 'light' : 'dark'
}
