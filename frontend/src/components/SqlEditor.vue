<script setup>
// SQL 编辑器薄壳（ADR 0006：CodeMirror 6）。
//
// 刻意做薄：CodeMirror 只在 onMounted 之后活在这个组件里，happy-dom 没有布局引擎，
// 测试一律用 textarea 桩替换它，控制台的逻辑（拆分/确认/批量请求/渲染/格式化取段）全在 SqlConsole。
// 这个壳只留三样东西：选区读数、就地写回、键盘入口（Ticket 006）。表名/列名补全（Ticket 008）从这里长。
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { EditorState } from '@codemirror/state'
import { EditorView } from '@codemirror/view'
import { HighlightStyle, syntaxHighlighting } from '@codemirror/language'
import { tags as t } from '@lezer/highlight'
import { basicSetup } from 'codemirror'
import { sql, PostgreSQL } from '@codemirror/lang-sql'
import { sqlEditorKeymap } from './sqlEditorKeys'

const props = defineProps({
  modelValue: { type: String, default: '' },
  tables: { type: Array, default: () => [] }, // 补全数据源，Ticket 008 接线
})
const emit = defineEmits(['update:modelValue', 'execute', 'format'])

// token 只打类名不上色，颜色全部由下面的 theme 给（US-01：跟着操作台主色走）。
// 不这么做的话 basicSetup 里的 defaultHighlightStyle 会直接写内联 color，theme 改不动。
// （@lezer/highlight 1.2 没导出成品的 classHighlightStyle，只能自带一份。）
const classHighlightStyle = HighlightStyle.define([
  { tag: t.keyword, class: 'cm-keyword' },
  { tag: [t.string, t.special(t.string)], class: 'cm-string' },
  { tag: t.number, class: 'cm-number' },
  { tag: [t.bool, t.null], class: 'cm-atom' },
  { tag: t.comment, class: 'cm-comment' },
  { tag: t.operator, class: 'cm-operator' },
  { tag: [t.variableName, t.propertyName], class: 'cm-variableName' },
  { tag: t.typeName, class: 'cm-type' },
])

// 编辑器只管自己的“内”（token 配色 / 光标 / 选中）；外框、高度、等宽字体由 db-admin.less 的
// .zc-sql-editor-host 统一给（避免两处各画一重 1px 边框、也避免 min-height 和实际高度错位）。
const consoleTheme = EditorView.theme({
  '&': { backgroundColor: '#fff', fontSize: '13px' },
  '&.cm-focused': { outline: 'none' },
  '.cm-content': { caretColor: '#4a6d94', padding: '8px 0' },
  '.cm-cursor, .cm-dropCursor': { borderLeftColor: '#4a6d94' },
  '.cm-selectionBackground, &.cm-focused .cm-selectionBackground, .cm-content ::selection': { backgroundColor: '#dce6f0' },
  '.cm-activeLine': { backgroundColor: '#f4f8fb' },
  '.cm-gutters': { backgroundColor: '#f7f9fb', color: '#8d98a6', border: 'none', borderRight: '1px solid #dfe5ec' },
  '.cm-keyword': { color: '#2f6fa7', fontWeight: '600' },
  '.cm-def, .cm-variableName, .cm-type': { color: '#374555' },
  '.cm-string, .cm-string2': { color: '#2e7d4f' },
  '.cm-number, .cm-atom': { color: '#a15c00' },
  '.cm-comment': { color: '#7f8b98', fontStyle: 'italic' },
  '.cm-operator, .cm-punctuation': { color: '#5c6875' },
}, { dark: false })

// 快捷键只在编辑器内生效（US-02 / US-10 验收 3：焦点在页面上其他控件时不误触发）。
// 走 keymap 而不是 window keydown：CM 自己消化按键，Enter 换行、Ctrl+F 搜索这些默认行为不受影响，
// 也不用操心组件卸载后监听器漏摘。「做什么」由控制台决定（拆分、确认、格式化取段），这里只报事件。
// 为什么要单独成模块 + Prec.highest，见 sqlEditorKeys.js 头注释。
const editorKeymap = sqlEditorKeymap((event) => emit(event))

const host = ref(null)
let view = null

onMounted(() => {
  view = new EditorView({
    state: EditorState.create({
      doc: props.modelValue || '',
      extensions: [
        basicSetup,
        sql({ dialect: PostgreSQL }),
        // 非 fallback 的 class 样式优先于 basicSetup 自带的 defaultHighlightStyle（后者只兜底未覆盖的 tag）。
        syntaxHighlighting(classHighlightStyle),
        EditorView.lineWrapping,
        editorKeymap,
        consoleTheme,
        EditorView.updateListener.of((u) => {
          if (u.docChanged) emit('update:modelValue', u.state.doc.toString())
        }),
      ],
    }),
    parent: host.value,
  })
})

onBeforeUnmount(() => {
  if (view) view.destroy()
  view = null
})

// 外部改值（切页签、载入脚本）才回写文档；自己打字触发的 modelValue 与原 doc 相同，跳过。
watch(() => props.modelValue, (val) => {
  if (!view) return
  const cur = view.state.doc.toString()
  const next = val ?? ''
  if (cur === next) return
  view.dispatch({ changes: { from: 0, to: cur.length, insert: next } })
})

defineExpose({
  // 当前选区文本；无选区（光标塌成一个点）返回空串，控制台据此决定「只跑选区」还是「跑全段」。
  getSelectionText: () => (view
    ? view.state.sliceDoc(view.state.selection.main.from, view.state.selection.main.to)
    : ''),
  // 选区下标；没挂上编辑器时给一个塌点（调用方会把它当无选区处理）。
  getSelectionRange: () => (view
    ? { from: view.state.selection.main.from, to: view.state.selection.main.to }
    : { from: 0, to: 0 }),
  // 就地替换 [from, to)；sel 是“替换后选区应该落在哪”（新文档坐标）。
  // 一次 dispatch 同时改文本与选区：CM 声明 selection 里的偏移按**事务之后**的文档计算，
  // 拆成两次 dispatch 会多一次可见重绘，中间还能被用户插进一个字符。
  replaceRange: (from, to, insert, sel) => {
    if (!view) return
    const spec = { changes: { from, to, insert } }
    if (sel && Number.isInteger(sel.start) && Number.isInteger(sel.end)) {
      spec.selection = { anchor: sel.start, head: sel.end }
    }
    view.dispatch(spec)
  },
  focus: () => view && view.focus(),
})
</script>

<template>
  <div ref="host" class="zc-sql-editor-host" />
</template>
