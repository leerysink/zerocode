// @vitest-environment happy-dom
// 记忆详情左栏的索引：分组顺序、当前条高亮、停用标记与三种状态（读取中/失败重试/空）。
// 取数、地址同步、删后切邻条都在 MemoryDetailView.test.js 钉，这里只钉渲染契约。
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import MemoryItemList from './MemoryItemList.vue'

const MEMORIES = [
  { id: '12', type: 'fact', title: '部署域名', content: 'prod 走网关', enabled: true },
  { id: '11', type: 'preference', title: '', content: '喜欢深色', enabled: false },
  { id: '10', type: 'fact', title: '端口口径', content: 'dev 用 8100', enabled: true },
  { id: '9', type: 'legacy', title: '旧分类', content: 'x', enabled: true },
]

function mountList(props = {}) {
  return mount(MemoryItemList, {
    props: {
      memories: MEMORIES,
      activeId: '12',
      loading: false,
      failed: false,
      ...props,
    },
  })
}

describe('MemoryItemList 渲染', () => {
  it('按固定类型顺序分组，未知类型兜底归「其他」，组内按传入顺序', () => {
    const wrapper = mountList()

    const titles = wrapper.findAll('.zc-mil-group-title').map((t) => t.text())
    expect(titles).toEqual(['偏好（1）', '事实（2）', '其他（1）'])

    const items = wrapper.findAll('.zc-mil-item')
    expect(items).toHaveLength(4)
    expect(items[0].text()).toContain('(无标题)')
    expect(items[1].text()).toContain('部署域名')
    expect(items[2].text()).toContain('端口口径')
    expect(wrapper.find('.zc-mil-count').text()).toBe('4')
  })

  it('当前条高亮，数字与字符串 id 都能对上', () => {
    const wrapper = mountList({ activeId: 10 })

    const items = wrapper.findAll('.zc-mil-item')
    expect(items[2].classes()).toContain('is-active')
    expect(items[2].attributes('aria-current')).toBe('true')
    expect(items[0].classes()).not.toContain('is-active')
    expect(items[0].attributes('aria-current')).toBeUndefined()
  })

  it('停用的记忆带「停用」标记，启用行没有', () => {
    const wrapper = mountList()
    const items = wrapper.findAll('.zc-mil-item')

    expect(items[0].find('.zc-mil-off').exists()).toBe(true)
    expect(items[1].find('.zc-mil-off').exists()).toBe(false)
  })

  it('边界：标题里的尖括号只当文本，不注入 HTML', () => {
    const wrapper = mountList({
      memories: [{ id: '1', type: 'fact', title: '<img src=x>', content: 'x', enabled: true }],
    })

    expect(wrapper.find('.zc-mil-title').text()).toBe('<img src=x>')
    expect(wrapper.find('.zc-mil-title img').exists()).toBe(false)
  })

  it('空列表给出「暂无记忆」，不再渲染分组', () => {
    const wrapper = mountList({ memories: [] })

    expect(wrapper.find('.zc-mil-empty').text()).toBe('暂无记忆')
    expect(wrapper.findAll('.zc-mil-item')).toHaveLength(0)
  })

  it('读取中只显示占位，失败给重试并把点击抛给页面', async () => {
    const loading = mountList({ loading: true })
    expect(loading.find('.zc-mil-loading').exists()).toBe(true)

    const failed = mountList({ failed: true })
    expect(failed.find('.zc-mil-error').exists()).toBe(true)
    await failed.find('.zc-mil-retry').trigger('click')
    expect(failed.emitted('retry')).toHaveLength(1)
  })

  it('点条目把整条记忆抛给页面', async () => {
    const wrapper = mountList()
    await wrapper.findAll('.zc-mil-item')[2].trigger('click')

    expect(wrapper.emitted('select')[0][0].id).toBe('10')
  })
})
