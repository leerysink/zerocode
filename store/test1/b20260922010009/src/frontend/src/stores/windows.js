// 窗口管理器：打开/关闭/最小化/最大化/聚焦/层级/多实例，位置尺寸变化由 Window.vue 回写
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { appOf } from '../desktop/registry/apps'

const TASKBAR_H = 48

export const useWinStore = defineStore('windows', () => {
  const wins = ref([])
  const topZ = ref(10)
  const activeId = ref('')
  let seq = 0

  const activeWin = computed(() => wins.value.find((w) => w.winId === activeId.value) || null)
  const visibleWins = computed(() =>
    wins.value.filter((w) => !w.minimized).sort((a, b) => a.z - b.z),
  )
  // 任务栏按应用分组
  const groups = computed(() => {
    const map = new Map()
    for (const w of wins.value) {
      if (!map.has(w.appId)) map.set(w.appId, [])
      map.get(w.appId).push(w)
    }
    return map
  })

  function stage() {
    return { w: window.innerWidth, h: window.innerHeight - TASKBAR_H }
  }

  function focus(winId) {
    const w = wins.value.find((i) => i.winId === winId)
    if (!w) return
    topZ.value += 1
    w.z = topZ.value
    w.minimized = false
    activeId.value = winId
  }

  function open(appId, props = {}) {
    const app = appOf(appId)
    if (!app) return null
    const s = stage()
    const w = Math.min(app.size?.w || 900, Math.max(360, s.w - 60))
    const h = Math.min(app.size?.h || 560, Math.max(240, s.h - 40))
    const offset = (wins.value.length % 7) * 26
    seq += 1
    const win = {
      winId: `w${seq}`,
      appId,
      props: { ...(app.props || {}), ...props },
      title: app.name,
      x: Math.max(8, Math.round((s.w - w) / 2) + offset - 60),
      y: Math.max(8, Math.round((s.h - h) / 2) + offset - 40),
      w,
      h,
      minW: app.min?.w || 340,
      minH: app.min?.h || 200,
      z: (topZ.value += 1),
      minimized: false,
      maximized: false,
      restore: null,
      born: Date.now(),
    }
    wins.value.push(win)
    activeId.value = win.winId
    return win
  }

  function close(winId) {
    const i = wins.value.findIndex((w) => w.winId === winId)
    if (i < 0) return
    wins.value.splice(i, 1)
    if (activeId.value === winId) {
      const rest = visibleWins.value.sort((a, b) => b.z - a.z)
      activeId.value = rest[0]?.winId || ''
    }
  }

  function closeApp(appId) {
    wins.value.filter((w) => w.appId === appId).forEach((w) => close(w.winId))
  }

  function minimize(winId) {
    const w = wins.value.find((i) => i.winId === winId)
    if (!w) return
    w.minimized = true
    if (activeId.value === winId) {
      const rest = visibleWins.value.filter((i) => i.winId !== winId).sort((a, b) => b.z - a.z)
      activeId.value = rest[0]?.winId || ''
    }
  }

  function toggleMax(winId) {
    const w = wins.value.find((i) => i.winId === winId)
    if (!w) return
    const s = stage()
    if (w.maximized) {
      const r = w.restore || { x: 60, y: 40, w: 900, h: 560 }
      Object.assign(w, r, { maximized: false })
      w.restore = null
    } else {
      w.restore = { x: w.x, y: w.y, w: w.w, h: w.h }
      Object.assign(w, { x: 0, y: 0, w: s.w, h: s.h, maximized: true })
    }
    focus(winId)
  }

  // 任务栏点击：聚焦 / 最小化 / 唤起
  function taskbarClick(appId) {
    const list = groups.value.get(appId) || []
    if (!list.length) return open(appId)
    if (list.length > 1) {
      const hidden = list.filter((w) => w.minimized)
      if (hidden.length) {
        hidden.forEach((w) => (w.minimized = false))
        focus(hidden[hidden.length - 1].winId)
      } else {
        list.forEach((w) => minimize(w.winId))
      }
      return
    }
    const w = list[0]
    if (w.minimized) focus(w.winId)
    else if (activeId.value === w.winId) minimize(w.winId)
    else focus(w.winId)
  }

  function minimizeAll() {
    const anyVisible = wins.value.some((w) => !w.minimized)
    wins.value.forEach((w) => (w.minimized = anyVisible))
    if (anyVisible) activeId.value = ''
  }

  function update(winId, part) {
    const w = wins.value.find((i) => i.winId === winId)
    if (w) Object.assign(w, part)
  }

  // 窗口在视口变化时收进可见区域
  function clampAll() {
    const s = stage()
    wins.value.forEach((w) => {
      if (w.maximized) {
        Object.assign(w, { x: 0, y: 0, w: s.w, h: s.h })
        return
      }
      w.w = Math.min(w.w, s.w)
      w.h = Math.min(w.h, s.h)
      w.x = Math.min(Math.max(0, w.x), Math.max(0, s.w - w.w))
      w.y = Math.min(Math.max(0, w.y), Math.max(0, s.h - 40))
    })
  }

  return {
    wins, activeId, activeWin, visibleWins, groups,
    open, close, closeApp, focus, minimize, minimizeAll, toggleMax, taskbarClick, update, clampAll,
  }
})
