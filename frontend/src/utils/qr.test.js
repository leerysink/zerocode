// 二维码矩阵 → SVG path 的渲染转换（编码由库负责，这里只钉住输出形状与确定性）
import { describe, expect, it } from 'vitest'
import { qrMatrix } from './qr'

describe('qrMatrix', () => {
  it('空串不出码', () => {
    expect(qrMatrix('')).toBe(null)
  })

  it('返回合法模数方阵与只含方块命令的 path', () => {
    const qr = qrMatrix('https://aaa.example.test/h5/')
    expect(qr.size).toBeGreaterThanOrEqual(21)
    expect((qr.size - 21) % 4).toBe(0) // QR 版本每升一版多 4 行
    expect(qr.path.startsWith('M')).toBe(true)
    expect(/^[Mhvz0-9\s-]+$/.test(qr.path)).toBe(true)
  })

  it('同一地址稳定、不同地址不同', () => {
    expect(qrMatrix('https://aaa.example.test/h5/')).toEqual(qrMatrix('https://aaa.example.test/h5/'))
    expect(qrMatrix('https://aaa.example.test/h5/').path)
      .not.toBe(qrMatrix('https://bbb.example.test/h5/').path)
  })
})
