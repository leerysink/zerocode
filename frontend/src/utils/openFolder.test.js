// 「打开文件夹」方式判定：这是"能直开就直开，远端就走内置"的唯一裁决点，
// 纯函数化是为了把三种取值 × 各种访问地址的组合全部钉死，不用挂组件。
import { describe, it, expect } from 'vitest'
import { isLocalHostname, resolveOpenFolderMode } from './openFolder'

describe('isLocalHostname', () => {
  it('回环形态判为本机（浏览器与后端同机，explorer 开在用户眼前）', () => {
    for (const h of ['localhost', 'LocalHost', '127.0.0.1', '127.5.5.5', '::1', '[::1]', 'localhost.']) {
      expect(isLocalHostname(h)).toBe(true)
    }
  })

  it('局域网 IP、域名与空值判为远端（服务端开窗口用户看不见）', () => {
    for (const h of ['192.168.1.20', '10.0.0.5', 'zerocode.internal', 'example.com', '', null, undefined]) {
      expect(isLocalHostname(h)).toBe(false)
    }
  })
})

describe('resolveOpenFolderMode', () => {
  it('auto：本机访问走系统资源管理器，远端访问走内置文件管理器', () => {
    expect(resolveOpenFolderMode('auto', 'localhost')).toBe('system')
    expect(resolveOpenFolderMode('auto', '127.0.0.1')).toBe('system')
    expect(resolveOpenFolderMode('auto', '192.168.1.20')).toBe('web')
    expect(resolveOpenFolderMode('auto', 'zerocode.example.com')).toBe('web')
  })

  it('缺省与未知取值都按 auto 处理（老配置库里没这个键也要能用）', () => {
    expect(resolveOpenFolderMode(undefined, 'localhost')).toBe('system')
    expect(resolveOpenFolderMode('', '10.1.2.3')).toBe('web')
    expect(resolveOpenFolderMode('typo', 'localhost')).toBe('system')
  })

  it('system / web 是人工强制覆盖，优先于地址判定', () => {
    expect(resolveOpenFolderMode('system', '192.168.1.20')).toBe('system')
    expect(resolveOpenFolderMode('web', 'localhost')).toBe('web')
  })
})
