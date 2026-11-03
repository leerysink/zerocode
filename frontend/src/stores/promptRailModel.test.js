// @vitest-environment happy-dom
// 提问导航条的纯几何/文案判定：文案回退、可导航提问的挑选、minimap 位置映射、视口内高亮。
// 数字进数字出，不碰 DOM——DOM 量测留在 useChatPromptRail。
import { describe, it, expect } from 'vitest'
import {
  MIN_RAIL_PROMPTS,
  promptLabelOf,
  collectPrompts,
  buildPromptMarks,
} from './promptRailModel'

describe('promptLabelOf：提问浮层的展示文案', () => {
  it('有文字就用文字，首尾空白抹掉', () => {
    expect(promptLabelOf({ role: 'user', content: '  帮我全面分析电投能源  ' })).toBe('帮我全面分析电投能源')
  })

  it('只有附件时回退成「附件：文件名」，多个用顿号连', () => {
    const label = promptLabelOf({
      role: 'user', content: '',
      attachments: [{ filename: 'a.png' }, { filename: 'b.png' }],
    })
    expect(label).toBe('附件：a.png、b.png')
  })

  it('附件缺文件名时不产出空段', () => {
    expect(promptLabelOf({ role: 'user', attachments: [{ filename: '' }, { filename: 'c.png' }] })).toBe('附件：c.png')
  })

  it('文字与附件都没有则返回空串（调用方据此不生成横杠）', () => {
    expect(promptLabelOf({ role: 'user', content: '   ' })).toBe('')
    expect(promptLabelOf({ role: 'user' })).toBe('')
  })

  it('非 user 角色一律不认', () => {
    expect(promptLabelOf({ role: 'assistant', content: '你好' })).toBe('')
  })
})

describe('collectPrompts：从归一化消息里挑出可导航的提问', () => {
  it('只收 user，index 用它在 view 里的下标', () => {
    const view = [
      { role: 'user', content: '第一问' },
      { role: 'assistant', content: '答' },
      { role: 'tool', name: 'preview_status' },
      { role: 'user', content: '第二问' },
    ]
    expect(collectPrompts(view)).toEqual([
      { index: 0, label: '第一问' },
      { index: 3, label: '第二问' },
    ])
  })

  it('丢掉既无文字又无附件的 user 行，下标不因此错位', () => {
    const view = [
      { role: 'user', content: '有内容' },
      { role: 'user', content: '' },
      { role: 'user', attachments: [{ filename: 'x.png' }] },
    ]
    expect(collectPrompts(view)).toEqual([
      { index: 0, label: '有内容' },
      { index: 2, label: '附件：x.png' },
    ])
  })

  it('空视图与非数组入参都得到空数组', () => {
    expect(collectPrompts([])).toEqual([])
    expect(collectPrompts(null)).toEqual([])
  })
})

// 夹具：内容高 4000、可视区 800，三条提问在 view 里的下标刻意不连续（0/3/7）——
// 中间夹着 assistant/tool 行和「无文字无附件」被丢弃的 user 行。若实现按数组顺序配对
// 量测而不是按下标配对，横杠会整体错位，下面的位置断言就会红。
const PROMPTS = [
  { index: 0, label: '第一问' },
  { index: 3, label: '第二问' },
  { index: 7, label: '第三问' },
]
const MEASURED = {
  0: { top: 500, height: 60 },
  3: { top: 1800, height: 60 },
  7: { top: 3200, height: 60 },
}
// 内容高 4000、可视区 800
const GEOM = { scrollHeight: 4000, clientHeight: 800 }
const run = (extra = {}) => buildPromptMarks({ prompts: PROMPTS, measured: MEASURED, ...GEOM, ...extra })

describe('buildPromptMarks：minimap 位置映射', () => {
  it('横杠纵向位置 = 提问在内容里的真实位置（百分比），按下标认人', () => {
    expect(run({ scrollTop: 0 }).marks.map((m) => m.topPct)).toEqual([12.5, 45, 80])
  })

  it('位置落在 [0,100] 内，越界被夹住', () => {
    const { marks } = buildPromptMarks({
      prompts: [{ index: 0, label: 'a' }, { index: 1, label: 'b' }],
      measured: { 0: { top: -20, height: 10 }, 1: { top: 130, height: 10 } },
      scrollHeight: 100, clientHeight: 50, scrollTop: 0,
    })
    expect(marks.map((m) => m.topPct)).toEqual([0, 100])
  })

  it('还没量到位置的提问不画横杠，其余照画不错位', () => {
    const { marks } = buildPromptMarks({
      prompts: PROMPTS,
      measured: { 0: MEASURED[0], 7: MEASURED[7] },
      ...GEOM, scrollTop: 0,
    })
    expect(marks.map((m) => [m.index, m.topPct])).toEqual([[0, 12.5], [7, 80]])
  })

  it('内容高度为 0 或完全没有量测时不画横杠（首屏还没量出位置）', () => {
    expect(buildPromptMarks({ prompts: PROMPTS, measured: MEASURED, ...GEOM, scrollTop: 0, scrollHeight: 0 }).marks).toEqual([])
    expect(buildPromptMarks({ prompts: PROMPTS, measured: {}, ...GEOM, scrollTop: 0 }).marks).toEqual([])
  })

  it('横杠带上 index 与 label，供点击定位与悬停浮层', () => {
    expect(run({ scrollTop: 0 }).marks[1]).toMatchObject({ index: 3, label: '第二问' })
  })
})

describe('buildPromptMarks：视口内高亮', () => {
  it('落在视口内的提问被标为 active', () => {
    // 视口 [1600, 2400]：只有第二条（top 1800）在内
    const { marks, activeIndex } = run({ scrollTop: 1600 })
    expect(activeIndex).toBe(3)
    expect(marks.filter((m) => m.active)).toEqual([marks[1]])
  })

  it('视口内有多条时取最靠上的那条', () => {
    const { marks, activeIndex } = buildPromptMarks({
      prompts: PROMPTS,
      measured: { 0: { top: 1000, height: 60 }, 3: { top: 1200, height: 60 }, 7: { top: 3200, height: 60 } },
      ...GEOM, scrollTop: 800,
    })
    // 视口 [800, 1600]：第一条（1000）与第二条（1200）都在内
    expect(activeIndex).toBe(0)
    expect(marks[0].active).toBe(true)
    expect(marks[1].active).toBe(false)
  })

  it('视口落在两条提问之间的空档时，高亮刚滚过去的那条', () => {
    // 视口 [700, 1500]：第一条（500~560）已整体滚过，第二条（1800）还没进
    expect(run({ scrollTop: 700 }).activeIndex).toBe(0)
  })

  it('停在第一条提问之上时不高亮任何一条', () => {
    const { marks, activeIndex } = buildPromptMarks({
      prompts: PROMPTS,
      measured: { 0: { top: 2000, height: 60 }, 3: { top: 3000, height: 60 }, 7: { top: 3800, height: 60 } },
      ...GEOM, scrollTop: 0,
    })
    expect(activeIndex).toBeNull()
    expect(marks.some((m) => m.active)).toBe(false)
  })

  it('滚到底时高亮最后一条', () => {
    // 视口 [3200, 4000]：第三条（top 3200）在内
    expect(run({ scrollTop: 3200 }).activeIndex).toBe(7)
  })

  it('没量到位置的提问不参与高亮判定', () => {
    // 只剩第三条量到，且它就在视口里
    const { marks, activeIndex } = buildPromptMarks({
      prompts: PROMPTS,
      measured: { 7: MEASURED[7] },
      ...GEOM, scrollTop: 3200,
    })
    expect(marks.length).toBe(1)
    expect(activeIndex).toBe(7)
  })
})

describe('MIN_RAIL_PROMPTS：少于两条提问不出轨道', () => {
  it('常量是 2', () => {
    expect(MIN_RAIL_PROMPTS).toBe(2)
  })

  it('只有一条提问时不产出横杠，两条起才产出', () => {
    const one = buildPromptMarks({
      prompts: [{ index: 0, label: '只有一问' }],
      measured: { 0: { top: 500, height: 60 } },
      ...GEOM, scrollTop: 0,
    })
    expect(one.marks).toEqual([])
    expect(one.activeIndex).toBeNull()

    const two = buildPromptMarks({
      prompts: PROMPTS.slice(0, 2),
      measured: { 0: MEASURED[0], 3: MEASURED[3] },
      ...GEOM, scrollTop: 0,
    })
    expect(two.marks.length).toBe(2)
  })
})
