// 预览日志面板的纯逻辑：seq 去重、上限裁剪、行着色、时间格式化。
import { describe, expect, it } from 'vitest'

import {
  MAX_LINES, appendRows, formatRow, lineKind, rowFromEvent, statusText, tsShort,
} from './deployLog'

const row = (seq, line, extra = {}) => ({ seq, ts: '2026-09-20T22:27:58+08:00',
                                          source: 'platform', service: '平台', line, ...extra })

describe('tsShort', () => {
  it('ISO 串取到秒', () => {
    expect(tsShort('2026-09-20T22:27:58+08:00')).toMatch(/^\d{2}:\d{2}:\d{2}$/)
  })
  it('脏数据不炸：空串/非时间一律空', () => {
    expect(tsShort('')).toBe('')
    expect(tsShort(undefined)).toBe('')
    expect(tsShort('not-a-time')).toBe('')
  })
})

describe('lineKind', () => {
  it('步骤行的成功/失败决定着色', () => {
    expect(lineKind(row(1, '[zerocode] ✓ 预览已就绪 http://localhost:8213'))).toBe('ok')
    expect(lineKind(row(2, '[zerocode] ✗ 启动预览失败：应用不存在'))).toBe('err')
    expect(lineKind(row(3, 'WARNING [test1]   连通性   : 不可用'))).toBe('err')
    expect(lineKind(row(4, '[zerocode] → docker compose up -d 启动 dev 栈'))).toBe('step')
  })
  it('容器来源的普通输出走 out，不被误判成平台步骤', () => {
    expect(lineKind(row(5, 'npm info ok', { source: 'container' }))).toBe('out')
  })
})

describe('appendRows', () => {
  it('REST 历史与 WS 实时重叠时按 seq 去重，并推进 lastSeq', () => {
    const base = [row(1, 'a'), row(2, 'b')]
    const { rows, lastSeq } = appendRows(base, [row(2, 'b'), row(3, 'c')], { lastSeq: 2 })
    expect(rows.map(r => r.line)).toEqual(['a', 'b', 'c'])
    expect(lastSeq).toBe(3)
  })
  it('无 seq 的行（本地补的行）照收，不受去重影响', () => {
    const { rows } = appendRows([row(1, 'a')], [{ seq: 0, line: 'x' }], { lastSeq: 1 })
    expect(rows.map(r => r.line)).toEqual(['a', 'x'])
  })
  it('裁到最近上限：面板长跑几天也不会把浏览器拖死', () => {
    const many = Array.from({ length: 10 }, (_, i) => row(i + 1, `l${i}`))
    const { rows, lastSeq } = appendRows(many, [row(11, 'new')], { max: 3, lastSeq: 10 })
    expect(rows.map(r => r.line)).toEqual(['l8', 'l9', 'new'])
    expect(lastSeq).toBe(11)
    expect(MAX_LINES).toBeGreaterThan(100)
  })
})

describe('rowFromEvent', () => {
  it('dev_log → 一行（带 seq 供去重）', () => {
    const r = rowFromEvent({ type: 'dev_log', seq: 9, line: '[zerocode] ✓ 迁移完成' },
                           '2026-09-20T22:27:58+08:00')
    expect(r).toMatchObject({ seq: 9, line: '[zerocode] ✓ 迁移完成', source: 'platform' })
    expect(r.ts).toBe('2026-09-20T22:27:58+08:00')
  })
  it('hello/ping/dev_status 不是日志，返回 null', () => {
    expect(rowFromEvent({ type: 'hello', app_id: 1 })).toBeNull()
    expect(rowFromEvent({ type: 'ping' })).toBeNull()
    expect(rowFromEvent({ type: 'dev_status', status: 'running' })).toBeNull()
    expect(rowFromEvent(null)).toBeNull()
  })
})

describe('formatRow / statusText', () => {
  it('复制文本带时间与来源，无 ts 时降级成裸行', () => {
    expect(formatRow(row(1, 'hello'))).toMatch(/\| hello$/)
    expect(formatRow({ line: 'bare' })).toBe('bare')
  })
  it('状态文案中文，未知状态原样显示', () => {
    expect(statusText('running')).toBe('运行中')
    expect(statusText(undefined)).toBe('已停止')
    expect(statusText('weird')).toBe('weird')
  })
})
