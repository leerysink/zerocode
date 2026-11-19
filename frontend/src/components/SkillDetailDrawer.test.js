// @vitest-environment happy-dom
// 技能详情/预览抽屉（ADR-0061 / ticket 001/002/004）：纯展示组件，加载器注入。
import { describe, expect, it, vi } from 'vitest'
import { h } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import SkillDetailDrawer from './SkillDetailDrawer.vue'

const STUBS = {
  'a-drawer': { name: 'a-drawer', props: ['open', 'title', 'width', 'placement'], emits: ['close'],
    render() { return h('div', { class: 'stub-drawer' }, [this.$slots.default?.(), this.$slots.footer?.()]) } },
  'a-button': { name: 'a-button', props: ['type', 'loading', 'size'],
    render() { return h('button', { class: 'stub-btn', onClick: () => this.$emit('click') }, this.$slots.default?.()) } },
  'MarkdownView': { name: 'MarkdownView', props: ['content'],
    render() { return h('div', { class: 'stub-md' }, this.content) } },
}

const FILES = [
  { path: 'SKILL.md', size: 20, kind: 'text' },
  { path: 'scripts/run.py', size: 8, kind: 'text' },
  { path: 'assets/logo.png', size: 8, kind: 'image' },
  { path: 'data.bin', size: 2, kind: 'binary' },
]

function mountDrawer(props = {}) {
  return mount(SkillDetailDrawer, {
    props: { open: true, files: FILES, loadContent: vi.fn(() => Promise.resolve('文件正文')),
      rawUrl: (p) => `/raw/${p}`, ...props },
    global: { stubs: STUBS },
  })
}

describe('SkillDetailDrawer', () => {
  it('渲染文件树（路径+大小+类型）', () => {
    const w = mountDrawer()
    expect(w.findAll('.zc-sd-file').length).toBe(4)
    expect(w.text()).toContain('scripts/run.py')
  })

  it('点文本文件 → 调 loadContent 并源码态展示', async () => {
    const loadContent = vi.fn(() => Promise.resolve('print(1)'))
    const w = mountDrawer({ loadContent })
    await w.find('[data-test="sd-file-scripts/run.py"]').trigger('click')
    await flushPromises()
    expect(loadContent).toHaveBeenCalledWith('scripts/run.py')
    expect(w.find('[data-test="sd-text"]').text()).toBe('print(1)')
  })

  it('点图片 → img 走 rawUrl', async () => {
    const w = mountDrawer()
    await w.find('[data-test="sd-file-assets/logo.png"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-test="sd-image"]').attributes('src')).toBe('/raw/assets/logo.png')
  })

  it('点二进制 → 仅元数据', async () => {
    const w = mountDrawer()
    await w.find('[data-test="sd-file-data.bin"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-test="sd-binary"]').exists()).toBe(true)
  })

  it('打开默认展示 SKILL.md（预览态给 skillMd 直接渲染）', async () => {
    const w = mountDrawer({ skillMd: '# 标题' })
    await flushPromises()
    expect(w.find('.stub-md').text()).toContain('# 标题')
  })

  it('confirmable → 确认安装按钮 emit confirm', async () => {
    const w = mountDrawer({ confirmable: true })
    const btn = w.findAll('.stub-btn').find((b) => b.text().includes('确认安装'))
    await btn.trigger('click')
    expect(w.emitted('confirm')).toBeTruthy()
  })

  it('只读：无编辑/删除入口', () => {
    const w = mountDrawer()
    expect(w.text()).not.toContain('编辑')
    expect(w.text()).not.toContain('删除')
  })
})