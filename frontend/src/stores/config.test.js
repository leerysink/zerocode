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

describe('config store 预览重启次数与提问提醒', () => {
  it('默认值与后端代码默认同口径：单次任务 3 次重启、第 6 题提醒', () => {
    const { form } = useConfigStore()
    expect(form.previewMaxRestarts).toBe(3)
    expect(form.askRemindAt).toBe(6)
  })

  it('loadConfig / save 读写两个键，0 表示不限制/不提醒', async () => {
    getConfig.mockResolvedValue({ data: {
      'agent.preview_max_restarts': 5, 'clarify.ask_remind_at': 4 } })
    const store = useConfigStore()
    await store.loadConfig()
    expect(store.form.previewMaxRestarts).toBe(5)
    expect(store.form.askRemindAt).toBe(4)

    store.form.previewMaxRestarts = 0
    store.form.askRemindAt = 0
    await store.save()
    expect(putConfig).toHaveBeenCalledWith(expect.objectContaining({
      'agent.preview_max_restarts': 0, 'clarify.ask_remind_at': 0 }))
  })
})

describe('config store 运行参数与记忆召回', () => {
  it('默认值逐项对齐后端代码默认：抄错等于用一次保存改掉默认值', () => {
    expect(useConfigStore().form).toMatchObject({
      autoCompactChars: 50000, compactMinNew: 6, compactKeepRecent: 8, steeringQueueMax: 20,
      previewWaitSeconds: 240, previewLogLines: 80, previewConfirmRunning: true,
      browserMaxPerRun: 6, browserMaxStepsPerRun: 12, browserTimeoutSeconds: 45,
      browserExpectTimeoutMs: 3000, browserSettleMs: 1200, browserInteractiveMax: 40,
      dbRowsLimit: 200, dbRowsAssertMax: 200,
      memoryEnabled: true, memoryTopK: 5, memoryMinSimilarity: -1, memoryMaxTokens: 600,
    })
  })

  it('新增映射键都必须在表单里有默认值，否则 save 会把 undefined 写进库', async () => {
    putConfig.mockResolvedValue({ data: { ok: true } })
    await useConfigStore().save()
    const payload = putConfig.mock.calls[0][0]
    expect(Object.keys(payload).length).toBeGreaterThan(20)
    expect(Object.entries(payload).filter(([, v]) => v === undefined || v === null)).toEqual([])
  })

  it('loadConfig / save 原样读写 0 与小数值（0=关闭自动压缩、-1=不滤相似度）', async () => {
    getConfig.mockResolvedValue({ data: {
      'agent.auto_compact_chars': 0, 'agent.compact_keep_recent': 12,
      'memory.top_k': 20, 'memory.min_similarity': 0.4 } })
    const store = useConfigStore()
    await store.loadConfig()
    expect(store.form.autoCompactChars).toBe(0)
    expect(store.form.compactKeepRecent).toBe(12)
    expect(store.form.memoryTopK).toBe(20)
    expect(store.form.memoryMinSimilarity).toBe(0.4)

    await store.save()
    expect(putConfig).toHaveBeenCalledWith(expect.objectContaining({
      'agent.auto_compact_chars': 0, 'agent.compact_keep_recent': 12,
      'memory.top_k': 20, 'memory.min_similarity': 0.4 }))
  })
})
