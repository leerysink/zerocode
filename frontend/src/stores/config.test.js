// config store 与「打开文件夹方式」系统参数的映射：
// FilePanel 在点击时读 store，故默认值必须是 auto，且 ensureLoaded 只打一次 GET /api/config。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('../api', () => ({ getConfig: vi.fn(), putConfig: vi.fn() }))

import { getConfig, putConfig } from '../api'
import { useConfigStore } from './config'

beforeEach(() => {
  vi.clearAllMocks()
  setActivePinia(createPinia())
})

describe('config store 打开文件夹方式', () => {
  it('默认 web：没配过这个键时一律弹内置文件管理器，本机直开要显式选（ADR-0009）', () => {
    expect(useConfigStore().form.openFolderMode).toBe('web')
  })

  it('loadConfig 读回 fs.open_folder_mode，save 写回同一个键', async () => {
    getConfig.mockResolvedValue({ data: { 'fs.open_folder_mode': 'web', 'llm.model': 'm' } })
    const store = useConfigStore()
    await store.loadConfig()
    expect(store.form.openFolderMode).toBe('web')

    store.form.openFolderMode = 'system'
    await store.save()
    expect(putConfig).toHaveBeenCalledWith(expect.objectContaining({ 'fs.open_folder_mode': 'system' }))
  })

  it('ensureLoaded 只拉一次；保存过就用内存里的新值（改值即时生效，无需刷新）', async () => {
    getConfig.mockResolvedValue({ data: { 'fs.open_folder_mode': 'auto' } })
    const store = useConfigStore()
    await store.ensureLoaded()
    await store.ensureLoaded()
    expect(getConfig).toHaveBeenCalledTimes(1)

    putConfig.mockResolvedValue({ data: { ok: true } })
    store.form.openFolderMode = 'web'
    await store.save()
    await store.ensureLoaded()
    expect(getConfig).toHaveBeenCalledTimes(1)
    expect(store.form.openFolderMode).toBe('web')
  })
})

describe('config store 视觉能力', () => {
  it('loadConfig / save 读写 llm.supports_vision', async () => {
    getConfig.mockResolvedValue({ data: { 'llm.supports_vision': true } })
    const store = useConfigStore()
    await store.loadConfig()
    expect(store.form.supportsVision).toBe(true)

    await store.save()
    expect(putConfig).toHaveBeenCalledWith(
      expect.objectContaining({ 'llm.supports_vision': true }))
  })
})
