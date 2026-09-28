<script setup>
// SQL 编辑器薄壳（ADR 0006：CodeMirror 6）。
//
// 刻意做薄：CodeMirror 只在 onMounted 之后活在这个组件里，happy-dom 没有布局引擎，
// 测试一律用 textarea 桩替换它，控制台的逻辑（拆分/确认/批量请求/渲染）全在 SqlConsole。
// 选区优先执行与格式化写回（Ticket 006）、表名/列名补全（Ticket 008）从这里往外长。
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { EditorState } from '@codemirror/state'
import { EditorView } from '@codemirror/view'
import { HighlightStyle, syntaxHighlighting } from '@codemirror/language'
import { tags as t } from '@lezer/highlight'
import { basicSetup } from 'codemirror'
import { sql, PostgreSQL } from '@codemirror/lang-sql'

const props = defineProps({
  modelValue: { type: String, default: '' },
  tables: { type: Array, default: () => [] }, // 补全数据源，Ticket 008 接线
})
const emit = defineEmits(['update:modelValue'])

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
  // 当前选区文本；无选区返回空串（Ticket 006 据此决定"只跑选区"还是"跑全段"）。
  getSelectionText: () => (view
    ? view.state.sliceDoc(view.state.selection.main.from, view.state.selection.main.to)
    : ''),
  focus: () => view && view.focus(),
})
</script>

<template>
  <div ref="host" class="zc-sql-editor-host" />
</template>
