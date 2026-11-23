// @vitest-environment happy-dom
// 控制台列表分页的唯一状态源：全量数组留在手里，只切眼下这一页。
// 后端不分页是刻意的（见 ADR-0071）——页头统计、tab 计数都要全量口径才对得上。
import { describe, it, expect } from 'vitest'
import { ref } from 'vue'

import { usePagedList, DEFAULT_PAGE_SIZE, PAGE_SIZES, PAGE_SIZE_STORAGE_KEY } from './usePagedList'

const items = (n) => Array.from({ length: n }, (_, i) => ({ id: i + 1 }))
const memoryStorage = (initial = {}) => {
  const map = new Map(Object.entries(initial))
  return {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, v),
    map,
  }
}
const make = (n) => usePagedList(ref(items(n)), { storage: memoryStorage() })
const withStorage = (n, storage) => usePagedList(ref(items(n)), { storage })

describe('usePagedList', () => {
  it('默认每页 6 条', () => {
    const p = make(13)
    expect(DEFAULT_PAGE_SIZE).toBe(6)
    expect(p.paged.map((x) => x.id)).toEqual([1, 2, 3, 4, 5, 6])
    expect(p.total).toBe(13)
    expect(p.pageCount).toBe(3)
  })

  it('goTo 翻到第 2 页取第 7 到 12 条', () => {
    const p = make(13)
    p.goTo(2)
    expect(p.current).toBe(2)
    expect(p.paged.map((x) => x.id)).toEqual([7, 8, 9, 10, 11, 12])
  })

  it('沿用本机记住的每页条数', () => {
    const p = withStorage(30, memoryStorage({ [PAGE_SIZE_STORAGE_KEY]: '12' }))
    expect(p.pageSize).toBe(12)
    expect(p.paged.length).toBe(12)
  })

  // 白名单外的一律不认：手改 localStorage 或以后改档位，都不该让页面拿到一个算不出页数的 pageSize
  it.each(['999', 'abc', '0', ''])('本机存的每页条数「%s」不合法时回落到默认', (raw) => {
    const p = withStorage(30, memoryStorage({ [PAGE_SIZE_STORAGE_KEY]: raw }))
    expect(p.pageSize).toBe(DEFAULT_PAGE_SIZE)
  })

  it('改每页条数：回到第 1 页并记在本机', () => {
    const storage = memoryStorage()
    const p = withStorage(30, storage)
    p.goTo(3)
    p.setPageSize(12)
    expect(p.pageSize).toBe(12)
    expect(p.current).toBe(1)
    expect(p.paged.map((x) => x.id)).toEqual(items(12).map((x) => x.id))
    expect(storage.map.get(PAGE_SIZE_STORAGE_KEY)).toBe('12')
  })

  it('每页条数是本机一份全局偏好，六个列表共用', () => {
    const storage = memoryStorage()
    withStorage(30, storage).setPageSize(24)
    expect(withStorage(30, storage).pageSize).toBe(24)
  })

  it('本机写不进去（隐私模式/配额满）也照样能改条数', () => {
    const p = withStorage(30, { getItem: () => null, setItem: () => { throw new Error('QuotaExceeded') } })
    expect(() => p.setPageSize(12)).not.toThrow()
    expect(p.pageSize).toBe(12)
  })

  it('resetPage 回到第 1 页：筛选与搜索换了口径就该从头看', () => {
    const p = make(13)
    p.goTo(3)
    p.resetPage()
    expect(p.current).toBe(1)
  })

  it('列表被轮询抽短时落在最后一页，而不是停在空页', () => {
    const src = ref(items(13))
    const p = usePagedList(src, { storage: memoryStorage() })
    p.goTo(3)
    src.value = items(7)
    expect(p.pageCount).toBe(2)
    expect(p.current).toBe(2)
    expect(p.paged.map((x) => x.id)).toEqual([7])
  })

  it('给 a-table 用的分页配置与分页条同一份状态', () => {
    const p = make(13)
    const cfg = p.tablePagination
    expect(cfg).toMatchObject({ current: 1, pageSize: 6, total: 13, showSizeChanger: true })
    expect(cfg.pageSizeOptions.map(Number)).toEqual([...PAGE_SIZES])
    expect(cfg.showTotal(13, [0, 6])).toBe('共 13 条')
    cfg.onChange(2, 6)
    expect(p.current).toBe(2)
    cfg.onChange(1, 24)
    expect(p.pageSize).toBe(24)
    expect(p.current).toBe(1)
  })
})
