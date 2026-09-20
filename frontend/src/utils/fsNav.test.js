// 内置「类桌面」文件管理器的纯逻辑：面包屑、上级、大小/时间显示、下载链接。
// 后端 /api/fs/ls 给的 path 是工作区内相对 posix 路径（根为空串），这里全部按该契约对齐。
import { describe, it, expect } from 'vitest'
import { buildBreadcrumb, formatBytes, formatMtime, parentPath, downloadUrl } from './fsNav'

describe('parentPath', () => {
  it('根没有上级，一级目录的上级是根', () => {
    expect(parentPath('')).toBeNull()
    expect(parentPath('src')).toBe('')
    expect(parentPath('src/components')).toBe('src')
  })
})

describe('buildBreadcrumb', () => {
  it('根用工作区名，逐级带可点击的完整路径', () => {
    expect(buildBreadcrumb('我的应用', '')).toEqual([{ label: '我的应用', path: '' }])
    expect(buildBreadcrumb('我的应用', 'src/components')).toEqual([
      { label: '我的应用', path: '' },
      { label: 'src', path: 'src' },
      { label: 'components', path: 'src/components' },
    ])
  })

  it('没给工作区名时根显示「工作区」', () => {
    expect(buildBreadcrumb('', '')[0].label).toBe('工作区')
  })
})

describe('formatBytes', () => {
  it('目录不给字节数，其余按 KB/MB/GB 保留一位', () => {
    expect(formatBytes(0, true)).toBe('—')
    expect(formatBytes(0, false)).toBe('0 B')
    expect(formatBytes(512, false)).toBe('512 B')
    expect(formatBytes(1536, false)).toBe('1.5 KB')
    expect(formatBytes(5 * 1024 * 1024, false)).toBe('5.0 MB')
    expect(formatBytes(3 * 1024 * 1024 * 1024, false)).toBe('3.0 GB')
  })
})

describe('formatMtime', () => {
  it('epoch 秒渲染成 YYYY-MM-DD HH:mm，缺失给占位符', () => {
    expect(formatMtime(1757900000)).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/)
    expect(formatMtime(0)).toBe('—')
    expect(formatMtime(null)).toBe('—')
  })
})

describe('downloadUrl', () => {
  it('带上工作区与路径参数，路径做编码（中文与斜杠不吃亏）', () => {
    expect(downloadUrl(7, 'a.txt')).toBe('/api/fs/download?workspace_id=7&path=a.txt')
    expect(downloadUrl(7, '报告.txt')).toBe('/api/fs/download?workspace_id=7&path=%E6%8A%A5%E5%91%8A.txt')
    expect(downloadUrl(7, 'src/a.txt')).toBe('/api/fs/download?workspace_id=7&path=src%2Fa.txt')
  })
})
