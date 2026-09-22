// @vitest-environment happy-dom
// Task 12：商店页三视图（列表/我的申请/待审批）与轻身份弹窗的接线契约。
// 真 Antd 插件挂载（与 AppsView 的桩件模式不同：商店页无 WS/进度流，交互全在 tabs/modal 上）。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import Antd from 'ant-design-vue'
import StoreView from './StoreView.vue'
import * as api from '../api'

// happy-dom 没有 ResizeObserver，antd Tabs 的 ink bar 测量会直接抛
global.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} }

vi.mock('../api')

const releases = [
  { id: 1, slug: 'test1', version: 'b2', title: '陌如玉', summary: '桌面备忘', stack: 'vue3-fastapi',
    publisher: '作者', installed: false, installed_apps: [], updated_count: 0 },
]
const applications = [
  { id: 9, release_id: 1, want_name: '我的备忘', status: 'pending', title: '陌如玉', version: 'b2' },
]

function mountView () {
  return mount(StoreView, { global: { plugins: [Antd] }, attachTo: document.body })
}

beforeEach(() => {
  vi.clearAllMocks()
  api.storeMe.mockResolvedValue({ data: { id: 1, nickname: '我' } })
  api.storeReleases.mockResolvedValue({ data: releases })
  api.storeApplications.mockResolvedValue({ data: applications })
  api.storeMyReleases.mockResolvedValue({ data: [] })
})

describe('StoreView', () => {
  it('渲染商店卡片：标题/发布者/版本与申请按钮', async () => {
    const w = mountView()
    await flushPromises()
    expect(w.text()).toContain('陌如玉')
    expect(w.text()).toContain('作者')
    expect(w.findAll('button').some((b) => b.text().includes('申请'))).toBe(true)
  })

  it('已安装的卡片不再给申请按钮，而是给出实例入口', async () => {
    api.storeReleases.mockResolvedValue({ data: [{ ...releases[0], installed: true,
      installed_apps: [{ app_id: 7, name: '我的备忘', status_prod: 'running' }] }] })
    const w = mountView()
    await flushPromises()
    expect(w.text()).toContain('我的备忘')
    expect(w.findAll('button').some((b) => b.text().includes('申请'))).toBe(false)
  })

  it('未登录（401 identity_required）弹昵称框，提交后重新拉列表', async () => {
    api.storeMe.mockRejectedValue({ response: { status: 401 } })
    api.storeIdentify.mockResolvedValue({ data: { id: 2, nickname: '新人' } })
    const w = mountView()
    await flushPromises()
    expect(w.find('[data-test="actor-modal"]').exists()).toBe(true)
    await w.find('[data-test="actor-name"]').setValue('新人')
    await w.find('[data-test="actor-submit"]').trigger('click')
    await flushPromises()
    expect(api.storeIdentify).toHaveBeenCalledWith('新人')
    expect(api.storeReleases).toHaveBeenCalled()
  })

  it('审批：驳回必须填原因才可调接口', async () => {
    const w = mountView()
    await flushPromises()
    await w.find('[data-test="tab-review"]').trigger('click')
    await flushPromises()
    await w.find('[data-test="reject-btn"]').trigger('click')
    await flushPromises()
    expect(api.storeReject).not.toHaveBeenCalled()
    await w.find('[data-test="reject-reason"]').setValue('名字不合适')
    await w.find('[data-test="reject-confirm"]').trigger('click')
    await flushPromises()
    expect(api.storeReject).toHaveBeenCalledWith(9, '名字不合适')
  })
})
