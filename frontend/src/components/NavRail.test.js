// @vitest-environment happy-dom
// 窄栏导航：四个入口的指向/文案、当前页高亮、对话页不高亮；创始人多一个管理入口。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'
import NavRail, { NAV_RAIL_WIDTH } from './NavRail.vue'
import { useAuthStore } from '../stores/auth'
import * as api from '../api'

vi.mock('../api')

beforeEach(() => {
  vi.clearAllMocks()
  api.storeApplications.mockResolvedValue({ data: [] })
})

const routes = [{ path: '/:pathMatch(.*)*', component: { render: () => null } }]

async function mountAt(path, user = null) {
  const pinia = createPinia()
  setActivePinia(pinia)
  useAuthStore().user = user
  const router = createRouter({ history: createMemoryHistory(), routes })
  await router.push(path)
  await router.isReady()
  return mount(NavRail, { global: { plugins: [router, pinia] } })
}

describe('NavRail', () => {
  it('四个入口指向应用/商店/设置/技能，宽度常量与样式约定一致', async () => {
    const wrapper = await mountAt('/')
    const links = wrapper.findAll('a')
    expect(links.map((l) => l.attributes('href'))).toEqual(['/apps', '/store', '/settings', '/skills'])
    expect(links.map((l) => l.text())).toEqual(['应用', '商店', '设置', '技能'])
    expect(NAV_RAIL_WIDTH).toBe(64)
  })

  it('当前页高亮：/store 下只有商店带 router-link-active', async () => {
    const wrapper = await mountAt('/store')
    const active = wrapper.findAll('a.router-link-active')
    expect(active).toHaveLength(1)
    expect(active[0].attributes('href')).toBe('/store')
  })

  it('技能页只高亮技能：设置不连带高亮（技能是 /skills 一级页，不嵌在 /settings 下）', async () => {
    const wrapper = await mountAt('/skills')
    const active = wrapper.findAll('a.router-link-active')
    expect(active).toHaveLength(1)
    expect(active[0].text()).toBe('技能')
  })

  it('对话页不属于四个入口：都不高亮', async () => {
    const wrapper = await mountAt('/')
    expect(wrapper.find('a.router-link-active').exists()).toBe(false)
  })

  it('创始人多一个管理入口：指向 /store-admin，非创始人仍四入口', async () => {
    const founder = await mountAt('/', { id: 1, is_founder: true })
    expect(founder.findAll('a').map((l) => l.attributes('href')))
      .toEqual(['/apps', '/store', '/store-admin', '/settings', '/skills'])
    const plain = await mountAt('/', { id: 2, is_founder: false })
    expect(plain.findAll('a').map((l) => l.attributes('href')))
      .toEqual(['/apps', '/store', '/settings', '/skills'])
  })

  it('管理页高亮管理入口', async () => {
    const wrapper = await mountAt('/store-admin', { id: 1, is_founder: true })
    const active = wrapper.findAll('a.router-link-active')
    expect(active).toHaveLength(1)
    expect(active[0].attributes('href')).toBe('/store-admin')
  })

  it('创始人有活时管理入口点红点，没活时不点', async () => {
    api.storeApplications.mockResolvedValue({ data: [{ id: 1 }, { id: 2 }, { id: 3 }] })
    const founder = await mountAt('/', { id: 1, is_founder: true })
    await flushPromises()
    expect(founder.find('[data-test="rail-admin-badge"]').text()).toBe('3')
    expect(api.storeApplications).toHaveBeenCalledWith('review')
  })

  it('非创始人不拉收件箱也不出红点；超过 9 条收成 9+', async () => {
    const plain = await mountAt('/', { id: 2, is_founder: false })
    await flushPromises()
    expect(plain.find('[data-test="rail-admin-badge"]').exists()).toBe(false)
    expect(api.storeApplications).not.toHaveBeenCalled()

    api.storeApplications.mockResolvedValue({ data: new Array(12).fill({ id: 1 }) })
    const founder = await mountAt('/', { id: 1, is_founder: true })
    await flushPromises()
    expect(founder.find('[data-test="rail-admin-badge"]').text()).toBe('9+')
  })

  it('收件箱拉不到就不点红点（宁可少提示，不提示一个看不见的队列）', async () => {
    api.storeApplications.mockRejectedValue(new Error('network'))
    const founder = await mountAt('/', { id: 1, is_founder: true })
    await flushPromises()
    expect(founder.find('[data-test="rail-admin-badge"]').exists()).toBe(false)
  })
})