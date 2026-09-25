// @vitest-environment happy-dom
// 预览日志面板接线：REST 拉历史 + /ws/deploy/{id} 追实时（seq 去重）、状态跟手、复制与清空。
//
// WS 用假实现（FakeWS）驱动：组件只认 onopen/onmessage/onclose 三个回调，
// 测试据此模拟「服务端推了一帧」，不去碰真网络。
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const devLogs = vi.fn()
vi.mock('../api', () => ({
  devLogs: (...args) => devLogs(...args),
  deployWsUrl: (id) => `ws://t/ws/deploy/${id}`,
}))

import DeployLogPanel from './DeployLogPanel.vue'

class FakeWS {
  static instances = []
  constructor(url) {
    this.url = url
    this.closed = false
    FakeWS.instances.push(this)
  }
  close() { this.closed = true }
  emit(obj) { this.onmessage && this.onmessage({ data: JSON.stringify(obj) }) }
}

const lineRows = [
  { seq: 1, ts: '2026-09-20T22:27:58+08:00', source: 'platform', service: '平台',
    line: '[zerocode] ✓ 预览已就绪 http://localhost:8213' },
  { seq: 0, ts: '2026-09-20T22:27:59+08:00', source: 'container', service: 'backend',
    line: 'INFO:     Uvicorn running on http://0.0.0.0:8000' },
]

function stubs() {
  return {
    'a-space': { template: '<div><slot /></div>' },
    'a-tag': { template: '<span><slot /></span>', props: ['color'] },
    // a-button 用原生 button 桩：不声明 emits，@click 作为 attr 继承落到根元素，trigger 才等价真点击
    'a-button': { template: '<button><slot /></button>', props: ['size', 'loading'] },
    'a-checkbox': { template: '<label><slot /></label>', props: ['checked'],
                    emits: ['update:checked'] },
  }
}

async function mountPanel(props = {}) {
  const w = mount(DeployLogPanel, {
    props: { appId: 5, appName: '陌如玉', active: true, ...props },
    global: { stubs: stubs() },
  })
  await flushPromises()
  return w
}

beforeEach(() => {
  FakeWS.instances = []
  devLogs.mockReset()
  devLogs.mockResolvedValue({ data: { lines: lineRows, note: '', status_dev: 'running',
                                      url: 'http://localhost:8213', last_seq: 1 } })
  globalThis.WebSocket = FakeWS
  Object.defineProperty(globalThis.navigator, 'clipboard',
                        { value: { writeText: vi.fn().mockResolvedValue(undefined) },
                          configurable: true })
})

describe('DeployLogPanel', () => {
  it('打开即拉历史并渲染两路来源的行', async () => {
    const w = await mountPanel()
    expect(devLogs).toHaveBeenCalledTimes(1)
    expect(devLogs).toHaveBeenCalledWith(5, 300)
    const rows = w.findAll('.zc-dlog-line')
    expect(rows).toHaveLength(2)
    expect(rows[0].text()).toContain('[zerocode] ✓ 预览已就绪')
    expect(rows[0].classes()).toContain('ok')       // 成功行绿色
    expect(rows[1].classes()).toContain('out')      // 容器输出走中性色
  })

  it('WS 的 dev_log 逐行上屏，重复 seq 不双份', async () => {
    const w = await mountPanel()
    const sock = FakeWS.instances[0]
    expect(sock.url).toBe('ws://t/ws/deploy/5')
    sock.onopen()
    await flushPromises()                                      // 状态翻转要等一次渲染
    expect(w.text()).toContain('实时')

    sock.emit({ type: 'dev_log', seq: 2, line: '[zerocode] ▶ 停止预览（dev）' })
    await flushPromises()
    expect(w.findAll('.zc-dlog-line')).toHaveLength(3)

    sock.emit({ type: 'dev_log', seq: 1, line: '[zerocode] ✓ 预览已就绪 http://localhost:8213' })
    await flushPromises()
    expect(w.findAll('.zc-dlog-line')).toHaveLength(3)    // REST 历史里已有 seq=1 → 丢弃

    sock.emit({ type: 'ping' })
    sock.emit({ type: 'hello', app_id: 5 })
    await flushPromises()
    expect(w.findAll('.zc-dlog-line')).toHaveLength(3)    // 非日志帧不进面板
  })

  it('dev_status 只改状态标签，migrate_result 补一行中文摘要', async () => {
    const w = await mountPanel()
    const sock = FakeWS.instances[0]
    sock.emit({ type: 'dev_status', status: 'starting' })
    await flushPromises()
    expect(w.findAll('.zc-dlog-line')).toHaveLength(2)
    expect(w.text()).toContain('启动中…')

    sock.emit({ type: 'migrate_result', env: 'dev', ok: false, log: 'FAIL V1.sql' })
    await flushPromises()
    const last = w.findAll('.zc-dlog-line').at(-1)
    expect(last.text()).toContain('✗ 迁移失败（dev）')
    expect(last.classes()).toContain('err')
  })

  it('dev_reset_result 补一行重建结果摘要（成功 ok / 失败 err）', async () => {
    const w = await mountPanel()
    const sock = FakeWS.instances[0]
    sock.emit({ type: 'dev_reset_result', env: 'dev', ok: true, code: 0 })
    await flushPromises()
    const last = w.findAll('.zc-dlog-line').at(-1)
    expect(last.text()).toContain('✓ dev 库重建完成（dev）')
    expect(last.classes()).toContain('ok')
    sock.emit({ type: 'dev_reset_result', env: 'dev', ok: false, code: -1 })
    await flushPromises()
    expect(w.findAll('.zc-dlog-line').at(-1).text()).toContain('✗ dev 库重建失败（dev）')
  })

  it('note 与失败原因照面：Docker 没开也能看到平台步骤行', async () => {
    devLogs.mockResolvedValue({ data: { lines: [lineRows[0]],
                                        note: '容器日志暂不可用：Docker 未启动或不可用',
                                        status_dev: 'stopped', last_seq: 1 } })
    const w = await mountPanel()
    expect(w.find('.zc-dlog-note').text()).toContain('Docker 未启动')
    expect(w.findAll('.zc-dlog-line')).toHaveLength(1)
  })

  it('复制全部导出纯文本；清空视图只清屏不丢服务端历史', async () => {
    const w = await mountPanel()
    await w.findAll('button')[1].trigger('click')
    expect(globalThis.navigator.clipboard.writeText).toHaveBeenCalledTimes(1)
    const text = globalThis.navigator.clipboard.writeText.mock.calls[0][0]
    expect(text).toContain('| [zerocode] ✓ 预览已就绪')
    expect(text.split('\n')).toHaveLength(2)

    await w.findAll('button')[2].trigger('click')
    expect(w.findAll('.zc-dlog-line')).toHaveLength(0)
    expect(w.text()).toContain('暂无日志')
  })

  it('关闭抽屉即断开 WS；重新打开会再拉一次并续上', async () => {
    const w = await mountPanel()
    const first = FakeWS.instances[0]
    await w.setProps({ active: false })
    expect(first.closed).toBe(true)
    expect(w.vm.live).toBe(false)

    await w.setProps({ active: true })
    await flushPromises()
    expect(devLogs).toHaveBeenCalledTimes(2)
    expect(FakeWS.instances).toHaveLength(2)
    w.unmount()
    expect(FakeWS.instances[1].closed).toBe(true)
  })

  it('认证拒绝（4401）：不重连，引导去登录页（Ticket 009）', async () => {
    const assign = vi.fn()
    const saved = window.location
    Object.defineProperty(window, 'location', { value: { assign }, writable: true, configurable: true })
    try {
      await mountPanel()
      const sock = FakeWS.instances[0]
      sock.onclose({ code: 4401 })
      expect(assign).toHaveBeenCalledWith('/login')
      // 原重连排在 500ms 后：等过那个窗口，确认没有新连接被 scheduled
      await new Promise((r) => setTimeout(r, 1100))
      expect(FakeWS.instances.length).toBe(1)
    } finally {
      Object.defineProperty(window, 'location', { value: saved, writable: true, configurable: true })
    }
  })

  it('拉取失败不白屏：错误进 note', async () => {
    devLogs.mockRejectedValue({ response: { data: { detail: '应用不存在' } } })
    const w = await mountPanel()
    expect(w.find('.zc-dlog-note').text()).toBe('应用不存在')
  })
})
