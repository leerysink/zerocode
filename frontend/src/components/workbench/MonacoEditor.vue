<script setup>
// 唯一直接 import monaco-editor 的模块。只装核心 editor.worker，不启 TS/JSON 语言服务——
// 语法高亮来自 basic-languages 的 Monarch 词法，不需要语言服务；预算留给主包（ADR-0023）。
// 每个打开文件一个 model（URI 带 workspaceId + 路径），切页签 save/restoreViewState，关页签 dispose。
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as monaco from 'monaco-editor/editor/editor.api.js'
import 'monaco-editor/basic-languages/monaco.contribution.js'
import 'monaco-editor/editor/contrib/find/browser/findController.js'
import 'monaco-editor/editor/contrib/contextmenu/browser/contextmenu.js'
import 'monaco-editor/editor/contrib/bracketMatching/browser/bracketMatching.js'
import EditorWorker from 'monaco-editor/editor/editor.worker.js?worker'

self.MonacoEnvironment = { getWorker: () => new EditorWorker() }

const props = defineProps({
  workspaceId: { type: [Number, String], required: true },
  path: { type: String, default: '' },
  value: { type: String, default: '' },
  savedValue: { type: String, default: '' },
  language: { type: String, default: 'plaintext' },
  readOnly: { type: Boolean, default: false },
  theme: { type: String, default: 'dark' },
  openPaths: { type: Array, default: () => [] },
})

const emit = defineEmits(['update:value', 'update:dirty', 'save', 'cursor'])

const host = ref(null)
let editor = null
let applying = false
const models = new Map() // path -> { model, viewState, savedValue }

function modelUri(path) {
  const encoded = String(path).split('/').map(encodeURIComponent).join('/')
  return monaco.Uri.parse(`inmemory://zerocode/${encodeURIComponent(props.workspaceId)}/${encoded}`)
}

function ensureEntry(path) {
  let entry = models.get(path)
  if (!entry) {
    const model = monaco.editor.createModel(props.value, props.language, modelUri(path))
    entry = { model, viewState: null, savedValue: props.value }
    models.set(path, entry)
  }
  return entry
}

// 外部内容（重新加载 / 恢复）替换缓冲区时，以替换后的版本为新基准，避免一挂载就脏
function applyValue(entry, value) {
  if (entry.model.getValue() === value) return false
  applying = true
  entry.model.setValue(value)
  entry.savedValue = value
  applying = false
  return true
}

function applyModel(path) {
  if (!editor) return
  if (!path) {
    editor.setModel(null)
    return
  }
  const entry = ensureEntry(path)
  applyValue(entry, props.value)
  editor.setModel(entry.model)
  monaco.editor.setModelLanguage(entry.model, props.language)
  editor.updateOptions({ readOnly: props.readOnly })
  if (entry.viewState) editor.restoreViewState(entry.viewState)
  editor.focus()
}

onMounted(() => {
  editor = monaco.editor.create(host.value, {
    model: null,
    theme: props.theme === 'light' ? 'vs' : 'vs-dark',
    readOnly: props.readOnly,
    automaticLayout: true,
    minimap: { enabled: false },
    fontSize: 13,
    tabSize: 2,
    scrollBeyondLastLine: false,
    smoothScrolling: true,
    renderWhitespace: 'selection',
  })
  applyModel(props.path)

  editor.onDidChangeModelContent(() => {
    if (!editor || !props.path || applying) return
    const entry = models.get(props.path)
    if (!entry) return
    const value = editor.getValue()
    emit('update:value', value)
    // 脏态按「缓冲区是否还等于已写盘那份内容」判定，不用 alternativeVersionId：
    // Monaco 的撤销在内容变更事件之后才恢复 alternativeVersionId，事件里读到的还是新号，
    // 撤销回保存态会漏报 dirty=false（浏览器验收实测）。内容比较不依赖事件顺序。
    emit('update:dirty', value !== entry.savedValue)
  })

  editor.onDidChangeCursorSelection((e) => {
    const pos = e.selection.getPosition()
    const selectedModel = editor.getModel()
    if (!selectedModel) return
    const selected = selectedModel.getValueInRange(e.selection).length
    emit('cursor', { line: pos.lineNumber, column: pos.column, selected })
  })

  editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => emit('save'))
})

watch(() => props.path, (next, prev) => {
  if (!editor) return
  if (prev) {
    const old = models.get(prev)
    if (old) old.viewState = editor.saveViewState()
  }
  applyModel(next)
})

watch(() => props.value, (value) => {
  if (!editor || !props.path) return
  const entry = models.get(props.path)
  if (!entry) return
  if (applyValue(entry, value)) emit('update:dirty', false)
})

// 保存成功：已写盘的这份内容成为该 model 的新基准。保存期间继续输入过也照样前移基准——
// 基准代表「磁盘上是什么」，不是「编辑器当前是什么」；缓冲区与它不等就仍然是脏的，
// 用户把内容改回刚保存的版本时也能正确收回脏态。
watch(() => props.savedValue, (value) => {
  if (!editor || !props.path) return
  const entry = models.get(props.path)
  if (!entry) return
  entry.savedValue = value
  emit('update:dirty', entry.model.getValue() !== value)
})

watch(() => props.language, (language) => {
  const entry = models.get(props.path)
  if (entry) monaco.editor.setModelLanguage(entry.model, language)
})

watch(() => props.readOnly, (readOnly) => {
  editor?.updateOptions({ readOnly })
})

watch(() => props.theme, (theme) => {
  monaco.editor.setTheme(theme === 'light' ? 'vs' : 'vs-dark')
})

watch(() => props.openPaths, (paths) => {
  const keep = new Set(paths || [])
  for (const [path, entry] of models) {
    if (keep.has(path)) continue
    entry.model.dispose()
    models.delete(path)
  }
})

onBeforeUnmount(() => {
  editor?.dispose()
  editor = null
  for (const entry of models.values()) entry.model.dispose()
  models.clear()
})
</script>

<template>
  <div ref="host" class="zc-monaco"></div>
</template>

<style scoped>
.zc-monaco {
  flex: 1;
  min-height: 0;
  width: 100%;
  height: 100%;
}
</style>
