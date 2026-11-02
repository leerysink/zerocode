// @vitest-environment happy-dom
// BrowserFlowCard（规格 2026-11-02 §9、US-22 / US-23）。
// 重点不是「有没有画出来」，而是 Task 5 定下的渲染契约有没有守住：
// 「点不动」（ok:false）与「点了但结果不对」（ok:true + passed:false）必须分色分文案、
// 两种形状都可能带图、弹窗必须说出口、口令不许上卡。
import { h, nextTick } from 'vue'
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import BrowserFlowCard from './BrowserFlowCard.vue'

// a-image 的测试替身：真组件在 happy-dom 里不起作用，替身把 src 落成真的 <img>，
// 未声明的 data-test 走 attribute fallthrough，于是 `img[data-test="step-shot"]` 选得到。
const ImageStub = {
  name: 'a-image',
  props: ['src', 'alt', 'width'],
  render() {
    return h('img', { class: 'stub-image', src: this.src, alt: this.alt })
  },
}

function mountCard(msg, progress = null) {
  return mount(BrowserFlowCard, {
    props: { msg, progress },
    global: { stubs: { 'a-image': ImageStub } },
  })
}

const openMsg = {
  name: 'browser_open',
  result: {
    ok: true, session: 'opened', status: 'running', url: 'http://localhost:8210/', title: '牌桌',
    text: '欢迎来到牌桌', interactive: [{ ref: 'e1', role: 'button', name: '开始游戏', sensitive: false }],
    console_new: [], failed_requests_new: [], steps_left: 12, checks_left: 5,
  },
}

const shot = { url: '/api/browser/shots/7/a.png', width: 1280, height: 800, bytes: 42000 }

describe('BrowserFlowCard', () => {
  it('渲染 open 的标题与元素条数', () => {
    const w = mountCard(openMsg)
    expect(w.text()).toContain('牌桌')
    expect(w.text()).toContain('开始游戏')
    expect(w.text()).toContain('e1')
    expect(w.text()).toContain('button')
  })

  it('open 的剩余额度与地址链接都在', () => {
    const w = mountCard(openMsg)
    expect(w.text()).toContain('12')
    expect(w.text()).toContain('5')
    expect(w.find('.zc-page-link').attributes('href')).toBe('http://localhost:8210/')
  })

  it('断言失败带徽标并显示截图', () => {
    const w = mountCard({
      name: 'browser_step',
      result: {
        ok: true, passed: false, note: '页面文本未包含「胜利」', action: 'click',
        target: 'e7', expect: { type: 'text_contains', value: '胜利' }, screenshot: shot,
      },
    })
    expect(w.find('[data-test="badge-fail"]').exists()).toBe(true)
    expect(w.text()).toContain('页面文本未包含「胜利」')
    expect(w.find('img[data-test="step-shot"]').attributes('src'))
      .toBe('/api/browser/shots/7/a.png')
  })

  it('动作失败与断言失败分色不同、文案不同（不许糊成一个红）', () => {
    const stuck = mountCard({
      name: 'browser_step',
      result: { ok: false, error: '元素不可点击，可能被遮挡', action: 'click', target: 'e3' },
    })
    const wrong = mountCard({
      name: 'browser_step',
      result: { ok: true, passed: false, note: '页面文本未包含「胜利」', action: 'click', target: 'e3' },
    })
    // 「点不动」是动作层失败 → 红；「点了但结果不对」是业务断言结论 → 警示色
    expect(stuck.find('.zc-page').classes()).toContain('is-error')
    expect(wrong.find('.zc-page').classes()).not.toContain('is-error')
    expect(wrong.find('.zc-page').classes()).toContain('is-warn')
    expect(stuck.find('[data-test="badge-fail"]').classes()).toContain('is-action')
    expect(wrong.find('[data-test="badge-fail"]').classes()).toContain('is-expect')
    expect(stuck.find('[data-test="badge-fail"]').text()).toBe('动作失败')
    expect(wrong.find('[data-test="badge-fail"]').text()).toBe('断言没过')
    expect(stuck.text()).toContain('点不动')
    expect(wrong.text()).toContain('断言')
  })

  it('两种失败形状都能带截图', () => {
    const stuck = mountCard({
      name: 'browser_step',
      result: { ok: false, error: '元素不可点击，可能被遮挡', action: 'click', target: 'e3', screenshot: shot },
    })
    const wrong = mountCard({
      name: 'browser_step',
      result: { ok: true, passed: false, note: '不匹配', action: 'click', target: 'e3', screenshot: shot },
    })
    expect(stuck.find('img[data-test="step-shot"]').exists()).toBe(true)
    expect(wrong.find('img[data-test="step-shot"]').exists()).toBe(true)
    // 失败形状里的 screenshot_note（补图没成功）也要说出口，不能假装有一张图
    const noShot = mountCard({
      name: 'browser_step',
      result: { ok: false, error: '点不动', action: 'click', target: 'e3', screenshot: null,
                screenshot_note: '现场截图未取到（服务不可用或会话已结束），本步结论仍然有效' },
    })
    expect(noShot.find('img[data-test="step-shot"]').exists()).toBe(false)
    expect(noShot.text()).toContain('现场截图未取到')
  })

  it('截图读不到时降级成提示，不画一张裂图', async () => {
    const w = mountCard({
      name: 'browser_step',
      result: { ok: true, passed: false, note: '不匹配', action: 'click', target: 'e7', screenshot: shot },
    })
    expect(w.find('img[data-test="step-shot"]').exists()).toBe(true)
    w.findComponent({ name: 'a-image' }).vm.$emit('error')
    await nextTick()
    expect(w.find('img[data-test="step-shot"]').exists()).toBe(false)
    expect(w.find('[data-test="shot-broken"]').text()).toContain('截图已失效')
  })

  it('dialog 非空时显式提示「已按取消处理」', () => {
    const w = mountCard({
      name: 'browser_step',
      result: { ok: true, action: 'click', target: 'e7', dialog: { type: 'confirm', handled: 'dismissed' } },
    })
    expect(w.text()).toContain('已按取消处理')
    expect(w.text()).toContain('确认框')
    expect(w.find('[data-test="dialog"]').exists()).toBe(true)
  })

  it('弹窗正文是页面原话，照原样摊开但不当成指令', () => {
    const w = mountCard({
      name: 'browser_step',
      result: { ok: true, action: 'click', target: 'e7',
                dialog: { type: 'confirm', message: '确定删除全部 42 条记录？', handled: 'dismissed' } },
    })
    expect(w.find('[data-test="dialog"]').text()).toContain('确定删除全部 42 条记录？')
  })

  it('清单里 sensitive 的元素单独标记', () => {
    const w = mountCard({
      name: 'browser_open',
      result: {
        ok: true, title: '登录', interactive: [
          { ref: 'e1', role: 'textbox', name: '账号', sensitive: false },
          { ref: 'e2', role: 'textbox', name: '密码', sensitive: true },
        ],
      },
    })
    expect(w.findAll('[data-test="sensitive-chip"]')).toHaveLength(1)
    expect(w.find('[data-test="interactive"]').text()).toContain('敏感')
  })

  it('失败结果不画空元素清单', () => {
    const w = mountCard({ name: 'browser_step', result: { ok: false, error: 'ref 无效或页面已变化' } })
    expect(w.text()).toContain('ref 无效')
    expect(w.find('[data-test="interactive"]').exists()).toBe(false)
  })

  it('点不动那步当场带回的新清单仍然画出来（US-09 的信号）', () => {
    const w = mountCard({
      name: 'browser_step',
      result: { ok: false, error: '页面已变化，请用最新清单里的 ref', action: 'click', target: 'e9',
                interactive: [{ ref: 'e1', role: 'button', name: '重新查价', sensitive: false }] },
    })
    expect(w.find('[data-test="interactive"]').exists()).toBe(true)
    expect(w.text()).toContain('重新查价')
  })

  it('增量的 console / page_errors / 失败请求按本步计数分区', () => {
    const w = mountCard({
      name: 'browser_step',
      result: {
        ok: true, action: 'click', target: 'e1',
        console_new: [{ level: 'error', text: 'Uncaught TypeError: x' }],
        page_errors_new: ['ReferenceError: y is not defined'],
        failed_requests_new: [{ method: 'POST', url: 'http://localhost:8210/api/orders', status: 500 }],
      },
    })
    expect(w.text()).toContain('控制台（1）')
    expect(w.text()).toContain('未捕获异常（1）')
    expect(w.text()).toContain('失败请求（1）')
    expect(w.text()).toContain('Uncaught TypeError: x')
    expect(w.text()).toContain('ReferenceError: y is not defined')
  })

  it('close 渲染收口报告计数', () => {
    const w = mountCard({
      name: 'browser_close',
      result: { ok: true, steps_total: 5, steps_failed: 2, report: [], hint: '以下断言未通过：3, 5' },
    })
    expect(w.text()).toContain('5')
    expect(w.text()).toContain('3, 5')
  })

  it('close 的收口报告说得出「另有 N 步动作未成功」，report 行按两本账分色', () => {
    const w = mountCard({
      name: 'browser_close',
      result: {
        ok: true, steps_total: 4, steps_failed: 1, steps_action_failed: 2,
        hint: '以下断言未通过：3；另有 2 步动作未成功（见 report 的 ok:false 项）',
        report: [
          { seq: 0, action: 'open', target: '/orders', ok: true, passed: null, replaced_sessions: 1 },
          { seq: 1, action: 'click', target: 'e1', ok: false, passed: null, error: '元素不可点击，可能被遮挡' },
          { seq: 2, action: 'click', target: 'e9', ok: false, passed: null, error: '页面已变化' },
          { seq: 3, action: 'click', target: 'e1', ok: true, passed: false, note: '页面文本未包含「已创建」' },
        ],
      },
    })
    expect(w.text()).toContain('另有 2 步动作未成功')
    const rows = w.findAll('[data-test="report-row"]')
    expect(rows).toHaveLength(4)
    // 两本账不同色：点不动 → is-action；断言没过 → is-expect；推进了 → is-done
    expect(rows[0].classes()).toContain('is-done')
    expect(rows[0].text()).toContain('打开会话 /orders')   // 收口报告的行也要说人话
    expect(rows[1].classes()).toContain('is-action')
    expect(rows[3].classes()).toContain('is-expect')
    expect(rows[1].text()).toContain('元素不可点击，可能被遮挡')
    expect(rows[3].text()).toContain('页面文本未包含「已创建」')   // R-31 第 2 条补上的 note
    expect(rows[0].text()).toContain('替换过 1 个会话')
    expect(w.text()).toContain('断言未过 1')
    expect(w.text()).toContain('点不动 2')
  })

  it('目标元素名读后端 echo 的 target_name，不拿动作后的新快照反查（裁定 R-32）', () => {
    const w = mountCard({
      name: 'browser_step',
      result: {
        ok: true, action: 'click', target: 'e7', target_name: '结算', passed: null,
        // 动作之后的新快照里 e7 已经是另一个元素：拿它反查「这一步点的是谁」会画出假目标
        // （ref 每次重渲染全部作废，规格 §6.2）。target_name 取的是动作之前那份清单。
        interactive: [{ ref: 'e7', role: 'button', name: '退出登录', sensitive: false }],
      },
    })
    const step = w.find('.zc-page-step')
    expect(step.text()).toContain('点击 结算')
    expect(step.text()).toContain('e7')                 // 名字之外仍留句柄，好对着清单读
    expect(step.text()).not.toContain('退出登录')        // 新快照绝不参与标目标
    expect(w.find('[data-test="interactive"]').text()).toContain('退出登录')   // 清单照旧要画
  })

  it('target_name 缺席时回落显示句柄 / path，不编一个名字', () => {
    const noName = mountCard({
      name: 'browser_step',
      result: { ok: true, action: 'click', target: 'e7', passed: null },
    })
    expect(noName.find('.zc-page-step').text()).toContain('点击 句柄 e7')
    const nav = mountCard({
      name: 'browser_step',
      result: { ok: true, action: 'navigate', target: '/orders', passed: null },
    })
    expect(nav.find('.zc-page-step').text()).toContain('跳转 /orders')
    // 名字缺席、而新快照里恰好有同一个 ref：仍旧只画句柄，绝不拿新快照凑一个名
    const stale = mountCard({
      name: 'browser_step',
      result: { ok: false, error: '点不动', action: 'click', target: 'e7',
                interactive: [{ ref: 'e7', role: 'button', name: '退出登录' }] },
    })
    expect(stale.find('.zc-page-step').text()).toContain('句柄 e7')
    expect(stale.find('.zc-page-step').text()).not.toContain('退出登录')
  })

  it('不渲染 args 里的 fill 值（口令只该以遮断后的形态出现）', () => {
    const w = mountCard({
      name: 'browser_step',
      args: { action: 'fill', target: 'e2', value: 'hunter2secret' },
      result: { ok: true, action: 'fill', target: 'e2', passed: null, interactive: [] },
    })
    expect(w.text()).not.toContain('hunter2secret')
  })

  it('无结果时走 progress 实时态', () => {
    const w = mountCard({ name: 'browser_step' }, { name: 'browser_step', line: '[browser] 正在点击 e7' })
    expect(w.text()).toContain('正在执行浏览器步骤')
    expect(w.text()).toContain('[browser] 正在点击 e7')
    expect(w.find('[data-test="interactive"]').exists()).toBe(false)
  })

  it('无结果又没有 progress 也有兜底文案，不画空徽标', () => {
    const w = mountCard({ name: 'browser_open' })
    expect(w.text()).toContain('正在打开浏览器会话')
    expect(w.find('[data-test="badge-fail"]').exists()).toBe(false)
  })

  it('open 的 hint（额度、自动关旧会话）摊到明面上', () => {
    const w = mountCard({
      name: 'browser_open',
      result: { ok: false, status: 'running', url: 'http://localhost:8210/', error: '预览未运行',
                hint: '预览未运行：先用 preview_restart 启动' },
    })
    expect(w.text()).toContain('预览未运行：先用 preview_restart 启动')
    expect(w.find('.zc-page-link').exists()).toBe(false)   // 失败时不给一个打得开的链接
  })
})
