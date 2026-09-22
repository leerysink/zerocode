// 应用注册表 + 壁纸定义（图标为内联 SVG，无需外部资源）
import { logo } from '../../assets/icons'
import NotepadApp from '../apps/NotepadApp.vue'
import CalculatorApp from '../apps/CalculatorApp.vue'
import ExplorerApp from '../apps/ExplorerApp.vue'
import SettingsApp from '../apps/SettingsApp.vue'
import WelcomeApp from '../apps/WelcomeApp.vue'

export const WALLPAPERS = {
  bloom: {
    name: 'Bloom（浅色）',
    css: `radial-gradient(120% 120% at 22% 18%, #6f8bd6 0%, #3f5fa8 35%, #16294f 72%, #0a1428 100%),
          conic-gradient(from 200deg at 60% 55%, rgba(255,255,255,.14), rgba(96,164,255,.22), rgba(198,120,255,.18), rgba(255,255,255,.14))`,
    dark: true,
  },
  night: {
    name: '午夜蓝',
    css: `radial-gradient(130% 110% at 70% 10%, #12305e 0%, #0a1a33 45%, #05070f 100%)`,
    dark: true,
  },
  flow: {
    name: '流光',
    css: `linear-gradient(120deg, #0f7b9b 0%, #23b5a3 32%, #a3e05c 66%, #f6d743 100%)`,
    dark: false,
  },
  sunset: {
    name: '暖阳',
    css: `radial-gradient(110% 110% at 30% 80%, #ffd479 0%, #ff9a52 38%, #e0466f 72%, #43134a 100%)`,
    dark: true,
  },
  graphite: {
    name: '石墨',
    css: `linear-gradient(160deg, #3b4148 0%, #1c1f23 60%, #101215 100%)`,
    dark: true,
  },
}

export const APPS = {
  welcome: {
    id: 'welcome',
    name: '欢迎使用',
    short: '陌如玉',
    icon: logo.windows,
    comp: WelcomeApp,
    size: { w: 720, h: 520 },
    desktop: false,
    category: '系统',
  },
  explorer: {
    id: 'explorer',
    name: '文件资源管理器',
    short: '文件资源管理器',
    icon: logo.explorer,
    comp: ExplorerApp,
    size: { w: 940, h: 600 },
    desktop: true,
    pinned: true,
    category: '系统',
  },
  notepad: {
    id: 'notepad',
    name: '记事本 · 便签',
    short: '记事本',
    icon: logo.notepad,
    comp: NotepadApp,
    size: { w: 860, h: 580 },
    desktop: true,
    pinned: true,
    category: '办公',
  },
  calc: {
    id: 'calc',
    name: '计算器',
    short: '计算器',
    icon: logo.calc,
    comp: CalculatorApp,
    size: { w: 372, h: 560 },
    desktop: true,
    pinned: true,
    min: { w: 340, h: 480 },
    category: '实用工具',
  },
  settings: {
    id: 'settings',
    name: '设置',
    short: '设置',
    icon: logo.settings,
    comp: SettingsApp,
    size: { w: 960, h: 640 },
    desktop: true,
    pinned: true,
    category: '系统',
  },
  thisPC: {
    id: 'thisPC',
    name: '此电脑',
    short: '此电脑',
    icon: logo.pc,
    comp: ExplorerApp,
    props: { path: '此电脑' },
    size: { w: 940, h: 600 },
    desktop: true,
    category: '系统',
  },
  recycle: {
    id: 'recycle',
    name: '回收站',
    short: '回收站',
    icon: logo.recycle,
    comp: ExplorerApp,
    props: { path: '回收站' },
    size: { w: 820, h: 520 },
    desktop: true,
    category: '系统',
  },
}

export const appList = () => Object.values(APPS)
export const appOf = (id) => APPS[id]
export const desktopApps = () => appList().filter((a) => a.desktop)
export const pinnedApps = () => appList().filter((a) => a.pinned)

export const ACCENTS = [
  '#0078d4', '#0099bc', '#00b294', '#498205', '#7a7500',
  '#ca5010', '#c239b3', '#8764b8', '#e3008c', '#69797e',
]
