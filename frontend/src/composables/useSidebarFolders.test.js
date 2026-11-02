// @vitest-environment happy-dom
// 侧栏「文件夹」（=应用）折叠态与手工顺序的纯逻辑契约：本地记忆的读写与清洗、
// 新/删应用对已存顺序的补齐与剔除、按住拖动的槽位换算、以及「拖完那次多余 click」的挡刀。
// 直接测 composable，DOM 几何由 getGroupRects 注入，组件只负责接线（断言见 SideBar.test.js）。
import { describe, it, expect, vi, afterEach } from 'vitest'
import { mount, enableAutoUnmount } from '@vue/test-utils'
import { FOLDERS_STORAGE_KEY, DRAG_THRESHOLD, useSidebarFolders } from './useSidebarFolders'

enableAutoUnmount(afterEach)

function fakeStorage(initial) {
  const map = new Map(Object.entries(initial || {}))
  return {
    getItem: vi.fn((key) => (map.has(key) ? map.get(key) : null)),
    setItem: vi.fn((key, value) => map.set(key, value)),
    dump: () => JSON.parse(map.get(FOLDERS_STORAGE_KEY) || 'null'),
  }
}

const stored = (values) => ({ [FOLDERS_STORAGE_KEY]: JSON.stringify(values) })

const app = (id) => ({ id, workspace_id: id * 10, name: `应用${id}`, slug: `app${id}` })
const apps = (...ids) => ids.map(app)

function mountFolders({ storage = fakeStorage(), rects = [] } = {}) {
  let api
  mount({
    setup() {
      api = useSidebarFolders({ storage, getGroupRects: () => rects })
      return () => null
    },
  })
  return { api, storage }
}

// 4 行、每行 40px 高，中线分别在 20 / 60 / 100 / 140
const fourRows = [0, 40, 80, 120].map((top) => ({ top, height: 40 }))

const down = (clientY) => ({ button: 0, clientY })
const moveTo = (clientY) => window.dispatchEvent(new MouseEvent('pointermove', { clientY }))
const release = () => window.dispatchEvent(new MouseEvent('pointerup'))

afterEach(() => {
  document.body.classList.remove('zc-folder-dragging')
})

describe('折叠状态的本地记忆', () => {
  it('没有存储时全部展开', () => {
    const { api } = mountFolders()
    expect(api.isFolded(7)).toBe(false)
  })

  it('点组头翻折叠并落盘，重新挂载后恢复', () => {
    const storage = fakeStorage()
    const first = mountFolders({ storage }).api
    first.onHeadClick(7)
    expect(first.isFolded(7)).toBe(true)
    expect(storage.dump()).toEqual({ order: [], folded: [7] })

    expect(mountFolders({ storage }).api.isFolded(7)).toBe(true)
  })

  it('展开时把 id 从 folded 里摘掉，不是塞个 false 进去', () => {
    const { api, storage } = mountFolders({ storage: fakeStorage(stored({ folded: [7, 8] })) })
    api.toggleFold(7)
    expect(storage.dump().folded).toEqual([8])
  })

  it('新会话要求「必须看得见」：setFolded(id, false) 对未折叠的组是幂等的', () => {
    const { api, storage } = mountFolders()
    api.setFolded(7, false)
    expect(api.isFolded(7)).toBe(false)
    expect(storage.dump().folded).toEqual([])
  })

  it('坏 JSON 与非整数 id 一律清洗，不炸也不记', () => {
    const broken = mountFolders({ storage: fakeStorage({ [FOLDERS_STORAGE_KEY]: '{不是json' }) })
    expect(broken.api.order.value).toEqual([])
    expect(broken.api.isFolded(7)).toBe(false)

    const dirty = mountFolders({ storage: fakeStorage(stored({ order: [7, '8', null, 9], folded: [true, 3] })) })
    expect(dirty.api.order.value).toEqual([7, 9])
    expect(dirty.api.isFolded(3)).toBe(true)
    expect(dirty.api.isFolded(7)).toBe(false)
  })
})

describe('顺序：已排的按存的走，没见过的顶到最前', () => {
  it('按存好的 order 排应用', () => {
    const { api } = mountFolders({ storage: fakeStorage(stored({ order: [9, 7, 8] })) })
    expect(api.applyOrder(apps(7, 8, 9)).map((a) => a.id)).toEqual([9, 7, 8])
  })

  it('新建的应用排到最前，跟「最新的在上面」的默认口径一致', () => {
    const { api } = mountFolders({ storage: fakeStorage(stored({ order: [7, 8] })) })
    expect(api.applyOrder(apps(9, 7, 8)).map((a) => a.id)).toEqual([9, 7, 8])
  })

  it('order 里残留的已删应用不会占位，也不会把真实应用挤掉', () => {
    const { api } = mountFolders({ storage: fakeStorage(stored({ order: [7, 66, 8] })) })
    expect(api.applyOrder(apps(8, 7)).map((a) => a.id)).toEqual([7, 8])
  })

  it('syncOrder 把新应用补进 order、剔掉已删的与已失效的折叠标记，并落一次盘', () => {
    const { api, storage } = mountFolders({ storage: fakeStorage(stored({ order: [7, 66], folded: [66, 7] })) })
    api.syncOrder(apps(9, 7))
    expect(storage.dump()).toEqual({ order: [9, 7], folded: [7] })
  })

  it('syncOrder 对没变化的列表不再写盘，避免每次刷新都刷一遍 localStorage', () => {
    const storage = fakeStorage(stored({ order: [7, 8], folded: [] }))
    const { api } = mountFolders({ storage })
    storage.setItem.mockClear()
    api.syncOrder(apps(8, 7))
    expect(storage.setItem).not.toHaveBeenCalled()
  })
})

describe('按住组头上下拖动改顺序', () => {
  it('位移没超过阈值：不算拖拽、不换位、不落盘', () => {
    const { api, storage } = mountFolders({ rects: fourRows })
    api.syncOrder(apps(1, 2, 3, 4))
    storage.setItem.mockClear()
    api.startDrag(down(60), 2)
    moveTo(60 + DRAG_THRESHOLD - 1)
    expect(api.order.value).toEqual([1, 2, 3, 4])
    expect(api.draggingId.value).toBeNull()
    release()
    expect(storage.setItem).not.toHaveBeenCalled()
  })

  it('超过阈值才起拖：拖动中实时换位，松手才落盘', () => {
    const { api, storage } = mountFolders({ rects: fourRows })
    api.syncOrder(apps(1, 2, 3, 4))
    storage.setItem.mockClear()
    api.startDrag(down(60), 2)
    moveTo(115) // 越过第 3 行中线 → 落到 index 2
    expect(api.order.value).toEqual([1, 3, 2, 4])
    expect(api.draggingId.value).toBe(2)
    expect(storage.setItem).not.toHaveBeenCalled()
    release()
    expect(storage.dump().order).toEqual([1, 3, 2, 4])
    expect(api.draggingId.value).toBeNull()
  })

  it('落点用起拖那一刻冻结的几何，一路拖到底能到最后一格', () => {
    const { api } = mountFolders({ rects: fourRows })
    api.syncOrder(apps(1, 2, 3, 4))
    api.startDrag(down(60), 2)
    moveTo(115)
    moveTo(200) // 越过所有中线
    expect(api.order.value).toEqual([1, 3, 4, 2])
    moveTo(10) // 再拖回最上面
    expect(api.order.value).toEqual([2, 1, 3, 4])
    release()
  })

  it('拖回自己原来的槽位不算改动，顺序原样', () => {
    const { api } = mountFolders({ rects: fourRows })
    api.syncOrder(apps(1, 2, 3, 4))
    api.startDrag(down(60), 2)
    moveTo(45) // 第 1 行上半段：slot 1 == from
    expect(api.order.value).toEqual([1, 2, 3, 4])
    release()
  })

  it('读不到组行几何（数量对不上）时直接放弃，不瞎排', () => {
    const { api } = mountFolders({ rects: [{ top: 0, height: 40 }] })
    api.syncOrder(apps(1, 2, 3, 4))
    api.startDrag(down(60), 2)
    moveTo(200)
    expect(api.order.value).toEqual([1, 2, 3, 4])
    release()
  })

  it('右键不触发拖拽', () => {
    const { api } = mountFolders({ rects: fourRows })
    api.syncOrder(apps(1, 2, 3, 4))
    api.startDrag({ button: 2, clientY: 60 }, 2)
    moveTo(200)
    expect(api.order.value).toEqual([1, 2, 3, 4])
    release()
  })

  it('拖完浏览器补发的那次 click 不再翻折叠', () => {
    const { api } = mountFolders({ rects: fourRows })
    api.syncOrder(apps(1, 2, 3, 4))
    api.startDrag(down(60), 2)
    moveTo(115)
    release()
    api.onHeadClick(2)
    expect(api.isFolded(2)).toBe(false)
  })

  it('下一次正常点击仍会折叠：起拖时把挡刀标记清掉', () => {
    const { api } = mountFolders({ rects: fourRows })
    api.syncOrder(apps(1, 2, 3, 4))
    api.startDrag(down(60), 2)
    moveTo(115)
    release()
    api.onHeadClick(2) // 被挡掉
    api.startDrag(down(60), 2)
    api.onHeadClick(2) // 这次只是点击
    expect(api.isFolded(2)).toBe(true)
  })

  it('松手在窗口外（blur 收尾）也清掉拖拽态并落盘', () => {
    const { api, storage } = mountFolders({ rects: fourRows })
    api.syncOrder(apps(1, 2, 3, 4))
    api.startDrag(down(60), 2)
    moveTo(115)
    window.dispatchEvent(new Event('blur'))
    expect(api.draggingId.value).toBeNull()
    expect(document.body.classList.contains('zc-folder-dragging')).toBe(false)
    expect(storage.dump().order).toEqual([1, 3, 2, 4])
  })
})
