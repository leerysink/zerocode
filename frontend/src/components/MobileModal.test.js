// @vitest-environment happy-dom
// 移动端弹框（规格 2026-11-03 US-15~US-19 + 规格 2026-11-05 / ADR-0042）：卡面 ☰ 与聊天页
// 「更多」共用。钉住：后端字段驱动的地址与二维码、localhost 灰码（US-08）、正式未运行阻断、
// APK 异步构建状态/重试/轮询、图标覆盖与恢复；旧的 APK 手动上传入口已移除。
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { h } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'

const mocks = vi.hoisted(() => ({
  api: {
    appMobile: vi.fn(),
    rebuildMobileApk: vi.fn(),
    uploadMobileIcon: vi.fn(),
    resetMobileIcon: vi.fn(),
  },
  antd: { message: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() } },
}))
vi.mock('../api', () => mocks.api)
vi.mock('ant-design-vue', () => mocks.antd)

import MobileModal from './MobileModal.vue'

const ModalStub = {
  name: 'a-modal',
  props: ['open', 'title', 'width', 'centered', 'footer'],
  render() {
    return h('div', { class: 'stub-modal' }, [
      h('div', { class: 'stub-modal-title' }, String(this.title)),
      h('button', { class: 'stub-modal-close', onClick: () => this.$emit('update:open', false) }),
      this.$slots.default?.(),
    ])
  },
}
const ButtonStub = {
  name: 'a-button',
  props: ['disabled', 'loading'],
  render() {
    return h('button', {
      class: ['stub-btn', { 'is-loading': !!this.loading }],
      disabled: !!this.disabled,
      onClick: () => this.$emit('click'),
    }, this.$slots.default?.())
  },
}
const SpinStub = {
  name: 'a-spin',
  render() { return h('div', { class: 'stub-spin' }, this.$slots.default?.()) },
}

const APK = {
  status: 'ready',
  error: '',
  shell_version: 3,
  params_stale: false,
  package_name: 'com.example.bbb',
  can_rebuild: true,
  requested_at: '2026-11-05T10:00:00Z',
  built_at: '2026-11-05T10:02:00Z',
}

const INFO = {
  stack: 'vue3-fastapi',
  status_dev: 'running',
  status_prod: 'running',
  dev_url: 'http://localhost:8211/h5/',
  h5_url: 'https://bbb.example.test/h5/',
  apk_url: 'https://bbb.example.test/api/uploads/mobile/bbb.apk',
  apk_size: 2 * 1024 * 1024,
  apk_max_bytes: 200 * 1024 * 1024,
  apk: APK,
  icon: { custom: false, url: '/api/apps/12/mobile/icon?v=1', updated_at: null },
}

const mountOptions = {
  props: { app: { id: 12, name: '乙' }, open: true },
  global: { stubs: { 'a-modal': ModalStub, 'a-button': ButtonStub, 'a-spin': SpinStub } },
}

const mounted = []

async function mountModal(over = {}, { host = 'localhost' } = {}) {
  vi.stubGlobal('location', { hostname: host })
  mocks.api.appMobile.mockResolvedValue({ data: { ...INFO, ...over } })
  const wrapper = mount(MobileModal, mountOptions)
  mounted.push(wrapper)
  await flushPromises()
  return wrapper
}

async function pickIcon(wrapper, file) {
  const input = wrapper.find('.zc-icon-input')
  Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
  await input.trigger('change')
  await flushPromises()
}

let writeText

beforeEach(() => {
  vi.clearAllMocks()
  writeText = vi.fn().mockResolvedValue(undefined)
  Object.defineProperty(window.navigator, 'clipboard', { value: { writeText }, configurable: true })
})

afterEach(() => {
  while (mounted.length) mounted.pop().unmount()
  vi.unstubAllGlobals()
  vi.useRealTimers()
  delete document.execCommand
  document.body.innerHTML = ''
})

describe('MobileModal 环境与地址', () => {
  it('打开时按 app.id 拉信息，默认展示预览环境', async () => {
    const w = await mountModal()
    expect(mocks.api.appMobile).toHaveBeenCalledWith(12)
    expect(w.find('.stub-modal-title').text()).toContain('移动端 · 乙')
    expect(w.text()).toContain('http://localhost:8211/h5/')
    expect(w.find('.zc-m-tab.active').text()).toBe('预览环境')
  })

  it('浏览器经宿主 IP 访问时，dev 地址与码改用该 host（覆盖打包默认的 localhost）', async () => {
    const w = await mountModal({}, { host: '192.168.1.50' })
    expect(w.text()).toContain('http://192.168.1.50:8211/h5/')
    expect(w.find('.zc-qr-main .zc-qr-svg').exists()).toBe(true)
    expect(w.find('.zc-qr-main .zc-qr-blocked').exists()).toBe(false)
  })

  // US-08：扫 localhost 必然失败，宁可置灰也不给一张扫不开的码
  it('dev 地址落到 localhost：码置灰并说明把 ZEROCODE_PREVIEW_HOST 配成宿主 IP', async () => {
    const w = await mountModal()
    const blocked = w.find('.zc-qr-main .zc-qr-blocked')
    expect(blocked.exists()).toBe(true)
    expect(blocked.text()).toContain('ZEROCODE_PREVIEW_HOST')
    expect(blocked.text()).toContain('宿主 IP')
    expect(w.find('.zc-qr-main .zc-qr-svg').exists()).toBe(false)
  })

  // 显式配置过的宿主优先：浏览器在 localhost、后端已配成 LAN IP 时不许拿 localhost 覆盖它
  it('后端 dev 宿主已不是 localhost 时，浏览器在 localhost 也保留后端投影', async () => {
    const w = await mountModal({ dev_url: 'http://10.0.0.8:8211/h5/' })
    expect(w.text()).toContain('http://10.0.0.8:8211/h5/')
    expect(w.find('.zc-qr-main .zc-qr-svg').exists()).toBe(true)
  })

  it('dev 未运行：码置灰、打开禁用', async () => {
    const w = await mountModal({ status_dev: 'stopped' })
    expect(w.find('.zc-qr-main .zc-qr-blocked').text()).toContain('预览未运行')
    expect(w.find('.zc-m-open').attributes('disabled')).toBeDefined()
  })

  it('切到正式环境：展示 h5_url，复制的是后端回值', async () => {
    const w = await mountModal()
    await w.findAll('.zc-m-tab')[1].trigger('click')
    expect(w.text()).toContain('https://bbb.example.test/h5/')
    await w.find('.zc-m-copy').trigger('click')
    await flushPromises()
    expect(writeText).toHaveBeenCalledWith('https://bbb.example.test/h5/')
    expect(mocks.antd.message.success).toHaveBeenCalledWith('已复制')
  })

  it('正式环境未运行：正式码置灰、打开禁用', async () => {
    const w = await mountModal({ status_prod: 'stopped' })
    await w.findAll('.zc-m-tab')[1].trigger('click')
    expect(w.find('.zc-qr-main .zc-qr-blocked').text()).toContain('正式环境未运行')
    expect(w.find('.zc-m-open').attributes('disabled')).toBeDefined()
  })

  it('打开按环境用后端地址，窗名区分 dev / prod', async () => {
    const open = vi.spyOn(window, 'open').mockReturnValue({})
    const w = await mountModal()
    await w.find('.zc-m-open').trigger('click')
    expect(open).toHaveBeenCalledWith('http://localhost:8211/h5/', 'zerocode-mobile-12-dev')
    await w.findAll('.zc-m-tab')[1].trigger('click')
    await w.find('.zc-m-open').trigger('click')
    expect(open).toHaveBeenCalledWith('https://bbb.example.test/h5/', 'zerocode-mobile-12-prod')
  })

  it('window.open 被拦截时给出提示', async () => {
    vi.spyOn(window, 'open').mockReturnValue(null)
    const w = await mountModal()
    await w.find('.zc-m-open').trigger('click')
    expect(mocks.antd.message.warning).toHaveBeenCalled()
  })

  it('关闭弹窗把 update:open 交回父级', async () => {
    const w = await mountModal()
    await w.find('.stub-modal-close').trigger('click')
    expect(w.emitted('update:open')).toEqual([[false]])
  })
})

describe('MobileModal APK 构建状态', () => {
  it('已就绪：显示体积、壳版本、包名、下载链接与下载码', async () => {
    const w = await mountModal()
    expect(w.find('.zc-apk-badge').text()).toBe('已就绪')
    expect(w.text()).toContain('2.0 MB')
    expect(w.text()).toContain('壳 v3')
    expect(w.text()).toContain('com.example.bbb')
    expect(w.find('.zc-apk-get').attributes('href')).toBe(INFO.apk_url)
    expect(w.find('.zc-qr-apk .zc-qr-svg').exists()).toBe(true)
  })

  it('尚未构建：badge 尚未构建，不出现下载链接与下载码', async () => {
    const w = await mountModal({ apk_size: null, apk: { ...APK, status: 'idle', shell_version: null, built_at: null } })
    expect(w.find('.zc-apk-badge').text()).toBe('尚未构建')
    expect(w.find('.zc-apk-get').exists()).toBe(false)
    expect(w.find('.zc-qr-apk .zc-qr-blocked').text()).toContain('APK 就绪后可扫码下载')
  })

  it('构建中：下载隐藏、下载码说明构建中，重建按钮禁用', async () => {
    const w = await mountModal({ apk_size: null, apk: { ...APK, status: 'building', can_rebuild: false } })
    expect(w.find('.zc-apk-badge').text()).toBe('构建中')
    expect(w.find('.zc-apk-get').exists()).toBe(false)
    expect(w.find('.zc-qr-apk .zc-qr-blocked').text()).toContain('构建中，完成后可扫码')
    expect(w.text()).toContain('每 3 秒自动刷新')
    expect(w.find('.zc-apk-rebuild').attributes('disabled')).toBeDefined()
  })

  it('构建失败：显示失败摘要，重建按钮可用', async () => {
    const w = await mountModal({
      apk_size: null,
      apk: { ...APK, status: 'failed', error: 'd8: 内存不足', can_rebuild: true },
    })
    expect(w.find('.zc-apk-badge').text()).toBe('构建失败')
    expect(w.find('.zc-m-error').text()).toContain('d8: 内存不足')
    expect(w.find('.zc-apk-rebuild').attributes('disabled')).toBeUndefined()
  })

  it('点重新构建：调 rebuildMobileApk，成功后静默重拉信息', async () => {
    mocks.api.rebuildMobileApk.mockResolvedValue({ data: { status: 'building' } })
    const w = await mountModal()
    const base = mocks.api.appMobile.mock.calls.length

    await w.find('.zc-apk-rebuild').trigger('click')
    await flushPromises()

    expect(mocks.api.rebuildMobileApk).toHaveBeenCalledWith(12)
    expect(mocks.antd.message.success).toHaveBeenCalledWith('已开始重新构建')
    expect(mocks.api.appMobile.mock.calls.length).toBe(base + 1)
  })

  it('重新构建被后端拒绝（409）时透出 detail 原文', async () => {
    mocks.api.rebuildMobileApk.mockRejectedValue({
      response: { data: { detail: '正式环境未运行：请先启动正式环境再构建 APK' } },
    })
    const w = await mountModal()
    await w.find('.zc-apk-rebuild').trigger('click')
    await flushPromises()
    expect(w.find('.zc-m-error').text()).toContain('正式环境未运行')
    expect(mocks.antd.message.error).toHaveBeenCalledWith('正式环境未运行：请先启动正式环境再构建 APK')
  })

  it('正式环境未运行：重建按钮不出现，写明 APK 靠发布后自动构建', async () => {
    const w = await mountModal({ status_prod: 'stopped' })
    expect(w.find('.zc-apk-rebuild').exists()).toBe(false)
    expect(w.text()).toContain('APK 在发布成功后自动构建')
  })

  it('params_stale：提示壳参数已变化并给出两条生效路径', async () => {
    const w = await mountModal({ apk: { ...APK, params_stale: true } })
    expect(w.text()).toContain('壳参数已变化')
    expect(w.text()).toContain('下次发布成功后会自动重建')
  })

  it('旧的 APK 手动上传入口已移除', async () => {
    const w = await mountModal()
    expect(w.find('.zc-apk-input').exists()).toBe(false)
    expect(w.find('.zc-apk-pick').exists()).toBe(false)
    expect(mocks.api.uploadMobileApk).toBeUndefined()
  })

  it('构建中每 3 秒静默轮询，离开 building 即停表', async () => {
    vi.useFakeTimers()
    mocks.api.appMobile.mockResolvedValue({
      data: { ...INFO, apk_size: null, apk: { ...APK, status: 'building', can_rebuild: false } },
    })
    const w = mount(MobileModal, mountOptions)
    mounted.push(w)
    await flushPromises()
    expect(w.find('.zc-apk-badge').text()).toBe('构建中')
    const base = mocks.api.appMobile.mock.calls.length

    mocks.api.appMobile.mockResolvedValue({ data: { ...INFO, apk: APK } })
    await vi.advanceTimersByTimeAsync(3000)
    await flushPromises()

    expect(mocks.api.appMobile.mock.calls.length).toBe(base + 1)
    expect(w.find('.zc-apk-badge').text()).toBe('已就绪')

    // 已离开 building：再走两个周期不再发请求
    await vi.advanceTimersByTimeAsync(6000)
    expect(mocks.api.appMobile.mock.calls.length).toBe(base + 1)
  })

  it('轮询失败不弹 toast，也不改当前状态', async () => {
    vi.useFakeTimers()
    mocks.api.appMobile.mockResolvedValue({
      data: { ...INFO, apk_size: null, apk: { ...APK, status: 'building', can_rebuild: false } },
    })
    const w = mount(MobileModal, mountOptions)
    mounted.push(w)
    await flushPromises()
    mocks.api.appMobile.mockRejectedValue({ response: { data: { detail: '网络断了' } } })

    await vi.advanceTimersByTimeAsync(3000)
    await flushPromises()

    expect(mocks.antd.message.error).not.toHaveBeenCalled()
    expect(w.find('.zc-apk-badge').text()).toBe('构建中')
  })
})

describe('MobileModal 图标覆盖', () => {
  it('默认展示自动生成图标与预览', async () => {
    const w = await mountModal()
    expect(w.text()).toContain('自动生成')
    expect(w.find('.zc-m-icon').attributes('src')).toBe('/api/apps/12/mobile/icon?v=1')
    expect(w.find('.zc-icon-reset').exists()).toBe(false)
  })

  it('自定义图标：显示恢复按钮，点击调 resetMobileIcon 并重拉', async () => {
    mocks.api.resetMobileIcon.mockResolvedValue({ data: {} })
    const w = await mountModal({
      icon: { custom: true, url: '/api/apps/12/mobile/icon?v=9', updated_at: '2026-11-05T09:00:00Z' },
    })
    expect(w.text()).toContain('自定义')
    const base = mocks.api.appMobile.mock.calls.length

    await w.find('.zc-icon-reset').trigger('click')
    await flushPromises()

    expect(mocks.api.resetMobileIcon).toHaveBeenCalledWith(12)
    expect(mocks.antd.message.success).toHaveBeenCalledWith('已恢复自动图标，正在重新构建')
    expect(mocks.api.appMobile.mock.calls.length).toBe(base + 1)
  })

  it('上传 PNG：调 uploadMobileIcon，成功提示正在重新构建', async () => {
    mocks.api.uploadMobileIcon.mockResolvedValue({ data: {} })
    const w = await mountModal()
    const base = mocks.api.appMobile.mock.calls.length

    await pickIcon(w, { name: 'icon.png', size: 1024, type: 'image/png' })

    expect(mocks.api.uploadMobileIcon).toHaveBeenCalledTimes(1)
    expect(mocks.api.uploadMobileIcon.mock.calls[0][0]).toBe(12)
    expect(mocks.antd.message.success).toHaveBeenCalledWith('图标已更新，正在重新构建')
    expect(mocks.api.appMobile.mock.calls.length).toBe(base + 1)
  })

  // 正式环境未运行：图标照样可存，后端返回 build=deferred，文案必须说清「下次发布生效」
  it('正式环境未运行时上传：提示图标已保存、下次发布成功后生效', async () => {
    mocks.api.uploadMobileIcon.mockResolvedValue({ data: { build: 'deferred' } })
    const w = await mountModal({ status_prod: 'stopped' })

    await pickIcon(w, { name: 'icon.jpeg', size: 1024, type: 'image/jpeg' })

    expect(mocks.antd.message.success).toHaveBeenCalledWith('图标已保存，下次发布成功后自动生效')
  })

  it('非 PNG/JPEG 在本地就被拒，不发请求', async () => {
    const w = await mountModal()
    await pickIcon(w, { name: 'icon.gif', size: 1024, type: 'image/gif' })
    expect(mocks.api.uploadMobileIcon).not.toHaveBeenCalled()
    expect(w.find('.zc-m-error').text()).toContain('只支持 PNG/JPEG 图标')
  })

  it('超过 2MB 在本地就被拒，不发请求', async () => {
    const w = await mountModal()
    await pickIcon(w, { name: 'icon.png', size: 2 * 1024 * 1024 + 1, type: 'image/png' })
    expect(mocks.api.uploadMobileIcon).not.toHaveBeenCalled()
    expect(w.find('.zc-m-error').text()).toContain('图标不能超过 2MB')
  })

  it('后端拒绝图标时透出 detail 原文', async () => {
    mocks.api.uploadMobileIcon.mockRejectedValue({
      response: { data: { detail: '图标不是有效的 PNG/JPEG' } },
    })
    const w = await mountModal()
    await pickIcon(w, { name: 'icon.png', size: 1024, type: 'image/png' })
    expect(w.find('.zc-m-error').text()).toContain('图标不是有效的 PNG/JPEG')
    expect(mocks.antd.message.error).toHaveBeenCalledWith('图标不是有效的 PNG/JPEG')
  })
})
