// @vitest-environment happy-dom
// 侧栏宽度/收起状态的纯逻辑契约：默认值、上下限、中栏保底、双击复位与本地记忆。
// 直接测 composable，组件只负责接线（ChatView.test.js 钉接线）。
import { describe, it, expect, vi, afterEach } from 'vitest'
import { mount, enableAutoUnmount } from '@vue/test-utils'
import { PANELS_STORAGE_KEY, useChatSidePanels } from './useChatSidePanels'

enableAutoUnmount(afterEach)

function fakeStorage(initial) {
  const map = new Map(Object.entries(initial || {}))
  return {
    getItem: vi.fn((key) => (map.has(key) ? map.get(key) : null)),
    setItem: vi.fn((key, value) => map.set(key, value)),
    dump: () => JSON.parse(map.get(PANELS_STORAGE_KEY) || 'null'),
  }
}

function stored(values) {
  return { [PANELS_STORAGE_KEY]: JSON.stringify(values) }
}

function mountPanels(options = {}) {
  let api
  mount({
    setup() {
      api = useChatSidePanels(options)
      return () => null
    },
  })
  return api
}

const viewport = { width: 1400 }
const viewportWidth = () => viewport.width

function pointer(type, clientX) {
  window.dispatchEvent(new MouseEvent(type, { clientX }))
}

afterEach(() => {
  vi.useRealTimers()
  viewport.width = 1400
})

describe('初始状态与本地记忆', () => {
  it('没有存储时：左 260 / 右 300，两侧都展开', () => {
    const api = mountPanels({ storage: fakeStorage(), getViewportWidth: viewportWidth })
    expect(api.leftWidth.value).toBe(260)
    expect(api.rightWidth.value).toBe(300)
    expect(api.leftCollapsed.value).toBe(false)
    expect(api.rightCollapsed.value).toBe(false)
  })

  it('恢复存储里的宽度与收起状态', () => {
    const api = mountPanels({
      storage: fakeStorage(stored({ leftWidth: 340, rightWidth: 480, leftCollapsed: true, rightCollapsed: true })),
      getViewportWidth: viewportWidth,
    })
    expect(api.leftWidth.value).toBe(340)
    expect(api.rightWidth.value).toBe(480)
    expect(api.leftCollapsed.value).toBe(true)
    expect(api.rightCollapsed.value).toBe(true)
  })

  it('坏存储：越界宽度收回区间、非 true 的收起不生效、坏 JSON 回默认', () => {
    const clamped = mountPanels({
      storage: fakeStorage(stored({ leftWidth: 9999, rightWidth: 'wide', leftCollapsed: 'yes', rightCollapsed: 1 })),
      getViewportWidth: viewportWidth,
    })
    expect(clamped.leftWidth.value).toBe(420)
    expect(clamped.rightWidth.value).toBe(300)
    expect(clamped.leftCollapsed.value).toBe(false)
    expect(clamped.rightCollapsed.value).toBe(false)

    const broken = mountPanels({
      storage: fakeStorage({ [PANELS_STORAGE_KEY]: '{not json' }),
      getViewportWidth: viewportWidth,
    })
    expect(broken.leftWidth.value).toBe(260)
    expect(broken.rightWidth.value).toBe(300)
  })
})

describe('拖拽调宽', () => {
  it('左栏随指针右移变宽，松手后写入存储', () => {
    const storage = fakeStorage()
    const api = mountPanels({ storage, getViewportWidth: viewportWidth })

    api.startDrag('left', { button: 0, clientX: 300 })
    expect(api.dragging.value).toBe('left')
    pointer('pointermove', 360)
    expect(api.leftWidth.value).toBe(320)

    pointer('pointerup', 360)
    expect(api.dragging.value).toBe(null)
    expect(storage.dump().leftWidth).toBe(320)
  })

  it('右栏随指针左移变宽', () => {
    const api = mountPanels({ storage: fakeStorage(), getViewportWidth: viewportWidth })

    api.startDrag('right', { button: 0, clientX: 900 })
    pointer('pointermove', 850)
    expect(api.rightWidth.value).toBe(350)

    pointer('pointerup', 850)
  })

  it('上下限：左栏最宽 420、最窄 200', () => {
    const api = mountPanels({ storage: fakeStorage(), getViewportWidth: viewportWidth })

    api.startDrag('left', { button: 0, clientX: 300 })
    pointer('pointermove', 1000)
    expect(api.leftWidth.value).toBe(420)
    pointer('pointermove', -400)
    expect(api.leftWidth.value).toBe(200)
    pointer('pointerup', -400)
  })

  it('中栏保底 480：右栏展开时，左栏向右拖到 260 就停住', () => {
    viewport.width = 1040
    const api = mountPanels({ storage: fakeStorage(), getViewportWidth: viewportWidth })

    api.startDrag('left', { button: 0, clientX: 300 })
    pointer('pointermove', 600)
    expect(api.leftWidth.value).toBe(260)
    pointer('pointerup', 600)
  })

  it('已收起的侧栏不占位：左栏收起后右栏能拖到 560', () => {
    viewport.width = 1200
    const api = mountPanels({
      storage: fakeStorage(stored({ leftCollapsed: true, rightWidth: 300 })),
      getViewportWidth: viewportWidth,
    })

    api.startDrag('right', { button: 0, clientX: 900 })
    pointer('pointermove', 300)
    expect(api.rightWidth.value).toBe(560)
    pointer('pointerup', 300)
  })

  it('收起态不响应拖拽；非主键按下也不进入拖拽', () => {
    const api = mountPanels({
      storage: fakeStorage(stored({ leftCollapsed: true })),
      getViewportWidth: viewportWidth,
    })

    api.startDrag('left', { button: 0, clientX: 300 })
    expect(api.dragging.value).toBe(null)
    pointer('pointermove', 500)
    expect(api.leftWidth.value).toBe(260)

    api.startDrag('right', { button: 2, clientX: 900 })
    expect(api.dragging.value).toBe(null)
  })
})

describe('双击复位与收起', () => {
  it('双击复位回默认宽并写入存储', () => {
    const storage = fakeStorage(stored({ leftWidth: 400, rightWidth: 520 }))
    const api = mountPanels({ storage, getViewportWidth: viewportWidth })

    api.resetWidth('left')
    expect(api.leftWidth.value).toBe(260)
    api.resetWidth('right')
    expect(api.rightWidth.value).toBe(300)
    expect(storage.dump().leftWidth).toBe(260)
    expect(storage.dump().rightWidth).toBe(300)
  })

  it('视口不够时复位退到中栏保底允许的最大宽', () => {
    viewport.width = 900
    const api = mountPanels({ storage: fakeStorage(), getViewportWidth: viewportWidth })

    api.resetWidth('left')
    expect(api.leftWidth.value).toBe(200)
  })

  it('收起/展开各自写入存储', () => {
    const storage = fakeStorage()
    const api = mountPanels({ storage, getViewportWidth: viewportWidth })

    api.toggleLeft()
    expect(api.leftCollapsed.value).toBe(true)
    expect(storage.dump().leftCollapsed).toBe(true)

    api.toggleRight()
    expect(api.rightCollapsed.value).toBe(true)
    expect(storage.dump().rightCollapsed).toBe(true)

    api.toggleLeft()
    expect(storage.dump().leftCollapsed).toBe(false)
  })
})

describe('存储不可用', () => {
  it('读取抛错按默认，写入抛错也不打断拖拽与收起', () => {
    const broken = {
      getItem: vi.fn(() => { throw new Error('denied') }),
      setItem: vi.fn(() => { throw new Error('denied') }),
    }
    const api = mountPanels({ storage: broken, getViewportWidth: viewportWidth })
    expect(api.leftWidth.value).toBe(260)

    expect(() => api.toggleLeft()).not.toThrow()
    expect(api.leftCollapsed.value).toBe(true)
  })
})
