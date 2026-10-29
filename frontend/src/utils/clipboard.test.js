// @vitest-environment happy-dom
// 剪贴板的两条路：async Clipboard API 只在安全上下文（https 或 localhost）存在，用 IP + http
// 访问控制台时 navigator.clipboard 是 undefined；execCommand('copy') 反过来在两种上下文里都
// 能用（已废弃但各浏览器仍实现）。所以这里只承诺一件事：能复制就返回 true，两条路都不通返回
// false，由调用方如实报错——不静默、也不谎报「已复制」。
import { afterEach, describe, expect, it, vi } from 'vitest'
import { copyText } from './clipboard'

const setClipboard = (value) => Object.defineProperty(window.navigator, 'clipboard',
  { value, configurable: true })

// execCommand 是浏览器 API，happy-dom 没实现，只能桩。桩里读真实 DOM：断言的是「复制那一刻
// 页面上那个临时节点承载的文本」，而不是桩自己记的调用参数。
const stubExecCommand = (impl) => {
  const spy = vi.fn(impl)
  Object.defineProperty(document, 'execCommand', { value: spy, configurable: true })
  return spy
}

afterEach(() => {
  setClipboard(undefined)
  delete document.execCommand
  document.body.innerHTML = ''
})

describe('copyText', () => {
  it('安全上下文：走 Clipboard API，不碰 execCommand', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    setClipboard({ writeText })
    const exec = stubExecCommand(() => true)

    await expect(copyText('https://aaa.example.test')).resolves.toBe(true)

    expect(writeText).toHaveBeenCalledWith('https://aaa.example.test')
    expect(exec).not.toHaveBeenCalled()
  })

  it('边界：http + IP 访问时 navigator.clipboard 不存在 → 降级 execCommand 仍能复制', async () => {
    setClipboard(undefined)
    const exec = stubExecCommand(() => {
      expect(document.querySelector('textarea').value).toBe('https://aaa.example.test')
      return true
    })

    await expect(copyText('https://aaa.example.test')).resolves.toBe(true)

    expect(exec).toHaveBeenCalledWith('copy')
  })

  it('边界：Clipboard API 被拒（窗口失焦/权限没给）→ 也再试一次降级', async () => {
    setClipboard({ writeText: vi.fn().mockRejectedValue(new Error('NotAllowedError')) })
    const exec = stubExecCommand(() => true)

    await expect(copyText('https://aaa.example.test')).resolves.toBe(true)

    expect(exec).toHaveBeenCalledWith('copy')
  })

  it('降级用的临时节点用完就摘，不在页面里留隐藏 textarea', async () => {
    stubExecCommand(() => {
      expect(document.querySelectorAll('textarea').length).toBe(1)
      return true
    })

    await copyText('https://aaa.example.test')

    expect(document.querySelectorAll('textarea').length).toBe(0)
  })

  it('边界：两条路都不通 → 返回 false，不抛异常', async () => {
    setClipboard({ writeText: vi.fn().mockRejectedValue(new Error('NotAllowedError')) })
    stubExecCommand(() => false)

    await expect(copyText('https://aaa.example.test')).resolves.toBe(false)
  })

  it('边界：execCommand 根本没实现（老浏览器/非浏览器环境）→ 同样返回 false', async () => {
    delete document.execCommand

    await expect(copyText('https://aaa.example.test')).resolves.toBe(false)
  })

  it('边界：空地址不写剪贴板，直接判失败', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    setClipboard({ writeText })
    const exec = stubExecCommand(() => true)

    await expect(copyText('')).resolves.toBe(false)

    expect(writeText).not.toHaveBeenCalled()
    expect(exec).not.toHaveBeenCalled()
  })
})
