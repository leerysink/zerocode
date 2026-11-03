// @vitest-environment happy-dom
import { describe, expect, it, vi } from 'vitest'
vi.mock('../stores/auth', () => ({ useAuthStore: () => ({ loaded: true, user: { id: 1 }, fetchMe: vi.fn() }) }))
import { fileChangePath, workspaceFilesPath } from '../utils/openFolder'
import router from './index'

describe('router', () => {
  it('注册了应用 DB 操作台路由', () => {
    const hit = router.options.routes.find((r) => r.path === '/apps/:id/db')
    expect(hit).toBeTruthy()
    expect(hit.name).toBe('app-db')
  })

  it('注册了工作区文件管理器整页路由', () => {
    const hit = router.options.routes.find((r) => r.name === 'workspace-files')
    expect(hit).toBeTruthy()
    expect(hit.path).toBe('/workspaces/:id/files')
  })

  it('入口拼接的地址与路由表对得上（两处各写一份时改一边不会静默断链）', () => {
    const hit = router.options.routes.find((r) => r.name === 'workspace-files')
    expect(hit.path.replace(':id', '123')).toBe(workspaceFilesPath(123))
  })

  it('注册了变更记录详情整页路由', () => {
    const hit = router.options.routes.find((r) => r.name === 'session-file-change')
    expect(hit).toBeTruthy()
    expect(hit.path).toBe('/sessions/:sessionId/changes/:changeId')
  })

  it('变更详情的入口地址与路由表对得上', () => {
    const hit = router.options.routes.find((r) => r.name === 'session-file-change')
    expect(hit.path.replace(':sessionId', '12').replace(':changeId', '345')).toBe(fileChangePath(12, 345))
  })
})
