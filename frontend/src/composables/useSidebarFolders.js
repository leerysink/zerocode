// 聊天侧栏「文件夹」（=应用）的折叠状态与手工顺序：只落 localStorage，不落后端。
// 与侧栏宽度/收起同一策略（见 useChatSidePanels）——属本机浏览器的现场，换设备不必跟随。
// 顺序与折叠都按 app.id 记（文件夹行本身就是一个应用），不是 workspace_id。
import { onBeforeUnmount, ref } from 'vue'

export const FOLDERS_STORAGE_KEY = 'zc.chat.sidebar-folders.v1'
export const DRAG_THRESHOLD = 4 // px：位移超过这个量才算拖拽，否则按点击折叠处理

function browserStorage() {
  try {
    return window.localStorage
  } catch {
    return null
  }
}

function readSaved(storage) {
  try {
    const raw = storage?.getItem(FOLDERS_STORAGE_KEY)
    if (!raw) return {}
    const data = JSON.parse(raw)
    return data && typeof data === 'object' ? data : {}
  } catch {
    return {}
  }
}

const saneIds = (value) => (Array.isArray(value) ? value.filter((x) => Number.isInteger(x)) : [])

const sameIds = (a, b) => a.length === b.length && a.every((id, i) => id === b[i])

// 槽位 = 第一条「指针还没越过其中线」的组；越过所有中线即落到最后一格之后。
// 拖动过程中组的位置一路在变，落点必须拿起拖那一刻的几何快照判定，否则会来回抖，
// 所以 rects 与 ids 都在 startDrag 里冻结。slot 是「含自己」的槽位，去掉自己那一格才是插入点。
function slotOf(rects, y) {
  for (let i = 0; i < rects.length; i += 1) {
    if (y < rects[i].top + rects[i].height / 2) return i
  }
  return rects.length
}

export function useSidebarFolders(options = {}) {
  const storage = options.storage ?? browserStorage()
  const getGroupRects = options.getGroupRects ?? (() => [])

  const saved = readSaved(storage)
  const order = ref(saneIds(saved.order))
  const foldedIds = ref(saneIds(saved.folded))
  const draggingId = ref(null)
  // 拖完浏览器仍会在组头上补一次 click，不挡掉就会把「拖拽」当成「点击折叠」再翻一次。
  // 不在 pointerup 里复位：松手在组外时 click 根本不来，标记留在 true 也只多挡一次空点击。
  const wasDragged = ref(false)

  let endDrag = null

  function persist() {
    try {
      storage?.setItem(FOLDERS_STORAGE_KEY, JSON.stringify({ order: order.value, folded: foldedIds.value }))
    } catch {
      // 隐私模式/配额满：记忆失效，折叠与拖拽照常
    }
  }

  // 排过序的按存的样子走；没见过的（刚建好的应用）按服务端顺序顶到最前——
  // 与「新建的在最上面」的既有直觉一致，也不会因为存了张旧表就把新应用藏到最后。
  function arrange(apps) {
    const ids = apps.map((a) => a.id)
    const known = order.value.filter((id) => ids.includes(id))
    const placed = new Set(known)
    return [...ids.filter((id) => !placed.has(id)), ...known]
  }

  function applyOrder(apps) {
    const byId = new Map(apps.map((a) => [a.id, a]))
    return arrange(apps).map((id) => byId.get(id)).filter(Boolean)
  }

  // 拉完应用后把 order/folded 归一成真实存在的应用（补新、去已删的），变了才落盘。
  function syncOrder(apps) {
    const next = arrange(apps)
    const ids = new Set(next)
    const nextFolded = foldedIds.value.filter((id) => ids.has(id))
    const changed = !sameIds(next, order.value) || !sameIds(nextFolded, foldedIds.value)
    order.value = next
    foldedIds.value = nextFolded
    if (changed) persist()
  }

  const isFolded = (id) => foldedIds.value.includes(id)

  function setFolded(id, value) {
    const at = foldedIds.value.indexOf(id)
    if (value && at < 0) foldedIds.value = [...foldedIds.value, id]
    else if (!value && at >= 0) foldedIds.value = foldedIds.value.filter((x) => x !== id)
    persist()
  }

  function toggleFold(id) {
    setFolded(id, !isFolded(id))
  }

  // 按住组头上下拖：拖动中只改内存里的 order（实时换位预览），松手才落盘。
  function startDrag(event, draggedId) {
    if (event.button !== 0) return
    wasDragged.value = false // 每次按下重新判定：只有真拖起来才会再置回 true
    const ids = order.value.slice()
    const rects = getGroupRects()
    const from = ids.indexOf(draggedId)
    if (from < 0 || rects.length !== ids.length) return
    const startY = event.clientY
    let at = from
    let moved = false

    const onMove = (moveEvent) => {
      if (!moved && Math.abs(moveEvent.clientY - startY) < DRAG_THRESHOLD) return
      if (!moved) {
        moved = true
        wasDragged.value = true
        draggingId.value = draggedId
        document.body.classList.add('zc-folder-dragging')
      }
      const slot = slotOf(rects, moveEvent.clientY)
      const target = Math.min(slot > from ? slot - 1 : slot, ids.length - 1)
      if (target === at) return
      ids.splice(at, 1)
      ids.splice(target, 0, draggedId)
      at = target
      order.value = ids.slice()
    }
    const onEnd = () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onEnd)
      window.removeEventListener('pointercancel', onEnd)
      window.removeEventListener('blur', onEnd)
      document.body.classList.remove('zc-folder-dragging')
      draggingId.value = null
      if (moved) persist()
      endDrag = null
    }
    endDrag?.() // 上一条拖拽没正常收尾（指针在窗口外松手）时先清掉旧监听
    endDrag = onEnd
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onEnd)
    window.addEventListener('pointercancel', onEnd)
    window.addEventListener('blur', onEnd)
  }

  function onHeadClick(id) {
    if (wasDragged.value) return
    toggleFold(id)
  }

  onBeforeUnmount(() => endDrag?.()) // 拖到一半切走聊天页：别让 window 监听留在那儿

  return {
    order,
    draggingId,
    isFolded,
    setFolded,
    toggleFold,
    applyOrder,
    syncOrder,
    startDrag,
    onHeadClick,
  }
}
