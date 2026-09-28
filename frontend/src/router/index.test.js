// @vitest-environment happy-dom
import { describe, expect, it, vi } from 'vitest'
vi.mock('../stores/auth', () => ({ useAuthStore: () => ({ loaded: true, user: { id: 1 }, fetchMe: vi.fn() }) }))
import router from './index'

describe('router', () => {
  it('注册了应用 DB 操作台路由', () => {
    const hit = router.options.routes.find((r) => r.path === '/apps/:id/db')
    expect(hit).toBeTruthy()
    expect(hit.name).toBe('app-db')
  })
})
