// SqlEditor 的键位（Ticket 006 / 验收 3）。
//
// 单独成模块有两个理由：
// 1. `<script setup>` 不能有具名导出，抽出来才能在 happy-dom 里直接断言「哪个绑定最终命中」
//    （见同目录 sqlEditorKeys.test.js）；真实 keydown 仍归浏览器实测。
// 2. 必须包 Prec.highest。CM6 的 keymap facet 是「先注册 / 高优先级先查」，而 basicSetup
//    把含 `Mod-Enter → insertBlankLine` 的 defaultKeymap 注册在最前面；普通 keymap.of
//    追加在后面会被这条默认绑定整个吃掉——实测时 Ctrl+Enter 只插空行不执行就是这个。
//
// 只做「报事件」：拆几条、要不要确认、格式化取哪一段，都由 SqlConsole 决定。
// run 必须返回 true（表示已处理），否则 CM 会继续往下跑默认命令，白插一个空行。
import { Prec } from '@codemirror/state'
import { keymap } from '@codemirror/view'

export function sqlEditorKeymap(emit) {
  const fire = (event) => () => {
    emit?.(event)
    return true
  }
  return Prec.highest(keymap.of([
    { key: 'Mod-Enter', run: fire('execute') },
    { key: 'Mod-Shift-f', run: fire('format') },
  ]))
}
