// 桌面图标布局与选中状态（布局写入数据库设置 iconLayout：{ appId: {x, y} }）
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { desktopApps } from '../desktop/registry/apps'
import { useSettings } from './settings'

const CELL_W = 96
const CELL_H = 108
const PAD = 12

export const useIcons = defineStore('icons', () => {
  const selected = ref([])
  const ghost = ref({}) // 拖拽中的实时位置（仅内存）

  function gridPos(i) {
    const rowsPerCol = Math.max(1, Math.floor((window.innerHeight - 160) / CELL_H))
    const col = Math.floor(i / rowsPerCol)
    const row = i % rowsPerCol
    return { x: PAD + col * CELL_W, y: PAD + row * CELL_H }
  }

  const positions = computed(() => {
    const st = useSettings()
    const layout = st.cfg.iconLayout || {}
    const map = {}
    desktopApps().forEach((app, i) => {
      const base = ghost.value[app.id] || layout[app.id] || gridPos(i)
      map[app.id] = base
    })
    return map
  })

  // 拖拽过程中只改内存，松手后一次性写入数据库
  function preview(key, pos) {
    ghost.value = { ...ghost.value, [key]: pos }
  }

  function commit(key) {
    const pos = ghost.value[key]
    if (pos) save(key, pos)
    const next = { ...ghost.value }
    delete next[key]
    ghost.value = next
  }

  function save(key, pos) {
    const st = useSettings()
    const layout = { ...(st.cfg.iconLayout || {}), [key]: pos }
    st.patch({ iconLayout: layout })
  }

  function move(key, pos) {
    save(key, { x: Math.max(0, pos.x), y: Math.max(0, pos.y) })
  }

  function resetLayout() {
    const st = useSettings()
    st.patch({ iconLayout: {} })
    selected.value = []
  }

  // 按当前布局自动排列（贴回网格）
  function autoArrange() {
    const list = desktopApps().map((a) => a.id)
    list.sort((a, b) => {
      const pa = positions.value[a]
      const pb = positions.value[b]
      return pa.y - pb.y || pa.x - pb.x
    })
    const out = {}
    list.forEach((id, i) => (out[id] = gridPos(i)))
    const st = useSettings()
    st.patch({ iconLayout: out })
  }

  function select(ids, additive = false) {
    selected.value = additive ? Array.from(new Set([...selected.value, ...ids])) : ids
  }

  function clear() {
    selected.value = []
  }

  return { selected, ghost, positions, preview, commit, move, resetLayout, autoArrange, select, clear }
})
