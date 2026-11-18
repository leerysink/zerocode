// @vitest-environment happy-dom
// 委派卡（ADR-0052 / 规格 2026-11-18-agent-subruns §2.5）：每行一个子 run 的 title、状态徽标
// （运行中/已完成/失败/已中断）与回执摘要；运行中步骤直接铺开（不设折叠，跟着事件长出来），
// 落定后收进折叠、默认收起。状态只读归一化后的字段——实时与刷新后同形。
import { describe, it, expect } from 'vitest'
import { h } from 'vue'
import { mount } from '@vue/test-utils'

import DelegateCard from './DelegateCard.vue'

const SpinStub = {
  name: 'a-spin',
  render() { return h('span', { class: 'stub-spin' }) },
}

function mountCard(msg) {
  return mount(DelegateCard, { props: { msg }, global: { stubs: { 'a-spin': SpinStub } } })
}

describe('DelegateCard 四态渲染', () => {
  const FINISHED = {
    role: 'tool', name: 'delegate',
    args: { tasks: [{ title: '调研 A', brief: 'a' }, { title: '调研 B', brief: 'b' }] },
    result: { ok: true, subruns: [] },
    subruns: [
      {
        index: 0, title: '调研 A', status: 'completed', result: 'A 的结论',
        steps: [{ name: 'read_file', args: { path: 'a.txt' }, result: { ok: true } }],
      },
      { index: 1, title: '调研 B', status: 'error', error: '模型连续返回空回复' },
    ],
  }

  it('头部给出子任务数，每行 title + 状态徽标 + 回执摘要', () => {
    const w = mountCard(FINISHED)
    expect(w.find('.zc-delegate-head').text()).toContain('委派 2 个子任务')
    const rows = w.findAll('.zc-delegate-row')
    expect(rows).toHaveLength(2)
    expect(rows[0].text()).toContain('调研 A')
    expect(rows[0].text()).toContain('已完成')
    expect(rows[0].text()).toContain('A 的结论')
    expect(rows[1].text()).toContain('调研 B')
    expect(rows[1].text()).toContain('失败')
    expect(rows[1].text()).toContain('模型连续返回空回复')
  })

  it('已中断单独成一态（停止时没有 done 的子 run）', () => {
    const w = mountCard({
      role: 'tool', name: 'delegate',
      args: { tasks: [{ title: '调研 A', brief: 'a' }] },
      result: { cancelled: true },
      subruns: [{ index: 0, title: '调研 A', status: 'cancelled', steps: [{ name: 'search', args: { q: 'x' } }] }],
    })
    const row = w.find('.zc-delegate-row')
    expect(row.text()).toContain('已中断')
    expect(row.text()).not.toContain('失败')
  })

  it('委派校验失败：头部直接给出错误，不编造子任务行', () => {
    const w = mountCard({
      role: 'tool', name: 'delegate', args: { tasks: [] },
      result: { ok: false, error: '一次最多委派 5 个子任务，请拆成多批' },
    })
    expect(w.find('.zc-delegate-fail').text()).toContain('一次最多委派 5 个子任务')
    expect(w.findAll('.zc-delegate-row')).toHaveLength(0)
  })
})

describe('DelegateCard 步骤展开', () => {
  it('运行中：步骤直接铺开（不设折叠）、显示进度行与转圈', () => {
    const w = mountCard({
      role: 'tool', name: 'delegate',
      args: { tasks: [{ title: '调研 A', brief: 'a' }] },
      subruns: [{
        index: 0, title: '调研 A', status: 'running',
        progress: { name: 'preview_status', line: '正在等待构建' },
        steps: [{ name: 'search', args: { q: 'x' } }],
      }],
    })
    expect(w.find('.stub-spin').exists()).toBe(true)
    expect(w.text()).toContain('运行中')
    expect(w.text()).toContain('正在等待构建')
    expect(w.find('.zc-delegate-step').text()).toContain('search')
    expect(w.find('details.zc-delegate-detail').exists()).toBe(false) // 运行中不折叠
  })

  it('落定后默认收起：步骤与文本留痕进 details，点开才看', () => {
    const w = mountCard({
      role: 'tool', name: 'delegate',
      args: { tasks: [{ title: '调研 A', brief: 'a' }] },
      result: { ok: true, subruns: [] },
      subruns: [{
        index: 0, title: '调研 A', status: 'completed', result: '结论',
        texts: ['中间过程'],
        steps: [{ name: 'read_file', args: { path: 'a.txt' }, result: { ok: true } }],
      }],
    })
    const details = w.find('details.zc-delegate-detail')
    expect(details.exists()).toBe(true)
    expect(details.attributes('open')).toBeUndefined() // 默认收起
    expect(details.text()).toContain('中间过程')
    expect(details.text()).toContain('read_file')
    expect(details.text()).toContain('a.txt')
  })
})
