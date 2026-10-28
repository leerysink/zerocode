import { describe, it, expect } from 'vitest'
import {
  ACCEPTED_IMAGE_MIMES, MAX_ATTACHMENTS, MAX_ATTACHMENT_BYTES, MAX_MESSAGE_BYTES,
  MAX_SOURCE_BYTES, formatBytes, newClientMsgId, validateCompressed, validateImageFile,
} from './imageAttachments'

function file(name, type, size) {
  return { name, type, size }
}

describe('validateImageFile', () => {
  it('接受四种图片', () => {
    for (const mime of ACCEPTED_IMAGE_MIMES) {
      expect(validateImageFile(file('x', mime, 10), 0)).toBeNull()
    }
  })

  it('超过 4 张拒绝', () => {
    expect(validateImageFile(file('x', 'image/png', 10), MAX_ATTACHMENTS))
      .toContain('最多')
  })

  it('SVG 与其他类型拒绝', () => {
    expect(validateImageFile(file('x.svg', 'image/svg+xml', 10), 0)).toContain('不支持')
    expect(validateImageFile(file('x.pdf', 'application/pdf', 10), 0)).toContain('不支持')
  })

  it('原始文件超过 20MB 拒绝', () => {
    expect(validateImageFile(file('x', 'image/png', MAX_SOURCE_BYTES + 1), 0))
      .toContain('20MB')
  })
})

describe('validateCompressed', () => {
  it('压缩后单张超过 5MB 拒绝', () => {
    expect(validateCompressed({ size: MAX_ATTACHMENT_BYTES + 1 }, []))
      .toContain('5MB')
  })

  it('本批合计超过 10MB 拒绝', () => {
    expect(validateCompressed({ size: 2 }, [{ size_bytes: MAX_MESSAGE_BYTES }]))
      .toContain('10MB')
  })

  it('合计未超限通过', () => {
    expect(validateCompressed({ size: 2 }, [{ size_bytes: 10 }])).toBeNull()
  })
})

describe('formatBytes', () => {
  it('按量级给 B / KB / MB', () => {
    expect(formatBytes(0)).toBe('0 B')
    expect(formatBytes(1024)).toBe('1.0 KB')
    expect(formatBytes(1024 * 1024)).toBe('1.0 MB')
  })
})

describe('newClientMsgId', () => {
  it('两次生成不重复', () => {
    expect(newClientMsgId()).not.toBe(newClientMsgId())
  })
})
