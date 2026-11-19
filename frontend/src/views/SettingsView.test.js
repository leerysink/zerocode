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

  it('「确认策略」卡收着两个确认开关，且全页各只绑一次', () => {
    const card = TEMPLATE.match(/<a-card title="确认策略"[\s\S]*?<\/a-card>/)?.[0] ?? ''
    expect(card, '确认开关必须并在这同一张卡里').not.toBe('')
    for (const field of ['writeConfirm', 'previewConfirmRunning']) {
      expect(card).toContain(`store.form.${field}`)
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

describe('设置页入口', () => {
  it('技能已升为首页窄栏一级入口，设置页不再留跳转链接', () => {
    expect(TEMPLATE).not.toContain('技能管理')
    expect(TEMPLATE).not.toContain('/settings/skills')
  })
})
