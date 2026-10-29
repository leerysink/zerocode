// @vitest-environment happy-dom
// 编辑区容器：空态 / 读取中 / 读取失败 + 下载 / 正常挂 Monaco。
// Monaco 封装本组件桩掉（真编辑器只在浏览器手测），这里钉的是 props 透传与错误态渲染。
import { describe, it, expect, vi } from 'vitest'
import { h } from 'vue'
import { mount } from '@vue/test-utils'

vi.mock('ant-design-vue', () => ({
  message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}))
// 桩掉 Monaco 封装：测试环境不加载 monaco（含 ?worker 导入），只钉 props 透传
vi.mock('./MonacoEditor.vue', async () => {
  const { h } = await import('vue')
  return {
    default: {
      name: 'MonacoEditor',
      props: ['workspaceId', 'path', 'value', 'language', 'readOnly', 'theme', 'openPaths'],
      emits: ['update:value', 'update:dirty', 'save', 'cursor'],
      render() { return h('div', { class: 'stub-monaco' }, this.value || '') },
    },
  }
})

import WorkbenchEditor from './WorkbenchEditor.vue'

const slotDiv = (name) => ({
  name,
  render() {
    return h('div', { class: `stub-${name}` }, this.$slots.default ? this.$slots.default() : [])
  },
})
const STUBS = {
  'a-spin': slotDiv('a-spin'),
}

const mountEditor = (props = {}) => mount(WorkbenchEditor, {
  props: { workspaceId: 7, path: 'src/App.vue', content: '<template />', ...props },
  global: { stubs: STUBS },
})
const monaco = (wrapper) => wrapper.findComponent({ name: 'MonacoEditor' })

describe('WorkbenchEditor 三种非编辑态', () => {
  it('没有激活页签时显示引导空态，不挂 Monaco', () => {
    const wrapper = mountEditor({ path: '', content: '' })

    expect(wrapper.find('.zc-wb-empty').text()).toContain('从左侧选择文件开始编辑')
    expect(monaco(wrapper).exists()).toBe(false)
  })

  it('读取中显示加载态，不挂 Monaco', () => {
    const wrapper = mountEditor({ loading: true })

    expect(wrapper.find('.stub-a-spin').exists()).toBe(true)
    expect(monaco(wrapper).exists()).toBe(false)
  })

  it('读取失败显示后端 detail 与下载链接，不挂 Monaco', () => {
    const wrapper = mountEditor({ error: '不支持读取的文件（二进制或超过200KB）' })

    expect(wrapper.find('.zc-wb-error').text()).toContain('不支持读取的文件')
    expect(wrapper.find('.zc-wb-download').attributes('href'))
      .toBe('/api/fs/download?workspace_id=7&path=src%2FApp.vue')
    expect(monaco(wrapper).exists()).toBe(false)
  })

  it('错误文案为空时兜底「读取失败」，不渲染 undefined', () => {
    const wrapper = mountEditor({ error: ' ' })

    expect(wrapper.find('.zc-wb-error').text()).toContain('读取失败')
  })
})

describe('WorkbenchEditor 正常态透传', () => {
  it('按扩展名给 Monaco 语言：.vue 走 HTML，未知类型纯文本', () => {
    expect(monaco(mountEditor({ path: 'src/App.vue' })).props('language')).toBe('html')
    expect(monaco(mountEditor({ path: 'a.weird' })).props('language')).toBe('plaintext')
  })

  it('只读标记与主题、内容原样透传', () => {
    const wrapper = mountEditor({ path: 'app.json', readOnly: true, theme: 'light', content: '{}' })

    expect(monaco(wrapper).props('readOnly')).toBe(true)
    expect(monaco(wrapper).props('theme')).toBe('light')
    expect(monaco(wrapper).props('value')).toBe('{}')
    expect(wrapper.find('.zc-wbe').attributes('data-theme')).toBe('light')
  })

  it('Monaco 的输入 / 脏态 / 保存 / 光标事件原样上报给宿主', async () => {
    const wrapper = mountEditor()

    monaco(wrapper).vm.$emit('update:value', '改过了')
    monaco(wrapper).vm.$emit('update:dirty', true)
    monaco(wrapper).vm.$emit('save')
    monaco(wrapper).vm.$emit('cursor', { line: 3, column: 5, selected: 2 })

    expect(wrapper.emitted('update:content')[0]).toEqual(['改过了'])
    expect(wrapper.emitted('update:dirty')[0]).toEqual([true])
    expect(wrapper.emitted('save')).toHaveLength(1)
    expect(wrapper.emitted('cursor')[0]).toEqual([{ line: 3, column: 5, selected: 2 }])
  })

  it('非 Markdown 文件没有渲染工具栏，也不渲染预览', () => {
    const wrapper = mountEditor({ path: 'src/App.vue', content: '<template />' })

    expect(wrapper.find('.zc-wbe-bar').exists()).toBe(false)
    expect(wrapper.find('.zc-md').exists()).toBe(false)
    expect(monaco(wrapper).exists()).toBe(true)
  })
})

const MD_DOC = [
  '# 标题',
  '',
  '段落 **加粗** [站内链接](other.md) [外链](https://example.com) [锚点](#section) [坏链](javascript:alert(1))',
  '',
  '![图](./img/a.png)',
  '',
  '```js',
  'const a = 1',
  '```',
].join('\n')

describe('WorkbenchEditor Markdown 三态', () => {
  const mountMd = (props = {}) => mountEditor({ path: 'docs/README.md', content: MD_DOC, ...props })
  const editorVisible = (wrapper) => wrapper.find('.zc-wbe-editor').element.style.display !== 'none'

  it('.md 默认渲染视图：标题、表格/代码高亮都在，Monaco 仍挂着（不丢模型与撤销栈）', () => {
    const wrapper = mountMd()

    expect(wrapper.find('.zc-md h1').text()).toBe('标题')
    expect(wrapper.find('.zc-md pre.hljs').exists()).toBe(true)
    expect(wrapper.find('.zc-wb-mode-render').classes()).toContain('is-active')
    expect(editorVisible(wrapper)).toBe(false)
    expect(monaco(wrapper).exists()).toBe(true)
  })

  it('工具栏可切源码：预览退场、编辑器露面，内容事件照常回传', async () => {
    const wrapper = mountMd()

    await wrapper.find('.zc-wb-mode-source').trigger('click')

    expect(wrapper.find('.zc-md').exists()).toBe(false)
    expect(editorVisible(wrapper)).toBe(true)
    expect(wrapper.find('.zc-wb-mode-source').classes()).toContain('is-active')
    expect(wrapper.emitted('update:mode')[0]).toEqual(['source'])

    monaco(wrapper).vm.$emit('update:value', '# 改过')
    expect(wrapper.emitted('update:content')[0]).toEqual(['# 改过'])
  })

  it('分屏：左右同框，右侧跟随缓冲区实时更新（含未保存改动）', async () => {
    const wrapper = mountMd()

    await wrapper.find('.zc-wb-mode-split').trigger('click')
    expect(wrapper.find('.zc-wbe-body').classes()).toContain('is-split')
    expect(editorVisible(wrapper)).toBe(true)
    expect(wrapper.find('.zc-md h1').text()).toBe('标题')

    await wrapper.setProps({ content: '# 未保存的新标题' })
    expect(wrapper.find('.zc-md h1').text()).toBe('未保存的新标题')
  })

  it('切到别的 md 文件时回到默认渲染视图（恢复的 md 页签同样从这里落地）', async () => {
    const wrapper = mountMd()
    await wrapper.find('.zc-wb-mode-source').trigger('click')
    await wrapper.setProps({ path: 'docs/OTHER.md', content: '# 别的文档' })

    expect(wrapper.find('.zc-wb-mode-render').classes()).toContain('is-active')
    expect(wrapper.find('.zc-md h1').text()).toBe('别的文档')
  })

  it('渲染视图里的相对链接交给宿主开页签；外链新标签带 noopener', async () => {
    const wrapper = mountMd()
    const inner = wrapper.find('.zc-md a[data-file-path]')

    expect(inner.attributes('href')).toBe('/workspaces/7/files?file=docs%2Fother.md')
    await inner.trigger('click')
    expect(wrapper.emitted('open-file')[0]).toEqual(['docs/other.md'])

    const ext = wrapper.find('.zc-md a[href="https://example.com"]')
    expect(ext.attributes('target')).toBe('_blank')
    expect(ext.attributes('rel')).toBe('noopener noreferrer')
  })

  it('相对图片经 download inline 显示；锚点保持；javascript: 只留文字', () => {
    const wrapper = mountMd()

    expect(wrapper.find('.zc-md img').attributes('src'))
      .toBe('/api/fs/download?workspace_id=7&path=docs%2Fimg%2Fa.png&inline=1')
    expect(wrapper.find('.zc-md a[href="#section"]').exists()).toBe(true)
    expect(wrapper.find('.zc-md').html()).not.toContain('javascript:alert')
    expect(wrapper.find('.zc-md').text()).toContain('坏链')
  })

  it('读取失败时不出现渲染工具栏与预览', () => {
    const wrapper = mountMd({ loading: false, error: '文件不存在' })

    expect(wrapper.find('.zc-wbe-bar').exists()).toBe(false)
    expect(wrapper.find('.zc-md').exists()).toBe(false)
  })
})
