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
    bindMiniprogram: vi.fn(),
    unbindMiniprogram: vi.fn(),
    reuploadMiniprogram: vi.fn(),
    requestMiniprogramPreview: vi.fn(),
    miniprogramPreviewUrl: (id, stamp) =>
      `/api/apps/${id}/mobile/miniprogram/preview${stamp ? `?v=${encodeURIComponent(stamp)}` : ''}`,
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
  miniprogram: {
    bound: false, appid_masked: '', key_uploaded_at: null, status: 'idle',
    job_kind: null, error: null, version: null, uploaded_at: null, preview_at: null,
    preview_available: false,
  },
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

describe('MobileModal 小程序凭据（ADR-0045）', () => {
  const BOUND = {
    bound: true, appid_masked: 'wx12****cdef', key_uploaded_at: '2026-11-09T10:00:00Z',
    status: 'idle', job_kind: null, error: null, version: null, uploaded_at: null, preview_at: null,
  }

  async function fillBind(w, { appid = 'wx1234567890abcdef', file = { name: 'private.key' } } = {}) {
    await w.find('.zc-mp-appid-input').setValue(appid)
    const input = w.find('.zc-mp-key-input')
    Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
    await input.trigger('change')
    await w.find('.zc-mp-bind').trigger('click')
    await flushPromises()
  }

  it('未绑定时填 AppID 并选密钥文件即可绑定，成功后重新拉取信息', async () => {
    mocks.api.bindMiniprogram.mockResolvedValue({ data: { ok: true } })
    const w = await mountModal()
    const base = mocks.api.appMobile.mock.calls.length

    await fillBind(w)

    expect(mocks.api.bindMiniprogram).toHaveBeenCalledTimes(1)
    const [id, appid, file] = mocks.api.bindMiniprogram.mock.calls[0]
    expect(id).toBe(12)
    expect(appid).toBe('wx1234567890abcdef')
    expect(file.name).toBe('private.key')
    expect(mocks.antd.message.success).toHaveBeenCalledWith('已绑定小程序账号')
    expect(mocks.api.appMobile.mock.calls.length).toBe(base + 1)
  })

  it('缺少 AppID 或密钥文件时本地拒绝，不发请求', async () => {
    const w = await mountModal()
    await w.find('.zc-mp-appid-input').setValue('')
    const input = w.find('.zc-mp-key-input')
    Object.defineProperty(input.element, 'files', { value: [{ name: 'private.key' }], configurable: true })
    await input.trigger('change')
    await w.find('.zc-mp-bind').trigger('click')
    await flushPromises()
    expect(mocks.api.bindMiniprogram).not.toHaveBeenCalled()
    expect(w.find('.zc-mp-error').text()).toContain('请填写小程序 AppID')
  })

  it('已绑定只显示脱敏 AppID 与上传时间，解绑后重新拉取', async () => {
    mocks.api.unbindMiniprogram.mockResolvedValue({ data: { ok: true } })
    const w = await mountModal({ miniprogram: BOUND })

    expect(w.find('.zc-mp-appid').text()).toBe('wx12****cdef')
    expect(w.find('.zc-mp-key-input').exists()).toBe(false)
    expect(w.text()).toContain('密钥上传于')

    await w.find('.zc-mp-unbind').trigger('click')
    await flushPromises()
    expect(mocks.api.unbindMiniprogram).toHaveBeenCalledWith(12)
    expect(mocks.antd.message.success).toHaveBeenCalledWith('已解绑小程序账号')
  })

  it('绑定被后端拒绝时透出 detail 原文', async () => {
    mocks.api.bindMiniprogram.mockRejectedValue({
      response: { data: { detail: '该 AppID 已绑定其他应用，请先解绑' } },
    })
    const w = await mountModal()
    await fillBind(w)
    expect(w.find('.zc-mp-error').text()).toContain('该 AppID 已绑定其他应用')
    expect(mocks.antd.message.error).toHaveBeenCalledWith('该 AppID 已绑定其他应用，请先解绑')
  })
})

describe('MobileModal 小程序任务状态（规格 2026-11-09）', () => {
  const BOUND = {
    bound: true, appid_masked: 'wx12****cdef', key_uploaded_at: '2026-11-09T10:00:00Z',
    status: 'idle', job_kind: null, error: null, version: null, uploaded_at: null, preview_at: null,
  }

  it('预检进行中显示构建提示并轮询，结束后停表', async () => {
    vi.useFakeTimers()
    const building = { ...BOUND, status: 'building', job_kind: 'precheck' }
    mocks.api.appMobile.mockResolvedValue({ data: { ...INFO, miniprogram: building } })
    const w = mount(MobileModal, mountOptions)
    mounted.push(w)
    await flushPromises()

    expect(w.find('.zc-mp-badge').text()).toBe('构建中')
    expect(w.text()).toContain('编译并预检（不产生开发版）')
    const base = mocks.api.appMobile.mock.calls.length

    mocks.api.appMobile.mockResolvedValue({ data: { ...INFO, miniprogram: { ...BOUND, status: 'ready' } } })
    await vi.advanceTimersByTimeAsync(3000)
    await flushPromises()

    expect(mocks.api.appMobile.mock.calls.length).toBe(base + 1)
    expect(w.find('.zc-mp-badge').text()).toBe('已就绪')

    await vi.advanceTimersByTimeAsync(6000)
    expect(mocks.api.appMobile.mock.calls.length).toBe(base + 1)
  })

  it('失败时展示分类后的错误摘要', async () => {
    const failed = { ...BOUND, status: 'failed', error: 'IP 白名单：invalid ip 1.2.3.4' }
    const w = await mountModal({ miniprogram: failed })
    expect(w.find('.zc-mp-badge').text()).toBe('失败')
    expect(w.find('.zc-mp-error').text()).toContain('IP 白名单')
  })

  it('上传成功后展示开发版版本与上传时间', async () => {
    const ready = {
      ...BOUND, status: 'ready', job_kind: 'upload',
      version: 'b20261109100000', uploaded_at: '2026-11-09T10:00:05Z',
    }
    const w = await mountModal({ miniprogram: ready })
    expect(w.find('.zc-mp-badge').text()).toBe('已就绪')
    expect(w.text()).toContain('版本 b20261109100000')
    expect(w.text()).toContain('上传于')
  })

  it('点「重新上传」触发一次上传并入队后重新拉取', async () => {
    mocks.api.reuploadMiniprogram.mockResolvedValue({ data: { ok: true, status: 'building' } })
    const w = await mountModal({ miniprogram: BOUND })
    const base = mocks.api.appMobile.mock.calls.length

    await w.find('.zc-mp-upload').trigger('click')
    await flushPromises()

    expect(mocks.api.reuploadMiniprogram).toHaveBeenCalledWith(12)
    expect(mocks.antd.message.success).toHaveBeenCalledWith('已开始上传开发版')
    expect(mocks.api.appMobile.mock.calls.length).toBe(base + 1)
  })

  it('上传/构建进行中禁用「重新上传」；后端 409 原文可见', async () => {
    const busy = { ...BOUND, status: 'uploading', job_kind: 'upload' }
    const w = await mountModal({ miniprogram: busy })
    expect(w.find('.zc-mp-upload').attributes('disabled')).toBeDefined()

    mocks.api.reuploadMiniprogram.mockRejectedValue({
      response: { data: { detail: '正式环境未运行：请先发布并启动正式环境，再上传小程序' } },
    })
    const idle = await mountModal({ miniprogram: BOUND })
    await idle.find('.zc-mp-upload').trigger('click')
    await flushPromises()
    expect(idle.find('.zc-mp-error').text()).toContain('正式环境未运行')
  })
})

describe('MobileModal 小程序前置条件与指引（规格 2026-11-09 US-22/US-25）', () => {
  const BOUND = {
    bound: true, appid_masked: 'wx12****cdef', key_uploaded_at: '2026-11-09T10:00:00Z',
    status: 'idle', job_kind: null, error: null, version: null, uploaded_at: null,
    preview_at: null, preview_available: false,
  }
  const READY = {
    ok: true, reasons: [],
    request_domain: 'https://bbb.example.test',
    egress_ip: '203.0.113.9',
  }
  const GUIDANCE = {
    request_domain: 'https://bbb.example.test',
    egress_ip: '203.0.113.9',
    console_url: 'https://mp.weixin.qq.com/',
    steps: ['dev_version', 'trial_version', 'submit_review'],
  }

  it('前置条件不满足时禁用上传与预览并给出原因，绑定仍可保存', async () => {
    const blocked = {
      ...BOUND,
      readiness: { ok: false, reasons: ['ssl_off'], request_domain: '', egress_ip: '' },
    }
    const w = await mountModal({ miniprogram: blocked })
    expect(w.find('.zc-mp-blocker').text()).toContain('HTTPS')
    expect(w.find('.zc-mp-upload').attributes('disabled')).toBeDefined()
    expect(w.find('.zc-mp-preview-btn').attributes('disabled')).toBeDefined()

    const unbound = await mountModal({
      miniprogram: { ...INFO.miniprogram, readiness: blocked.readiness },
    })
    expect(unbound.find('.zc-mp-bind').attributes('disabled')).toBeUndefined()
  })

  it('条件满足时展示加白清单与三步指引，域名可一键复制', async () => {
    const w = await mountModal({
      miniprogram: { ...BOUND, readiness: READY, guidance: GUIDANCE },
    })
    expect(w.text()).toContain('https://bbb.example.test')
    expect(w.text()).toContain('203.0.113.9')
    expect(w.text()).toContain('不支持通配符')
    expect(w.text()).toContain('平台不会提审')
    expect(w.find('.zc-mp-console').attributes('href')).toBe('https://mp.weixin.qq.com/')

    await w.find('.zc-mp-copy-domain').trigger('click')
    await flushPromises()
    expect(writeText).toHaveBeenCalledWith('https://bbb.example.test')
    expect(mocks.antd.message.success).toHaveBeenCalledWith('域名已复制')
  })

  it('出网 IP 获取失败只提示手动确认，不阻断上传', async () => {
    const noIp = {
      ...BOUND,
      readiness: { ...READY, egress_ip: '' },
      guidance: { ...GUIDANCE, egress_ip: '' },
    }
    const w = await mountModal({ miniprogram: noIp })
    expect(w.text()).toContain('获取失败，请手动确认')
    expect(w.find('.zc-mp-copy-ip').attributes('disabled')).toBeDefined()
    expect(w.find('.zc-mp-upload').attributes('disabled')).toBeUndefined()
  })

  it('非移动栈应用没有小程序区块', async () => {
    vi.stubGlobal('location', { hostname: 'localhost' })
    mocks.api.appMobile.mockRejectedValue({
      response: { status: 400, data: { detail: '仅移动端应用支持移动端分发' } },
    })
    const w = mount(MobileModal, mountOptions)
    mounted.push(w)
    await flushPromises()
    expect(w.find('.zc-mp').exists()).toBe(false)
    expect(w.text()).toContain('仅移动端应用支持移动端分发')
  })
})

describe('MobileModal 小程序预览码（规格 2026-11-09 US-16）', () => {
  const BOUND = {
    bound: true, appid_masked: 'wx12****cdef', key_uploaded_at: '2026-11-09T10:00:00Z',
    status: 'idle', job_kind: null, error: null, version: null, uploaded_at: null, preview_at: null,
    preview_available: false,
  }

  it('未生成过时只有按钮、无破图；点击后入队并重新拉取', async () => {
    mocks.api.requestMiniprogramPreview.mockResolvedValue({ data: { ok: true, status: 'building' } })
    const w = await mountModal({ miniprogram: BOUND })
    const base = mocks.api.appMobile.mock.calls.length

    expect(w.find('.zc-mp-qr-img').exists()).toBe(false)
    expect(w.find('.zc-mp-preview-btn').text()).toContain('生成预览码')

    await w.find('.zc-mp-preview-btn').trigger('click')
    await flushPromises()

    expect(mocks.api.requestMiniprogramPreview).toHaveBeenCalledWith(12)
    expect(mocks.antd.message.success).toHaveBeenCalledWith('已开始生成预览码')
    expect(mocks.api.appMobile.mock.calls.length).toBe(base + 1)
  })

  it('已有预览码时展示鉴权图片与「重新生成」，并提示短时有效', async () => {
    const ready = {
      ...BOUND, status: 'ready', job_kind: 'preview',
      preview_at: '2026-11-09T10:02:00Z', preview_available: true,
    }
    const w = await mountModal({ miniprogram: ready })
    const img = w.find('.zc-mp-qr-img')
    expect(img.exists()).toBe(true)
    expect(img.attributes('src')).toContain('/api/apps/12/mobile/miniprogram/preview?v=')
    expect(w.find('.zc-mp-preview-btn').text()).toContain('重新生成')
    expect(w.text()).toContain('短时有效')
  })

  it('任务进行中禁用生成按钮', async () => {
    const busy = { ...BOUND, status: 'building', job_kind: 'preview' }
    const w = await mountModal({ miniprogram: busy })
    expect(w.find('.zc-mp-preview-btn').attributes('disabled')).toBeDefined()
  })

  it('重新生成失败时透出后端 detail', async () => {
    mocks.api.requestMiniprogramPreview.mockRejectedValue({
      response: { data: { detail: '尚未绑定小程序账号：请先绑定 AppID 与上传密钥' } },
    })
    const w = await mountModal({ miniprogram: BOUND })
    await w.find('.zc-mp-preview-btn').trigger('click')
    await flushPromises()
    expect(w.find('.zc-mp-error').text()).toContain('尚未绑定小程序账号')
  })
})
