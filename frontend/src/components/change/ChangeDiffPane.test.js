// @vitest-environment happy-dom
// 变更详情的双栏面板：把 sideBySide 的行模型铺成左右两栏网格，并管「展开 N 行未变更」的本地状态。
// 行模型本身由 src/utils/sideBySide.test.js 钉死，这里只钉渲染契约：
// 一行两栏、行号与配色类名、折叠段的展开/续展、identical 与 unaligned 两种降级、文本只当文本。
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ChangeDiffPane from './ChangeDiffPane.vue'

const row = (ln, lt, lc, rn, rt, rc) => ({
  kind: 'row',
  left: { n: ln, text: lt, cls: lc },
  right: { n: rn, text: rt, cls: rc },
})

function mountPane(props = {}) {
  return mount(ChangeDiffPane, {
    props: {
      model: { status: 'ok', rows: [], added: 0, removed: 0 },
      before: '',
      after: '',
      theme: 'dark',
      ...props,
    },
  })
}

describe('ChangeDiffPane 行渲染', () => {
  it('每行铺左右两个单元格，行号与 del/add/ctx/gap 各自的类名跟着走', () => {
    const wrapper = mountPane({
      model: {
        status: 'ok',
        added: 1,
        removed: 1,
        rows: [
          row(1, 'keep', 'ctx', 1, 'keep', 'ctx'),
          row(2, 'old', 'del', 2, 'new', 'add'),
          { kind: 'row', left: { n: 3, text: 'gone', cls: 'del' }, right: { n: null, text: '', cls: 'gap' } },
        ],
      },
    })

    const rows = wrapper.findAll('.zc-cd-row')
    expect(rows).toHaveLength(3)
    expect(rows[0].findAll('.zc-cd-cell')).toHaveLength(2)
    expect(rows[0].findAll('.zc-cd-cell')[0].find('.zc-cd-no').text()).toBe('1')
    expect(rows[0].findAll('.zc-cd-cell')[0].find('.zc-cd-text').text()).toBe('keep')
    expect(rows[1].findAll('.zc-cd-cell')[0].classes()).toContain('is-del')
    expect(rows[1].findAll('.zc-cd-cell')[1].classes()).toContain('is-add')
    // gap 单元格仍占位（不能塌陷，否则左右两栏的行就错开了），但不给行号
    expect(rows[2].findAll('.zc-cd-cell')[1].classes()).toContain('is-gap')
    expect(rows[2].findAll('.zc-cd-cell')[1].find('.zc-cd-no').text()).toBe('')
  })

  it('边界：文本里的尖括号只当文本，不注入 HTML', () => {
    const wrapper = mountPane({
      model: {
        status: 'ok',
        added: 0,
        removed: 0,
        rows: [row(1, '<b>粗</b>', 'ctx', 1, '<b>粗</b>', 'ctx')],
      },
    })

    expect(wrapper.find('.zc-cd-text b').exists()).toBe(false)
    expect(wrapper.find('.zc-cd-text').text()).toBe('<b>粗</b>')
  })
})

describe('ChangeDiffPane 折叠未变更段', () => {
  const TANK = {
    before: Array.from({ length: 12 }, (_, i) => `L${i + 1}\n`).join(''),
    after: Array.from({ length: 12 }, (_, i) => (i === 0 ? 'X\n' : `L${i + 1}\n`)).join(''),
  }

  it('默认收成一条「展开 N 行未变更」，点一下才铺出两侧上下文行', async () => {
    const wrapper = mountPane({
      before: TANK.before,
      after: TANK.after,
      model: {
        status: 'ok',
        added: 1,
        removed: 1,
        rows: [
          row(1, 'L1', 'del', 1, 'X', 'add'),
          { kind: 'gutter', count: 11, leftStart: 2, rightStart: 2 },
        ],
      },
    })

    expect(wrapper.findAll('.zc-cd-row')).toHaveLength(1)
    const gutter = wrapper.find('.zc-cd-gutter')
    expect(gutter.text()).toContain('11')

    await gutter.trigger('click')

    expect(wrapper.findAll('.zc-cd-gutter')).toHaveLength(0)
    expect(wrapper.findAll('.zc-cd-row')).toHaveLength(12)
    const sixth = wrapper.findAll('.zc-cd-row')[5].findAll('.zc-cd-cell')[0]
    expect(sixth.find('.zc-cd-no').text()).toBe('6')
    expect(sixth.find('.zc-cd-text').text()).toBe('L6')
  })

  it('超过上限只铺一部分，剩下的还是一条能继续点的折叠段', async () => {
    const wrapper = mountPane({
      before: TANK.before,
      after: TANK.after,
      expandLimit: 4,
      model: {
        status: 'ok',
        added: 1,
        removed: 1,
        rows: [
          row(1, 'L1', 'del', 1, 'X', 'add'),
          { kind: 'gutter', count: 11, leftStart: 2, rightStart: 2 },
        ],
      },
    })

    await wrapper.find('.zc-cd-gutter').trigger('click')

    // 上限 4：铺 L2..L5 四行，剩 7 行仍是折叠段，且从 L6 起算
    expect(wrapper.findAll('.zc-cd-row')).toHaveLength(5)
    const rest = wrapper.find('.zc-cd-gutter')
    expect(rest.text()).toContain('7')
    const fifth = wrapper.findAll('.zc-cd-row')[4].findAll('.zc-cd-cell')[0]
    expect(fifth.find('.zc-cd-no').text()).toBe('5')
    expect(fifth.find('.zc-cd-text').text()).toBe('L5')

    // 再点一次续展 4 行，还剩 3 行——折叠段不会因为你点过一次就假装整段都展开了
    await rest.trigger('click')
    expect(wrapper.findAll('.zc-cd-row')).toHaveLength(9)
    expect(wrapper.find('.zc-cd-gutter').text()).toContain('3')

    await wrapper.find('.zc-cd-gutter').trigger('click')
    expect(wrapper.findAll('.zc-cd-row')).toHaveLength(12)
    expect(wrapper.findAll('.zc-cd-gutter')).toHaveLength(0)
  })
})

describe('ChangeDiffPane 两种降级', () => {
  it('diff 为空（写入与上一版一致）时只给一句话，不铺空栅格', () => {
    const wrapper = mountPane({ model: { status: 'identical', rows: [], added: 0, removed: 0 } })

    expect(wrapper.find('.zc-cd-notice').text()).toContain('与上一版一致')
    expect(wrapper.findAll('.zc-cd-row')).toHaveLength(0)
  })

  it('diff 对不齐全文时如实说明，并退回两侧全文（不假装逐行对齐）', () => {
    const wrapper = mountPane({
      model: { status: 'unaligned', rows: [], added: 0, removed: 0 },
      before: 'a\nb',
      after: 'a\nB',
    })

    expect(wrapper.find('.zc-cd-notice').text()).toContain('无法逐行对齐')
    const sides = wrapper.findAll('.zc-cd-plain')
    expect(sides).toHaveLength(2)
    expect(sides[0].findAll('.zc-cd-cell')).toHaveLength(2)
    const lastRight = sides[1].findAll('.zc-cd-cell')[1]
    expect(lastRight.find('.zc-cd-no').text()).toBe('2')
    expect(lastRight.find('.zc-cd-text').text()).toBe('B')
  })

  it('新建文件时左栏给「变更前不存在」，右栏照常铺全文', () => {
    const wrapper = mountPane({
      action: 'create',
      model: {
        status: 'ok',
        added: 2,
        removed: 0,
        rows: [
          { kind: 'row', left: { n: null, text: '', cls: 'gap' }, right: { n: 1, text: 'l1', cls: 'add' } },
          { kind: 'row', left: { n: null, text: '', cls: 'gap' }, right: { n: 2, text: 'l2', cls: 'add' } },
        ],
      },
    })

    expect(wrapper.find('.zc-cd-side-left').text()).toContain('变更前不存在')
    expect(wrapper.findAll('.zc-cd-row')).toHaveLength(2)
  })

  it('删除文件时右栏给「变更后已删除」', () => {
    const wrapper = mountPane({
      action: 'delete',
      model: {
        status: 'ok',
        added: 0,
        removed: 1,
        rows: [{ kind: 'row', left: { n: 1, text: 'l1', cls: 'del' }, right: { n: null, text: '', cls: 'gap' } }],
      },
    })

    expect(wrapper.find('.zc-cd-side-right').text()).toContain('变更后已删除')
  })
})
