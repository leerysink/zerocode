// @vitest-environment happy-dom
// Task 10：PublishModal——发布/回滚进度弹层行为契约（规格 04 §5 + 08 UI）。
// useDeploy 整体替身（WS 与状态流在 T9 已单测），这里只验组件接线：
// 生命周期（open→connect / close 不泄漏）、steps 渲染、409 chat_active 确认链、
// 完成态域名 URL 展示与复制、回滚 autoStart 单点触发 rollbackApp。
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { h, ref } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'

const mocks = vi.hoisted(() => ({
  useDeploy: vi.fn(),
  api: { appUrls: vi.fn(), publishApp: vi.fn(), rollbackApp: vi.fn(),
    scaffoldBackfill: vi.fn() },
  antd: {
    message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
    Modal: { confirm: vi.fn() },
  },
}))
vi.mock('../composables/useDeploy', () => ({ useDeploy: mocks.useDeploy }))
vi.mock('../api', () => mocks.api)
vi.mock('ant-design-vue', () => mocks.antd)

import PublishModal from './PublishModal.vue'

const WAIT = { preflight: 'wait', build: 'wait', provision: 'wait', migrate: 'wait',
  deploy: 'wait', health: 'wait', record: 'wait' }

function fakeDep() {
  const dep = {
    connected: ref(false), steps: ref({ ...WAIT }), buildTail: ref([]),
    prodStatus: ref(null), devStatus: ref(null), lastDone: ref(null), migrateResult: ref(null),
    connect: vi.fn(), close: vi.fn(), reset: vi.fn(), on: vi.fn(),
  }
  mocks.useDeploy.mockReturnValue(dep)
  return dep
}

const APP = { id: 7, name: '待办', slug: 'todo8', stack: 'vue3-fastapi',
  prod_port: 8210, current_version: null, status_prod: 'stopped' }

const STUBS = {
  'a-modal': {
    name: 'a-modal', props: ['open'],
    render() {
      if (!this.open) return null
      return h('div', { class: 'stub-modal' }, this.$slots.default ? this.$slots.default() : [])
    },
  },
  'a-button': {
    name: 'a-button',
    render() {
      return h('button', { class: 'stub-btn', onClick: (e) => this.$emit('click', e) },
        this.$slots.default ? this.$slots.default() : [])
    },
  },
  'a-space': { name: 'a-space', render() { return h('div', this.$slots.default?.()) } },
  'a-select': { name: 'a-select', props: ['value', 'options'], render() { return h('span', 'select') } },
}

function mountModal(props = {}) {
  const dep = fakeDep()
  const wrapper = mount(PublishModal, {
    props: { app: APP, open: true, ...props },
    global: { stubs: STUBS },
  })
  return { wrapper, dep }
}

let writeText
beforeEach(() => {
  vi.clearAllMocks()
  writeText = vi.fn().mockResolvedValue(undefined)
  Object.defineProperty(window.navigator, 'clipboard', { value: { writeText }, configurable: true })
  mocks.api.appUrls.mockResolvedValue({ data: { domain: 'https://todo8.zerocode.com' } })
})
afterEach(() => vi.restoreAllMocks())

describe('PublishModal WS 生命周期', () => {
  it('open 变 true 才 connect，变 false 立即 close（不泄漏后台连接）', async () => {
    const dep = fakeDep()
    const wrapper = mount(PublishModal, {
      props: { app: APP, open: false }, global: { stubs: STUBS },
    })
    expect(dep.connect).not.toHaveBeenCalled()
    await wrapper.setProps({ open: true })
    expect(dep.connect).toHaveBeenCalledTimes(1)
    await wrapper.setProps({ open: false })
    expect(dep.close).toHaveBeenCalledTimes(1)
    // 卸载兜底：父级 v-if 直接摘除时也要 close
    await wrapper.setProps({ open: true })
    wrapper.unmount()
    expect(dep.close).toHaveBeenCalledTimes(2)
  })
})

describe('PublishModal 七步进度', () => {
  it('steps 状态映射渲染：ok 绿勾 / running 蓝转 / wait 灰点', async () => {
    const { wrapper, dep } = mountModal()
    dep.steps.value = { ...WAIT, preflight: 'ok', build: 'running' }
    await flushPromises()
    expect(wrapper.findAll('.st-ok')).toHaveLength(1)
    const running = wrapper.find('.st-running')
    expect(running.text()).toContain('镜像构建')
    expect(wrapper.findAll('.st-wait')).toHaveLength(5)
  })

  it('失败收口时 running 步骤补红叉（事件流不再发 publish_step failed）', async () => {
    const { wrapper, dep } = mountModal()
    dep.steps.value = { ...WAIT, build: 'running' }
    dep.lastDone.value = { type: 'publish_done', ok: false, error: 'x'.repeat(300) }
    await flushPromises()
    expect(wrapper.find('.st-failed').text()).toContain('镜像构建')
    expect(wrapper.find('.zc-pub-fail').text().length).toBeLessThanOrEqual(200)
  })
})

describe('PublishModal 发布链', () => {
  it('点击立即发布：先 reset 再 publishApp(id, false)', async () => {
    mocks.api.publishApp.mockResolvedValue({ data: { ok: true } })
    const { wrapper, dep } = mountModal()
    await wrapper.find('.zc-pub-foot .stub-btn').trigger('click')
    await flushPromises()
    expect(dep.reset).toHaveBeenCalled()
    expect(mocks.api.publishApp).toHaveBeenCalledWith(7, false, false)
  })

  it('publishing 进行中不重复受理（重复点击守卫）', async () => {
    const { wrapper, dep } = mountModal()
    dep.prodStatus.value = 'publishing'
    await wrapper.find('.zc-pub-foot .stub-btn').trigger('click')
    await flushPromises()
    expect(mocks.api.publishApp).not.toHaveBeenCalled()
  })

  it('409 chat_active → Modal.confirm，确认后 force=true 重发', async () => {
    mocks.api.publishApp.mockRejectedValueOnce({ response: { status: 409,
      data: { detail: { code: 'chat_active', message: '当前 AI 正在修改代码' } } } })
    mocks.api.publishApp.mockResolvedValue({ data: { ok: true } })
    const { wrapper } = mountModal()
    await wrapper.find('.zc-pub-foot .stub-btn').trigger('click')
    await flushPromises()
    const arg = mocks.antd.Modal.confirm.mock.calls[0][0]
    expect(arg.title).toBe('AI 正在修改代码')
    expect(arg.okText).toBe('继续发布')
    await arg.onOk()
    await flushPromises()
    expect(mocks.api.publishApp).toHaveBeenLastCalledWith(7, true, false)
  })

  it('非 chat_active 错误走 message.error，不弹确认', async () => {
    mocks.api.publishApp.mockRejectedValueOnce({ response: { status: 503,
      data: { detail: 'Docker 未就绪' } } })
    const { wrapper } = mountModal()
    await wrapper.find('.zc-pub-foot .stub-btn').trigger('click')
    await flushPromises()
    expect(mocks.antd.Modal.confirm).not.toHaveBeenCalled()
    expect(mocks.antd.message.error).toHaveBeenCalledWith('Docker 未就绪')
  })
})

describe('PublishModal 破坏性确认交互', () => {
  function destructive() {
    const ctx = mountModal()
    ctx.dep.lastDone.value = { type: 'publish_done', ok: false, error: '破坏性 blob...',
      code: 'schema_destructive',
      destructive: ['删除表 gone', '删除列 demo_notes.title（改名亦属此，需两步走）'] }
    return ctx
  }

  it('code=schema_destructive → 渲染结构化清单 + 两按钮，不再是红字 blob', async () => {
    const { wrapper } = destructive()
    await flushPromises()
    const list = wrapper.find('.zc-pub-destr')
    expect(list.exists()).toBe(true)
    expect(list.text()).toContain('demo_notes.title')
    const txt = wrapper.text()
    expect(txt).toContain('我已确认风险，继续发布')
    expect(txt).toContain('生成回填骨架')
  })

  it('「继续发布」→ publishApp(id, true, true)（force + confirmDestructive）', async () => {
    mocks.api.publishApp.mockResolvedValue({ data: { ok: true } })
    const { wrapper } = destructive()
    await flushPromises()
    const btn = wrapper.findAll('.zc-pub-destr ~ * .stub-btn, .stub-btn')
      .find((b) => b.text().includes('我已确认风险'))
    await btn.trigger('click')
    await flushPromises()
    expect(mocks.api.publishApp).toHaveBeenLastCalledWith(7, true, true)
  })

  it('「生成回填骨架」→ scaffoldBackfill(id) 成功提示落盘文件名', async () => {
    mocks.api.scaffoldBackfill.mockResolvedValue({ data: { filename: 'D20260922_01_backfill.sql' } })
    const { wrapper } = destructive()
    await flushPromises()
    const btn = wrapper.findAll('.stub-btn')
      .find((b) => b.text().includes('生成回填骨架'))
    await btn.trigger('click')
    await flushPromises()
    expect(mocks.api.scaffoldBackfill).toHaveBeenCalledWith(7)
    expect(mocks.antd.message.success).toHaveBeenCalledWith(
      expect.stringContaining('D20260922_01_backfill.sql'))
  })

  it('普通失败（无 code）仍走原 failText + 重试，不显破坏清单', async () => {
    const { wrapper, dep } = mountModal()
    dep.lastDone.value = { type: 'publish_done', ok: false, error: '镜像构建失败' }
    await flushPromises()
    expect(wrapper.find('.zc-pub-destr').exists()).toBe(false)
    expect(wrapper.find('.zc-pub-fail').text()).toContain('镜像构建失败')
  })
})

describe('PublishModal 完成态', () => {
  async function doneOk() {
    const ctx = mountModal()
    ctx.dep.lastDone.value = { type: 'publish_done', ok: true,
      url: 'https://todo8.zerocode.com', version: 'b20260921120000' }
    await flushPromises()
    return ctx
  }

  it('大号 URL 展示 + 点击复制（发布完成以网关域名为准）', async () => {
    const { wrapper } = await doneOk()
    const link = wrapper.find('.zc-pub-url')
    expect(link.text()).toContain('https://todo8.zerocode.com')
    await link.trigger('click')
    expect(writeText).toHaveBeenCalledWith('https://todo8.zerocode.com')
  })

  it('无 done.url 时回退到 appUrls 域名', async () => {
    const ctx = mountModal()
    ctx.dep.lastDone.value = { type: 'publish_done', ok: true, version: 'b20260921120000' }
    await flushPromises()
    expect(ctx.wrapper.find('.zc-pub-url').text()).toContain('https://todo8.zerocode.com')
  })

  it('收口即 emit done 通知父级刷新', async () => {
    const { wrapper, dep } = mountModal()
    dep.lastDone.value = { type: 'publish_done', ok: true, url: 'http://x' }
    await flushPromises()
    expect(wrapper.emitted('done')).toBeTruthy()
  })
})

describe('PublishModal 回滚模式', () => {
  it('autoStart：打开即调 rollbackApp(id, version)（历史页确认后单点触发）', async () => {
    mocks.api.rollbackApp.mockResolvedValue({ data: { ok: true } })
    mountModal({ mode: 'rollback', rollbackVersion: 'b20260920100000', autoStart: true })
    await flushPromises()
    expect(mocks.api.rollbackApp).toHaveBeenCalledWith(7, 'b20260920100000')
  })

  it('回滚受理失败展示错误不静默，autoStart 链收弹层不空转', async () => {
    mocks.api.rollbackApp.mockRejectedValueOnce({ response: { status: 400,
      data: { detail: '镜像已清理，无法回滚到该版本' } } })
    const { wrapper, dep } = mountModal({ mode: 'rollback', rollbackVersion: 'b1', autoStart: true })
    await flushPromises()
    expect(mocks.antd.message.error).toHaveBeenCalledWith('镜像已清理，无法回滚到该版本')
    expect(dep.reset).toHaveBeenCalled()
    expect(wrapper.emitted('update:open')?.at(-1)).toEqual([false])
  })
})
