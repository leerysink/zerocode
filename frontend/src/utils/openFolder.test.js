// 「打开文件夹」方式判定：auto 是“能直开就直开，远端就走内置”的唯一地址分流点，
// 纯函数化是为了把三种取值 × 各种访问地址的组合全部钉死，不用挂组件。
// 内置那一路现在是个独立页面（新标签打开），地址拼接与被拦截的返回值也一起钉在这里。
import { afterEach, describe, expect, it, vi } from 'vitest'
import { isLocalHostname, openWorkspaceFilesPage, resolveOpenFolderMode, workspaceFilesPath } from './openFolder'

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

describe('workspaceFilesPath 内置文件管理器整页地址', () => {
  it('数字与字符串 id 得到同一条路径，19 位大整数按字符串直传不丢精度', () => {
    expect(workspaceFilesPath(7)).toBe('/workspaces/7/files')
    expect(workspaceFilesPath('7')).toBe('/workspaces/7/files')
    // 雪花 id 一旦被 Number() 过就会变成 1.2345e+18 这种形态，路径直接废掉
    expect(workspaceFilesPath('2026092201000900123')).toBe('/workspaces/2026092201000900123/files')
    expect(workspaceFilesPath('0')).toBe('/workspaces/0/files')
  })

  it('边界：空值与脏 id 不给地址（宁可不打开标签，也不开一个必然 404 的页面）', () => {
    for (const id of [undefined, null, '', ' ', 'abc', '7 ', '1.5', '1e3', '-7', {}, []]) {
      expect(workspaceFilesPath(id)).toBe('')
    }
  })
})

describe('openWorkspaceFilesPage 新标签打开整页', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('开新标签且按工作区命名窗口：同一个工作区反复点复用那一个标签', () => {
    const open = vi.fn(() => ({}))
    vi.stubGlobal('open', open)

    expect(openWorkspaceFilesPage(7)).toBe(true)

    expect(open).toHaveBeenCalledWith('/workspaces/7/files', 'zerocode-files-7')
  })

  it('带上工作区名时编码进 query，中文名与 #、&、空格都不会把地址拆坏', () => {
    const open = vi.fn(() => ({}))
    vi.stubGlobal('open', open)

    openWorkspaceFilesPage(7, '甲 / 乙#v2&x')

    const url = open.mock.calls[0][0]
    expect(url).toBe('/workspaces/7/files?name=%E7%94%B2%20%2F%20%E4%B9%99%23v2%26x')
    // 关键：# 与 & 必须被编码，否则名字后半段会掉进 fragment / 变成多出来的参数
    expect((url.match(/#/g) || []).length).toBe(0)
    expect(url.split('?')[1]).not.toContain('&')
  })

  it('边界：名字为空、为 0 或压根没传时，不带 ?name=（收藏出来的地址是干净的）', () => {
    const open = vi.fn(() => ({}))
    vi.stubGlobal('open', open)

    openWorkspaceFilesPage(7)
    openWorkspaceFilesPage(7, '')
    openWorkspaceFilesPage(7, null)
    openWorkspaceFilesPage(7, '0')

    expect(open.mock.calls.map((c) => c[0]))
      .toEqual(['/workspaces/7/files', '/workspaces/7/files', '/workspaces/7/files', '/workspaces/7/files?name=0'])
  })

  it('被浏览器拦截（open 返回 null）时如实报 false，让调用方去提示', () => {
    vi.stubGlobal('open', vi.fn(() => null))
    expect(openWorkspaceFilesPage(7)).toBe(false)
  })

  it('边界：id 不合法时一个标签都不开，也不报错', () => {
    const open = vi.fn(() => ({}))
    vi.stubGlobal('open', open)

    for (const id of [undefined, null, '', 'abc']) {
      expect(openWorkspaceFilesPage(id)).toBe(false)
    }
    expect(open).not.toHaveBeenCalled()
  })
})
