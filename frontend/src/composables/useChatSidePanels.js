// 聊天页左右侧栏的宽度与收起状态：拖拽、双击复位、浏览器本地记忆。
// 记忆只落 localStorage，不落后端（与查询页签同一策略）。
import { onBeforeUnmount, ref } from 'vue'

export const LEFT_PANEL = { defaultWidth: 260, minWidth: 200, maxWidth: 420 }
export const RIGHT_PANEL = { defaultWidth: 300, minWidth: 240, maxWidth: 560 }
export const CENTER_MIN_WIDTH = 480
export const PANELS_STORAGE_KEY = 'zc.chat.side-panels.v1'

const clamp = (value, min, max) => Math.min(Math.max(value, min), max)

function browserStorage() {
  try {
    return window.localStorage
  } catch {
    return null
  }
}

function readSaved(storage) {
  try {
    const raw = storage?.getItem(PANELS_STORAGE_KEY)
    if (!raw) return {}
    const data = JSON.parse(raw)
    return data && typeof data === 'object' ? data : {}
  } catch {
    return {}
  }
}

function savedWidth(value, panel) {
  return typeof value === 'number' && Number.isFinite(value)
    ? clamp(value, panel.minWidth, panel.maxWidth)
    : panel.defaultWidth
}

export function useChatSidePanels(options = {}) {
  const storage = options.storage ?? browserStorage()
  const getViewportWidth = options.getViewportWidth ?? (() => window.innerWidth)
  const centerMinWidth = options.centerMinWidth ?? CENTER_MIN_WIDTH

  const saved = readSaved(storage)
  const leftWidth = ref(savedWidth(saved.leftWidth, LEFT_PANEL))
  const rightWidth = ref(savedWidth(saved.rightWidth, RIGHT_PANEL))
  const leftCollapsed = ref(saved.leftCollapsed === true)
  const rightCollapsed = ref(saved.rightCollapsed === true)
  const dragging = ref(null)

  const PANELS = { left: LEFT_PANEL, right: RIGHT_PANEL }

  function persist() {
    try {
      storage?.setItem(PANELS_STORAGE_KEY, JSON.stringify({
        leftWidth: leftWidth.value,
        rightWidth: rightWidth.value,
        leftCollapsed: leftCollapsed.value,
        rightCollapsed: rightCollapsed.value,
      }))
    } catch {
      // 隐私模式/配额满：记忆失效不影响拖拽与收起
    }
  }

  // 另一侧收起时不再占位，拖拽上限只算真正占宽的侧栏
  function occupiedOther(side) {
    return side === 'left'
      ? (rightCollapsed.value ? 0 : rightWidth.value)
      : (leftCollapsed.value ? 0 : leftWidth.value)
  }

  function roomFor(side) {
    return getViewportWidth() - occupiedOther(side) - centerMinWidth
  }

  // 拖拽上限 = 固定上限与「中栏至少 centerMinWidth」取小；已超限的当前宽不被强行压回
  function dragMax(side, startWidth) {
    return Math.max(startWidth, Math.min(PANELS[side].maxWidth, roomFor(side)))
  }

  // 双击复位不受当前宽限制，但仍不把中栏压到保底以下（视口实在不够时退到最小宽）
  function resetMax(side) {
    const panel = PANELS[side]
    return Math.max(panel.minWidth, Math.min(panel.maxWidth, roomFor(side)))
  }

  let endDrag = null

  function startDrag(side, event) {
    if (event.button !== 0) return
    if (side === 'left' ? leftCollapsed.value : rightCollapsed.value) return
    endDrag?.() // 上一条拖拽没正常收尾（指针在窗口外松开）时先清掉旧监听
    const panel = PANELS[side]
    const startX = event.clientX
    const startWidth = side === 'left' ? leftWidth.value : rightWidth.value
    // 指针捕获：拖出窗口再松手也能收到 pointerup，不会卡在拖拽态
    const capture = event.currentTarget
    const pointerId = event.pointerId
    try {
      capture?.setPointerCapture?.(pointerId)
    } catch {
      /* 不支持指针捕获的环境退化为窗口监听 */
    }
    dragging.value = side
    document.body.classList.add('zc-panel-resizing')

    const onMove = (moveEvent) => {
      const delta = moveEvent.clientX - startX
      const next = clamp(
        side === 'left' ? startWidth + delta : startWidth - delta,
        panel.minWidth,
        dragMax(side, startWidth),
      )
      if (side === 'left') leftWidth.value = next
      else rightWidth.value = next
    }
    const onEnd = () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onEnd)
      window.removeEventListener('pointercancel', onEnd)
      window.removeEventListener('blur', onEnd)
      try {
        capture?.releasePointerCapture?.(pointerId)
      } catch {
        /* 捕获已丢失时忽略 */
      }
      document.body.classList.remove('zc-panel-resizing')
      dragging.value = null
      endDrag = null
      persist()
    }
    endDrag = onEnd
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onEnd)
    window.addEventListener('pointercancel', onEnd)
    window.addEventListener('blur', onEnd)
  }

  function resetWidth(side) {
    const panel = PANELS[side]
    const width = clamp(panel.defaultWidth, panel.minWidth, resetMax(side))
    if (side === 'left') leftWidth.value = width
    else rightWidth.value = width
    persist()
  }

  function toggleLeft() {
    leftCollapsed.value = !leftCollapsed.value
    persist()
  }

  function toggleRight() {
    rightCollapsed.value = !rightCollapsed.value
    persist()
  }

  onBeforeUnmount(() => endDrag?.())

  return {
    leftWidth,
    rightWidth,
    leftCollapsed,
    rightCollapsed,
    dragging,
    startDrag,
    resetWidth,
    toggleLeft,
    toggleRight,
  }
}
