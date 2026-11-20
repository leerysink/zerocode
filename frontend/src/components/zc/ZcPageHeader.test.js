// @vitest-environment happy-dom
// 页头薄件契约：返回可关、统计格按 props 渲染、右侧动作槽可注入——不含任何业务判断。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'

const push = vi.fn()
vi.mock('vue-router', () => ({ useRouter: () => ({ push }) }))

import ZcPageHeader from './ZcPageHeader.vue'
import TEMPLATE from './ZcPageHeader.vue?raw'

const stubs = {
  'a-button': { template: '<button><slot /></button>' },
  ArrowLeftOutlined: { template: '<i />' },
}

function mountHeader(props = {}, slots = {}) {
  return mount(ZcPageHeader, { props, global: { stubs }, slots })
}

beforeEach(() => vi.clearAllMocks())

describe('ZcPageHeader', () => {
  it('渲染标题与说明', () => {
    const w = mountHeader({ title: '商店管理', subtitle: '管理上架应用' })
    expect(w.find('[data-test="page-title"]').text()).toBe('商店管理')
    expect(w.text()).toContain('管理上架应用')
  })

  it('默认带返回按钮，点击固定回对话页', async () => {
    const w = mountHeader({ title: '商店管理' })
    expect(w.find('.zc-back').exists()).toBe(true)
    await w.find('.zc-back').trigger('click')
    expect(push).toHaveBeenCalledWith('/')
  })

  it('back=false 时不渲染返回', () => {
    expect(mountHeader({ title: 'x', back: false }).find('.zc-back').exists()).toBe(false)
  })

  it('backTo 可覆盖返回目标', async () => {
    const w = mountHeader({ title: 'x', backTo: '/store' })
    await w.find('.zc-back').trigger('click')
    expect(push).toHaveBeenCalledWith('/store')
  })

  it('统计格按 stats 渲染读数与标签', () => {
    const w = mountHeader({ title: 'x', stats: [
      { key: 'all', label: '全部', value: 7 },
      { key: 'pending', label: '待审批', value: 2, color: 'red' },
    ] })
    expect(w.find('[data-test="page-stat-all"]').find('b').text()).toBe('7')
    expect(w.find('[data-test="page-stat-all"]').find('.zc-page-stat-label').text()).toBe('全部')
    expect(w.find('[data-test="page-stat-pending"]').find('b').text()).toBe('2')
    expect(w.find('[data-test="page-stat-pending"]').attributes('style')).toContain('color')
  })

  it('无统计格时不渲染该行', () => {
    expect(mountHeader({ title: 'x' }).find('[data-test="page-stats"]').exists()).toBe(false)
  })

  it('actions 槽注入右侧动作', () => {
    const w = mountHeader({ title: 'x' }, { actions: '<button data-test="act">刷新</button>' })
    expect(w.find('[data-test="act"]').exists()).toBe(true)
  })

  // 面卡外壳（描边 / 圆角 / 卡底 / 阴影）在 styles/theme.less 的全局 .zc-panel 上，这里钉不住那份文件：
  // vitest 的 css:false 让 `theme.less?raw` 返回空串，node:fs 又得靠 process.cwd()。
  // 所以钉两端：类名挂上了 + 组件内不再抄一份卡面样式——中间那层由主题文件唯一供给。
  it('页头是一张面卡：挂全局 zc-panel 外壳，组件自己不再抄一份卡面', () => {
    const w = mountHeader({ title: 'x' })
    const head = w.find('[data-test="page-header"]')
    expect(head.classes()).toContain('zc-page-head')
    expect(head.classes()).toContain('zc-panel')
    const scoped = TEMPLATE.match(/<style scoped>([\s\S]*?)<\/style>/)?.[1] ?? ''
    const rule = scoped.match(/\.zc-page-head\s*\{([^}]*)\}/)?.[1] ?? ''
    expect(rule).not.toMatch(/background/)
    expect(rule).not.toMatch(/border/)
    expect(rule).not.toMatch(/box-shadow/)
  })

  it('面卡内边距走 token，不留裸 px', () => {
    const w = mountHeader({ title: 'x' })
    expect(w.find('[data-test="page-header"]').attributes('style')).toBeUndefined()
    const scoped = TEMPLATE.match(/<style scoped>([\s\S]*?)<\/style>/)?.[1] ?? ''
    const rule = scoped.match(/\.zc-page-head\s*\{([^}]*)\}/)?.[1] ?? ''
    expect(rule).toContain('padding: var(--zc-space-4)')
    expect(rule).not.toMatch(/\d+px/)
  })

  it('返回在卡内收成描边胶囊（与应用页 hero 同处理），几何走 token', () => {
    const scoped = TEMPLATE.match(/<style scoped>([\s\S]*?)<\/style>/)?.[1] ?? ''
    const chip = scoped.match(/:deep\(\.zc-back\)\s*\{([^}]*)\}/)?.[1] ?? ''
    expect(chip).toContain('border: 1px solid var(--zc-border)')
    expect(chip).toContain('border-radius: var(--zc-radius-pill)')
    expect(chip).not.toMatch(/padding:[^;]*[0-9]+px/)
  })
})
