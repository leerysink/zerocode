// @vitest-environment happy-dom
// 移动端弹框（规格 2026-11-03 US-15~US-19）：卡面 ☰ 与聊天页「更多」共用的居中弹框。
// 钉住：后端字段驱动的地址与二维码、localhost 灰码（US-08）、正式未运行阻断（US-17）、
// APK 上传/下载行与两道体积闸门。
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { h } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'

const mocks = vi.hoisted(() => ({
  api: { appMobile: vi.fn(), uploadMobileApk: vi.fn() },
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

const INFO = {
  stack: 'vue3-springboot-mobile',
  status_dev: 'running',
  status_prod: 'running',
  dev_url: 'http://localhost:8211/h5/',
  h5_url: 'https://bbb.example.test/h5/',
  apk_url: 'https://bbb.example.test/api/uploads/mobile/bbb.apk',
  apk_size: null,
  apk_max_bytes: 200 * 1024 * 1024,
}

async function mountModal(over = {}, { host = 'localhost' } = {}) {
  vi.stubGlobal('location', { hostname: host })
  mocks.api.appMobile.mockResolvedValue({ data: { ...INFO, ...over } })
  const wrapper = mount(MobileModal, {
    props: { app: { id: 12, name: '乙' }, open: true },
    global: { stubs: { 'a-modal': ModalStub, 'a-button': ButtonStub, 'a-spin': SpinStub } },
  })
  await flushPromises()
  return wrapper
}

async function pickFile(wrapper, file) {
  const input = wrapper.find('.zc-apk-input')
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
  vi.unstubAllGlobals()
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

describe('MobileModal APK 行', () => {
  it('未上传：显示尚未上传，不出下载链接与下载码', async () => {
    const w = await mountModal()
    expect(w.text()).toContain('尚未上传')
    expect(w.find('.zc-apk-get').exists()).toBe(false)
    expect(w.find('.zc-qr-apk .zc-qr-blocked').text()).toContain('上传后可扫码下载')
  })

  it('已上传：显示体积、下载链接与下载码', async () => {
    const w = await mountModal({ apk_size: 2 * 1024 * 1024 })
    expect(w.text()).toContain('已上传 2.0 MB')
    expect(w.find('.zc-apk-get').attributes('href')).toBe(INFO.apk_url)
    expect(w.find('.zc-qr-apk .zc-qr-svg').exists()).toBe(true)
  })

  // US-17：还没发布过时给明确阻断理由，不隐式替用户起栈或发布
  it('正式环境未运行：上传入口整个不出现，并写明阻断理由', async () => {
    const w = await mountModal({ status_prod: 'stopped' })
    expect(w.find('.zc-apk-pick').exists()).toBe(false)
    expect(w.text()).toContain('再上传 APK')
  })

  it('选 .apk 调 uploadMobileApk，成功后重拉信息', async () => {
    mocks.api.uploadMobileApk.mockResolvedValue({ data: { ok: true } })
    const w = await mountModal()
    const base = mocks.api.appMobile.mock.calls.length

    await pickFile(w, { name: 'app.apk', size: 1024 })

    expect(mocks.api.uploadMobileApk).toHaveBeenCalledTimes(1)
    expect(mocks.api.uploadMobileApk.mock.calls[0][0]).toBe(12)
    expect(mocks.api.appMobile.mock.calls.length).toBe(base + 1)
    expect(mocks.antd.message.success).toHaveBeenCalledWith('APK 已上传')
  })

  it('非 .apk 在本地就被拒，不发请求', async () => {
    const w = await mountModal()
    await pickFile(w, { name: 'app.zip', size: 1024 })
    expect(mocks.api.uploadMobileApk).not.toHaveBeenCalled()
    expect(w.find('.zc-m-error').text()).toContain('只支持 .apk')
  })

  it('超过 apk_max_bytes 在本地就被拒，不发请求', async () => {
    const w = await mountModal({ apk_max_bytes: 2 * 1024 * 1024 })
    await pickFile(w, { name: 'app.apk', size: 3 * 1024 * 1024 })
    expect(mocks.api.uploadMobileApk).not.toHaveBeenCalled()
    expect(w.find('.zc-m-error').text()).toContain('APK 不能超过 2MB')
  })

  it('后端拒绝时透出 detail 原文', async () => {
    mocks.api.uploadMobileApk.mockRejectedValue({
      response: { data: { detail: '正式环境未运行：请先发布并启动正式环境，再上传 APK' } },
    })
    const w = await mountModal()
    await pickFile(w, { name: 'app.apk', size: 1024 })
    expect(w.find('.zc-m-error').text()).toContain('正式环境未运行')
    expect(mocks.antd.message.error).toHaveBeenCalledWith('正式环境未运行：请先发布并启动正式环境，再上传 APK')
  })
})
