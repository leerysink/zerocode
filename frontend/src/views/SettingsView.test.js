// 设置页与 config store 的接线守卫：模板里写错 store.form.xxx 不会编译失败，
// 只会让那个输入框静默显示空值、保存时把 undefined 写进库——所以按源码逐个核。
import { readFileSync } from 'node:fs'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('../api', () => ({ getConfig: vi.fn(), putConfig: vi.fn() }))

import { useConfigStore } from '../stores/config'

const TEMPLATE = readFileSync(new URL('./SettingsView.vue', import.meta.url), 'utf8')

beforeEach(() => setActivePinia(createPinia()))

describe('设置页字段绑定', () => {
  const bound = [...TEMPLATE.matchAll(/store\.form\.([A-Za-z0-9_]+)/g)].map((m) => m[1])

  it('模板确实绑了这批字段（防空跑通过）', () => {
    expect(bound.length).toBeGreaterThanOrEqual(25)
  })

  it('每个绑定都能在 config store 的表单里找到', () => {
    const form = useConfigStore().form
    expect([...new Set(bound)].filter((k) => !(k in form))).toEqual([])
  })

  it('「确认策略」组收着两个确认开关，且全页各只绑一次', () => {
    const group = TEMPLATE.match(/<h3 class="zc-group-title">确认策略<\/h3>[\s\S]*?<\/section>/)?.[0] ?? ''
    expect(group, '确认开关必须并在同一组里').not.toBe('')
    for (const field of ['writeConfirm', 'previewConfirmRunning']) {
      expect(group).toContain(`store.form.${field}`)
      // 同一字段绑两个 switch 会联动：改一个另一个跟着变，用户看不出自己动了什么
      expect(bound.filter((k) => k === field)).toHaveLength(1)
    }
  })

  it('「运行参数」页签覆盖压缩、预览、浏览器、数据库与记忆五组', () => {
    for (const tab of ['上下文压缩', '执行与预览', '浏览器检查', '应用数据库工具', '记忆召回']) {
      expect(TEMPLATE).toContain(tab)
    }
    expect(TEMPLATE).toContain('tab="运行参数"')
  })
})

describe('设置页视觉纪律', () => {
  const STYLE = TEMPLATE.match(/<style scoped>([\s\S]*?)<\/style>/)?.[1] ?? ''

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
