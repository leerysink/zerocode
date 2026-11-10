// @vitest-environment happy-dom
// 变更详情左栏的文件索引：只负责把列表铺出来、标出当前条、把点击与重试抛给页面。
// 取数、地址同步与「点当前条不重复请求」都在 FileChangeDetailView.test.js 钉，这里只钉渲染契约。
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ChangeFileList from './ChangeFileList.vue'

const CHANGES = [
  { id: '346', path: 'src/new.js', action: 'create' },
  { id: '345', path: 'src/App.vue', action: 'edit' },
  { id: '344', path: 'src/old.js', action: 'delete' },
  { id: '343', path: 'src/x.js', action: 'rename' },
]

function mountList(props = {}) {
  return mount(ChangeFileList, {
    props: {
      changes: CHANGES,
      activeId: '345',
      loading: false,
      failed: false,
      ...props,
    },
  })
}

describe('ChangeFileList 渲染', () => {
  it('按传入顺序铺出全部条目，动作说人话、未知取值原样展示', () => {
    const wrapper = mountList()
    const items = wrapper.findAll('.zc-cfl-item')

    expect(items).toHaveLength(4)
    expect(items[0].text()).toContain('新建')
    expect(items[0].text()).toContain('src/new.js')
    expect(items[1].text()).toContain('修改')
    expect(items[2].text()).toContain('删除')
    expect(items[3].text()).toContain('rename')
    expect(wrapper.find('.zc-cfl-count').text()).toBe('4')
  })

  it('当前条高亮，数字与字符串 id 都能对上', () => {
    const wrapper = mountList({ activeId: 345 })

    const items = wrapper.findAll('.zc-cfl-item')
    expect(items[1].classes()).toContain('is-active')
    expect(items[1].attributes('aria-current')).toBe('true')
    expect(items[0].classes()).not.toContain('is-active')
    expect(items[0].attributes('aria-current')).toBeUndefined()
  })

  it('边界：路径里的尖括号只当文本，不注入 HTML', () => {
    const wrapper = mountList({ changes: [{ id: '1', path: '<img src=x>', action: 'edit' }] })

    expect(wrapper.find('.zc-cfl-path').text()).toBe('<img src=x>')
    expect(wrapper.find('.zc-cfl-path img').exists()).toBe(false)
  })

  it('空列表给出「暂无变更」，不再渲染条目', () => {
    const wrapper = mountList({ changes: [] })

    expect(wrapper.findAll('.zc-cfl-item')).toHaveLength(0)
    expect(wrapper.find('.zc-cfl-empty').exists()).toBe(true)
  })
})

describe('ChangeFileList 交互与状态', () => {
  it('点任意条目都抛 select，附带整条变更（切换与否由页面判断）', async () => {
    const wrapper = mountList()

    await wrapper.findAll('.zc-cfl-item')[2].trigger('click')

    expect(wrapper.emitted('select')).toHaveLength(1)
    expect(wrapper.emitted('select')[0][0]).toEqual(CHANGES[2])
  })

  it('读取中不铺列表，也不误报空', () => {
    const wrapper = mountList({ changes: [], loading: true })

    expect(wrapper.find('.zc-cfl-loading').exists()).toBe(true)
    expect(wrapper.find('.zc-cfl-empty').exists()).toBe(false)
  })

  it('列表加载失败给出说明与重试，点重试抛 retry', async () => {
    const wrapper = mountList({ failed: true })

    expect(wrapper.find('.zc-cfl-error').exists()).toBe(true)
    await wrapper.find('.zc-cfl-retry').trigger('click')

    expect(wrapper.emitted('retry')).toHaveLength(1)
  })
})
