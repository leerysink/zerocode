// @vitest-environment happy-dom
// 分页条薄件契约：它不持有状态，只把 pager（usePagedList 的返回）接到 a-pagination 上。
// 「非空即常驻」这条规则收在组件里，六个列表才不会各写各的显隐判断。
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { ref, nextTick } from 'vue'

import ZcPagination from './ZcPagination.vue'
import { usePagedList, PAGE_SIZES } from '../../composables/usePagedList'

const items = (n) => Array.from({ length: n }, (_, i) => ({ id: i + 1 }))
const memoryStorage = () => {
  const map = new Map()
  return { getItem: (k) => (map.has(k) ? map.get(k) : null), setItem: (k, v) => map.set(k, v) }
}
const makePager = (n) => usePagedList(ref(items(n)), { storage: memoryStorage() })

const PagerStub = {
  props: ['current', 'total', 'pageSize', 'pageSizeOptions', 'showSizeChanger', 'showTotal'],
  emits: ['update:current', 'update:pageSize'],
  template: '<div class="stub-pager" />',
}
const stubs = { 'a-pagination': PagerStub }

const mountPager = (n, props = {}) => {
  const pager = makePager(n)
  const w = mount(ZcPagination, { props: { pager, ...props }, global: { stubs } })
  return { w, pager, bar: () => w.findComponent(PagerStub) }
}

describe('ZcPagination', () => {
  it('一条都没有时整条不渲染（空态由列表自己出）', () => {
    const { w } = mountPager(0)
    expect(w.find('[data-test="zc-pager"]').exists()).toBe(false)
  })

  it('不足一页也常驻：切档入口不能因为条目少就消失', () => {
    const { w } = mountPager(3)
    expect(w.find('[data-test="zc-pager"]').exists()).toBe(true)
  })

  it('把 pager 的状态原样交给 a-pagination', async () => {
    const { w, pager } = mountPager(13)
    pager.goTo(2)
    await nextTick()
    const props = w.findComponent(PagerStub).props()
    expect(props).toMatchObject({ current: 2, total: 13, pageSize: 6, showSizeChanger: true })
    expect(props.pageSizeOptions.map(Number)).toEqual([...PAGE_SIZES])
    expect(props.showTotal(13)).toBe('共 13 条')
  })

  it('页码变化写回 pager', async () => {
    const { w, pager } = mountPager(13)
    w.findComponent(PagerStub).vm.$emit('update:current', 2)
    expect(pager.current).toBe(2)
  })

  it('改每页条数走 pager，回到第 1 页并记住', async () => {
    const { w, pager } = mountPager(30)
    pager.goTo(3)
    w.findComponent(PagerStub).vm.$emit('update:pageSize', 12)
    expect(pager.pageSize).toBe(12)
    expect(pager.current).toBe(1)
  })

  it('可传标签说明这条分页属于哪个列表', () => {
    const { w } = mountPager(13, { label: '我的应用' })
    expect(w.find('[data-test="zc-pager"]').attributes('aria-label')).toBe('我的应用')
  })
})
