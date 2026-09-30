// @vitest-environment happy-dom
// Ticket 006 / 验收 3：编辑器内的 Ctrl+Enter 执行、Ctrl+Shift+F 格式化。
//
// 这组用例是浏览器实测踩出来的回归守门人：实测时 Ctrl+Enter 只插了一个空行、不执行。
// 查下来 CM6 的 keymap facet 是「先注册 / 高优先级先查」（@codemirror/view 源码注释原文），
// 而 basicSetup 把含 `Mod-Enter → insertBlankLine` 的 defaultKeymap 注册在最前面，
// 普通 keymap.of 追加在后面就被默认绑定整个盖掉。所以键位必须包 Prec.highest，
// 且不能顺手把普通 Enter 也吃掉（打字换行还得照常）。
// 真实 DOM 里的 keydown 归浏览器实测；这里锁的是「哪个绑定最终命中」这层语义。
import { describe, expect, it, vi } from 'vitest'
import { EditorState } from '@codemirror/state'
import { keymap } from '@codemirror/view'
import { basicSetup } from 'codemirror'
import { sql, PostgreSQL } from '@codemirror/lang-sql'

import { sqlEditorKeymap } from './sqlEditorKeys'

// 拍平成一个绑定数组，取第一个命中该键名的绑定——CM 的 buildKeymap 也是这个语义：
// 同名键按注册顺序累积 run，第一个返回 true 的就不再往下走。
const firstBindingFor = (extensions, name) => {
  const state = EditorState.create({ doc: 'select 1', extensions })
  return state.facet(keymap).flat().find((b) => b.key === name)
}

const editorWith = (emit) => [
  basicSetup,
  sql({ dialect: PostgreSQL }),
  sqlEditorKeymap(emit),
]

describe('SqlEditor 键位（Ticket 006 / 验收 3）', () => {
  it('Ctrl+Enter 命中我们的执行，而不是 basicSetup 的 insertBlankLine', () => {
    const emit = vi.fn()
    const bind = firstBindingFor(editorWith(emit), 'Mod-Enter')
    expect(bind).toBeTruthy()
    expect(bind.run({})).toBe(true)
    expect(emit).toHaveBeenCalledWith('execute')
  })

  it('Ctrl+Shift+F 命中我们的格式化', () => {
    const emit = vi.fn()
    const bind = firstBindingFor(editorWith(emit), 'Mod-Shift-f')
    expect(bind).toBeTruthy()
    expect(bind.run({})).toBe(true)
    expect(emit).toHaveBeenCalledWith('format')
  })

  it('边界：键位与 basicSetup 的注册顺序反过来，照样抢得到（靠优先级，不碰数组顺序运气）', () => {
    const emit = vi.fn()
    const bind = firstBindingFor([sqlEditorKeymap(emit), basicSetup], 'Mod-Enter')
    expect(bind).toBeTruthy()
    bind.run({})
    expect(emit).toHaveBeenCalledWith('execute')
  })

  it('边界：不劫持普通 Enter——普通 Enter 仍由 CM 默认键位处理（打字照常换行）', () => {
    const emit = vi.fn()
    const ours = EditorState.create({
      doc: 'select 1',
      extensions: [sqlEditorKeymap(emit)],
    }).facet(keymap).flat()
    // 我们的表里只应有这两个组合键，没有裸 Enter / Shift-Enter
    expect(ours.map((b) => b.key).sort()).toEqual(['Mod-Enter', 'Mod-Shift-f'])
    // 合并进完整编辑器后，裸 Enter 第一个命中的不是我们的绑定（调用它不会 emit）
    const plainEnter = firstBindingFor(editorWith(emit), 'Enter')
    expect(plainEnter).toBeTruthy()
    expect(ours.includes(plainEnter)).toBe(false)
  })

  it('边界：两个绑定都必须返回 true，返回 false 会让 CM 继续跑默认命令（又插一个空行）', () => {
    const emit = vi.fn()
    for (const name of ['Mod-Enter', 'Mod-Shift-f']) {
      expect(firstBindingFor(editorWith(emit), name).run({})).toBe(true)
    }
  })

  it('边界：一次按键只 emit 一次，不重复触发执行', () => {
    const emit = vi.fn()
    firstBindingFor(editorWith(emit), 'Mod-Enter').run({})
    expect(emit).toHaveBeenCalledTimes(1)
  })

  it('边界：不越权占其他组合键（Ctrl+Shift+X 不在我们的绑定表里）', () => {
    const ours = EditorState.create({
      doc: '',
      extensions: [sqlEditorKeymap(vi.fn())],
    }).facet(keymap).flat()
    expect(ours.some((b) => b.key === 'Mod-Shift-x')).toBe(false)
    expect(ours.some((b) => b.key === 'Mod-s')).toBe(false)
  })

  it('边界：emit 缺失（编辑器还没挂载完就被按）只报 handled，不抛错', () => {
    const bind = firstBindingFor([sqlEditorKeymap(undefined)], 'Mod-Enter')
    expect(() => bind.run({})).not.toThrow()
    expect(bind.run({})).toBe(true)
  })
})
