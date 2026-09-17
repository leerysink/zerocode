import { describe, it, expect } from 'vitest'
import { parseDiff } from '../src/utils/diff'

describe('parseDiff', () => {
  it('按行分类 add/del/hunk/ctx', () => {
    const d = ['@@ -1,2 +1,2 @@', 'a', '-old', '+new'].join('\n')
    expect(parseDiff(d)).toEqual([
      { type: 'hunk', text: '@@ -1,2 +1,2 @@' },
      { type: 'ctx', text: 'a' },
      { type: 'del', text: '-old' },
      { type: 'add', text: '+new' },
    ])
  })
})
