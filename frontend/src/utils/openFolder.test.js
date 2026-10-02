// 「打开文件夹」方式判定：auto 是“能直开就直开，远端就走内置”的唯一地址分流点，
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
  it('显式 auto：本机访问走系统资源管理器，远端访问走内置文件管理器', () => {
    expect(resolveOpenFolderMode('auto', 'localhost')).toBe('system')
    expect(resolveOpenFolderMode('auto', '127.0.0.1')).toBe('system')
    expect(resolveOpenFolderMode('auto', '192.168.1.20')).toBe('web')
    expect(resolveOpenFolderMode('auto', 'zerocode.example.com')).toBe('web')
    // 拿不到访问地址时不猜本机：宁可走网页，也不把窗口开去用户看不见的机器
    expect(resolveOpenFolderMode('auto', undefined)).toBe('web')
    expect(resolveOpenFolderMode('auto', '')).toBe('web')
  })

  it('未配置与未知取值一律走 web（兜底与平台默认同源，不再在 util 里重猜一次 auto）', () => {
    // 默认值只能有一处真源（stores/config.js + changelog 播种）；这里再按地址猜，
    // 等于在两个地方对“没配过时该怎样”给出不同答案，扫代码的人会先信错的那个。
    for (const mode of [undefined, null, '', 'typo', 'Auto', 'WEB', 'SYSTEM']) {
      expect(resolveOpenFolderMode(mode, 'localhost')).toBe('web')
    }
    expect(resolveOpenFolderMode(undefined, '192.168.1.20')).toBe('web')
  })

  it('system / web 是人工强制覆盖（大小写敏感），优先于地址判定', () => {
    expect(resolveOpenFolderMode('system', '192.168.1.20')).toBe('system')
    expect(resolveOpenFolderMode('web', 'localhost')).toBe('web')
    // 'SYSTEM' 不是合法取值，不按强制覆盖走（新兜底下它也不回到地址判定）
    expect(resolveOpenFolderMode('SYSTEM', '192.168.1.20')).toBe('web')
  })
})
