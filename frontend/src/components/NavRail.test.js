// @vitest-environment happy-dom
// 窄栏导航：四个入口的指向/文案、当前页高亮、对话页不高亮。
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'
import NavRail, { NAV_RAIL_WIDTH } from './NavRail.vue'

const routes = [{ path: '/:pathMatch(.*)*', component: { render: () => null } }]

async function mountAt(path) {
  const router = createRouter({ history: createMemoryHistory(), routes })
  await router.push(path)
  await router.isReady()
  return mount(NavRail, { global: { plugins: [router] } })
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
})