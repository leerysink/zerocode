// 工作台主题：默认深色，只认精确的 'light'，其余（未设置、大写、带空格、脏值）一律深色。
// storage 可注入，纯函数测试不需要挂 localStorage；存储不可用（隐私模式/配额满）时静默降级。

export const DEFAULT_THEME = 'dark'
export const THEME_KEY = 'zc.workbench.theme.v1'

export function normalizeTheme(value) {
  return value === 'light' ? 'light' : DEFAULT_THEME
}

export function nextTheme(theme) {
  return normalizeTheme(theme) === 'light' ? 'dark' : 'light'
}

export function readWorkbenchTheme(storage) {
  try {
    const s = storage ?? globalThis.localStorage
    return normalizeTheme(s.getItem(THEME_KEY))
  } catch {
    return DEFAULT_THEME
  }
}

export function writeWorkbenchTheme(value, storage) {
  try {
    const s = storage ?? globalThis.localStorage
    s.setItem(THEME_KEY, normalizeTheme(value))
    return true
  } catch {
    return false
  }
}
