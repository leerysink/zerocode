// @vitest-environment happy-dom
// 设置页守卫（009 改造）。旧版模板里手写 store.form.xxx，守卫靠正则扫源码防打错字；
// 配置行改成 SETTINGS_SECTIONS 数据表后正则扫不到东西了，于是改成直接核那张表——
// 这是更强的版本：漏项、重复绑定、控件类型拼错、区间倒挂都能在装配期抓到，而不是等用户
// 发现某个框一直是空的、或保存时把不存在的键写进库。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import Antd from 'ant-design-vue'
import { createPinia, setActivePinia } from 'pinia'

global.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} }

vi.mock('../api')
// ZcPageHeader 里的 BackButton 顶层调 useRouter()：不建真 router，给桩
const mocks = vi.hoisted(() => ({ router: { push: vi.fn() } }))
vi.mock('vue-router', () => ({ useRouter: () => mocks.router }))

import SettingsView from './SettingsView.vue'
// ?raw 而不是 node:fs：happy-dom 环境下 import.meta.url 不是 file: 协议，
// 源码扫描要跨环境可用就走 Vite 的原始导入
import TEMPLATE from './SettingsView.vue?raw'
import { SETTINGS_SECTIONS } from '../utils/settingsSections'
import { useConfigStore } from '../stores/config'
import { useThemeStore } from '../stores/theme'
import { useAuthStore } from '../stores/auth'

const STYLE = TEMPLATE.match(/<style scoped>([\s\S]*?)<\/style>/)?.[1] ?? ''

const rows = SETTINGS_SECTIONS.flatMap((s) => (s.groups || []).flatMap((g) => g.rows))
const groupTitles = SETTINGS_SECTIONS.flatMap((s) => (s.groups || []).map((g) => g.title))

function mountView() {
  const pinia = createPinia()
  setActivePinia(pinia)
  useAuthStore().user = { id: 1, nickname: '创始人', is_founder: true }
  return mount(SettingsView, { global: { plugins: [Antd, pinia] }, attachTo: document.body })
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  document.body.innerHTML = ''
})

describe('分区表与 config store 的一致性', () => {
  it('表里的键与 config store 的表单严格一一对应（漏项与多键都会静默出错）', () => {
    const keyed = rows.map((r) => r.form)
    expect(keyed.sort()).toEqual(Object.keys(useConfigStore().form).sort())
  })

  it('每个键只绑一次：同一字段绑两个控件会联动，用户看不出自己动了什么', () => {
    const keyed = rows.map((r) => r.form)
    expect(new Set(keyed).size).toBe(keyed.length)
  })

  it('控件类型只认这五种：拼错会渲染出空白行', () => {
    for (const r of rows) expect(r.type).toSatisfy(
      (t) => ['switch', 'number', 'text', 'password', 'select'].includes(t))
  })

  it('数值行都给了下限，且不存在区间倒挂', () => {
    for (const r of rows.filter((x) => x.type === 'number')) {
      expect(r.min, r.form).toBeTypeOf('number')
      if (r.max !== undefined) expect(r.min, r.form).toBeLessThanOrEqual(r.max)
      if (r.step !== undefined) expect(r.step, r.form).toBeGreaterThan(0)
    }
  })

  it('每行都有中文名（表驱动后没有模板可核对，缺名等于界面上出现空白标签）', () => {
    for (const r of rows) expect(r.label, r.form).toBeTruthy()
  })

  it('「确认策略」组收着两个确认开关', () => {
    const g = SETTINGS_SECTIONS.flatMap((s) => s.groups || [])
      .find((x) => x.title === '确认策略')
    expect(g, '确认开关必须并在同一组里').toBeTruthy()
    expect(g.rows.map((r) => r.form)).toEqual(['writeConfirm', 'previewConfirmRunning'])
  })

  it('搬迁没丢老分组：压缩、预览、浏览器、数据库与记忆五组都还在', () => {
    for (const t of ['上下文压缩', '执行与预览', '浏览器检查', '应用数据库工具', '记忆召回']) {
      expect(groupTitles, t).toContain(t)
    }
  })
})

describe('设置中心形态', () => {
  it('八个分区都在左侧导航里，且切换不碰路由', async () => {
    const w = mountView()
    await flushPromises()
    expect(SETTINGS_SECTIONS).toHaveLength(8)
    for (const s of SETTINGS_SECTIONS) {
      expect(w.find(`[data-test="set-nav-${s.key}"]`).text()).toBe(s.label)
    }
    await w.find('[data-test="set-nav-runtime"]').trigger('click')
    await flushPromises()
    expect(w.text()).toContain('PostgreSQL')
    expect(mocks.router.push).not.toHaveBeenCalled()
  })

  it('配置分区有吸底保存条，模型库/外观/账号没有', async () => {
    const w = mountView()
    await flushPromises()
    expect(w.find('[data-test="set-save"]').exists()).toBe(false) // 默认停在模型库
    await w.find('[data-test="set-nav-behavior"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-test="set-save"]').exists()).toBe(true)
    await w.find('[data-test="set-nav-appearance"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-test="set-save"]').exists()).toBe(false)
  })

  it('模型库行操作：编辑直点，删除收在 ☰ 里', async () => {
    const w = mountView()
    await flushPromises()
    expect(w.find('[data-test="set-model-new"]').exists()).toBe(true)
    expect(TEMPLATE).toContain('zc-set-menu-btn')
    expect(TEMPLATE).toContain('removeModel(record)')
  })

  it('外观分区拨的是 theme store，不是本地副本', async () => {
    const w = mountView()
    await flushPromises()
    await w.find('[data-test="set-nav-appearance"]').trigger('click')
    await flushPromises()
    const theme = useThemeStore()
    const before = theme.theme
    await w.find('[data-test="set-theme-toggle"]').trigger('click')
    expect(theme.theme).not.toBe(before)
    expect(document.documentElement.dataset.theme).toBe(theme.theme)
  })

  it('账号分区显示昵称与创始人标识，登出走 authLogout', async () => {
    const w = mountView()
    await flushPromises()
    await w.find('[data-test="set-nav-account"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-test="set-account-name"]').text()).toBe('创始人')
    expect(w.text()).toContain('创始人') // 徽标
    expect(w.find('[data-test="set-logout"]').exists()).toBe(true)
  })

  it('配置分区顶部保留「平台级 vs 账户级」告知，外观/账号分区不需要', async () => {
    const w = mountView()
    await flushPromises()
    expect(w.find('[data-test="set-note-scope"]').exists()).toBe(false) // 模型库
    await w.find('[data-test="set-nav-tools"]').trigger('click')
    await flushPromises()
    const note = w.find('[data-test="set-note-scope"]')
    expect(note.text()).toContain('平台级配置')
    expect(note.text()).toContain('账户级')
    expect(note.text()).toContain('后端会回落')
    await w.find('[data-test="set-nav-appearance"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-test="set-note-scope"]').exists()).toBe(false)
  })

  it('页头返回按钮沿用 BackButton 固定回对话页', async () => {
    const w = mountView()
    await flushPromises()
    await w.find('.zc-back').trigger('click')
    expect(mocks.router.push).toHaveBeenCalledWith('/')
  })
})

describe('设置页视觉纪律', () => {
  it('样式块存在且消费几何 token：无裸 px 字号与圆角', () => {
    expect(STYLE).not.toBe('')
    expect(STYLE).not.toMatch(/font-size:\s*[0-9]/)
    expect(STYLE).not.toMatch(/border-radius:\s*[0-9]/)
  })

  it('颜色全走语义变量：无裸色值', () => {
    expect(STYLE).not.toMatch(/#[0-9a-fA-F]{3,8}\b/)
    expect(STYLE).not.toMatch(/rgba?\(\s*[0-9]/)
  })

  it('保存条吸底，长页面随时可保存', () => {
    expect(STYLE).toMatch(/\.zc-save-bar\s*\{[^}]*position:\s*sticky/)
  })

  it('设置行横排：左标签右控件', () => {
    expect(TEMPLATE).toContain('class="zc-row"')
    expect(STYLE).toMatch(/\.zc-row\s*\{[^}]*display:\s*flex/)
  })
})

describe('设置页入口', () => {
  it('技能已升为首页窄栏一级入口，设置页不再留跳转链接', () => {
    expect(TEMPLATE).not.toContain('技能管理')
    expect(TEMPLATE).not.toContain('/settings/skills')
  })
})

describe('设置页正文面卡（整页层级统一）', () => {
  it('左导航与右分区同住一张 zc-panel 卡，不再是铺在页面底色上的裸块', () => {
    const w = mountView()
    const body = w.find('.zc-set-body')
    expect(body.classes()).toContain('zc-panel')
    expect(body.find('.zc-set-nav').exists()).toBe(true)
    expect(body.find('.zc-set-main').exists()).toBe(true)
  })

  it('卡内留 token 内边距，两栏之间一条细分隔线', () => {
    expect(STYLE).toMatch(/\.zc-set-body\s*\{[^}]*padding:\s*var\(--zc-space-4\)/)
    expect(STYLE).toMatch(/\.zc-set-nav\s*\{[^}]*border-right:\s*1px solid var\(--zc-border-soft\)/)
  })

  it('外壳仍只有 zc-panel 一份：正文块不重复抄底色与描边', () => {
    const rule = STYLE.match(/\.zc-set-body\s*\{([^}]*)\}/)?.[1] ?? ''
    expect(rule).not.toMatch(/background/)
    expect(rule).not.toMatch(/(^|[^-])border(-radius)?:/)
    expect(rule).not.toMatch(/box-shadow/)
  })
})
