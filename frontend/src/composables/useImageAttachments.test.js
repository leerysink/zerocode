// @vitest-environment happy-dom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { watchEffect } from 'vue'

vi.mock('../api', () => ({ uploadAttachment: vi.fn() }))
vi.mock('../utils/imageAttachments', async (importOriginal) => ({
  ...(await importOriginal()),
  compressImage: vi.fn(),
}))

import { uploadAttachment } from '../api'
import { compressImage } from '../utils/imageAttachments'
import { useImageAttachments } from './useImageAttachments'

function file(name = 'a.png', type = 'image/png', size = 10) {
  return new File([new Uint8Array(size)], name, { type })
}

beforeEach(() => {
  vi.clearAllMocks()
  globalThis.URL.createObjectURL = vi.fn(() => 'blob:preview')
  globalThis.URL.revokeObjectURL = vi.fn()
  compressImage.mockResolvedValue(new Blob(['x'], { type: 'image/jpeg' }))
  uploadAttachment.mockResolvedValue({
    data: { id: 1, filename: 'a.png', mime: 'image/png', size_bytes: 10, url: '/api/attachments/1' },
  })
})

it('addFiles 压缩并上传，readyItems 只含服务端字段', async () => {
  const c = useImageAttachments(() => 7)
  await c.addFiles([file()])
  expect(compressImage).toHaveBeenCalledTimes(1)
  expect(uploadAttachment).toHaveBeenCalledWith(7, expect.any(Blob), 'a.png', expect.anything())
  expect(c.items.value[0].status).toBe('ready')
  expect(c.readyItems()).toEqual([
    { id: 1, filename: 'a.png', mime: 'image/png', size_bytes: 10, url: '/api/attachments/1' },
  ])
})

it('超过 4 张不新增，非图片拒绝', async () => {
  const c = useImageAttachments(() => 7)
  await c.addFiles(Array.from({ length: 5 }, (_, i) => file(`a${i}.png`)))
  expect(c.items.value.length).toBe(4)
  expect(c.error.value).toContain('最多')
  // 队列已满时 validateImageFile 先报数量上限，类型拒绝需在空队列上验证
  const d = useImageAttachments(() => 7)
  await d.addFiles([file('x.pdf', 'application/pdf')])
  expect(d.items.value.length).toBe(0)
  expect(d.error.value).toContain('不支持')
})

it('上传失败可单张重试', async () => {
  uploadAttachment.mockRejectedValueOnce({ response: { data: { detail: '上传失败' } } })
  const c = useImageAttachments(() => 7)
  await c.addFiles([file()])
  expect(c.items.value[0].status).toBe('failed')
  expect(c.hasFailed.value).toBe(true)
  await c.retry(c.items.value[0].key)
  expect(c.items.value[0].status).toBe('ready')
})

it('会话切换后晚到的上传结果不写回', async () => {
  let resolve
  uploadAttachment.mockReturnValue(new Promise((r) => { resolve = r }))
  let sid = 7
  const c = useImageAttachments(() => sid)
  const pending = c.addFiles([file()])
  await vi.waitFor(() => expect(uploadAttachment).toHaveBeenCalled())
  sid = 8
  c.clear()
  resolve({ data: { id: 1, filename: 'a.png', mime: 'image/png', size_bytes: 10, url: '/api/attachments/1' } })
  await pending
  expect(c.items.value).toEqual([])
})

it('上传状态变化驱动 uploading 响应式更新', async () => {
  const c = useImageAttachments(() => 7)
  const seen = []
  watchEffect(() => { seen.push(c.uploading.value) })
  await c.addFiles([file()])
  expect(seen).toContain(true) // 压缩/上传中至少触发过一次
  expect(seen[seen.length - 1]).toBe(false) // 完成后回到 false
  expect(c.uploading.value).toBe(false)
})

it('mergeRejected 直接标 ready 且按 id 去重', () => {
  const c = useImageAttachments(() => 7)
  const row = { id: 1, filename: 'a.png', mime: 'image/png', size_bytes: 10, url: '/api/attachments/1' }
  c.mergeRejected([row, row])
  expect(c.items.value.length).toBe(1)
  expect(c.readyItems()).toEqual([row])
  expect(uploadAttachment).not.toHaveBeenCalled()
})
