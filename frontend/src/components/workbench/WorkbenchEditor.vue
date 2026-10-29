<script setup>
// 编辑区容器：空态 / 读取中 / 读取失败 + 下载 / 正常挂 Monaco。
// 这里是编辑器与宿主的唯一接缝：内容、脏态、保存、光标都经 emit 上报，宿主不直接碰 monaco。
// Markdown 额外有源码 / 渲染 / 分屏三态：默认渲染，渲染视图只吃当前缓冲区（含未保存改动）。
// 渲染模式不卸载 Monaco——模型与撤销栈留在原地，切回源码接着改。
import { computed, ref, watch } from 'vue'
import MonacoEditor from './MonacoEditor.vue'
import { isMarkdown, languageOf } from '../../utils/fileTypes'
import { downloadUrl } from '../../utils/fsNav'
import { renderDocMarkdown } from '../../utils/markdownDoc'

const props = defineProps({
  workspaceId: { type: [Number, String], required: true },
  path: { type: String, default: '' },
  content: { type: String, default: '' },
  savedContent: { type: String, default: '' },
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
  readOnly: { type: Boolean, default: false },
  theme: { type: String, default: 'dark' },
  openPaths: { type: Array, default: () => [] },
})

const emit = defineEmits(['update:content', 'update:dirty', 'save', 'cursor', 'open-file', 'update:mode'])

const language = computed(() => languageOf(props.path))
const errorText = computed(() => String(props.error ?? '').trim() || '读取失败')
const isMd = computed(() => isMarkdown(props.path))
const mode = ref(isMd.value ? 'render' : 'source')

// 换文件回到该文件的默认态：md 渲染、其余源码（恢复的 md 页签也从这里落回渲染视图）
watch(() => props.path, (path) => {
  mode.value = isMarkdown(path) ? 'render' : 'source'
})

const showEditor = computed(() => !isMd.value || mode.value !== 'render')
const showRender = computed(() => isMd.value && mode.value !== 'source')
const bodyClass = computed(() => (isMd.value ? `is-${mode.value}` : 'is-source'))
const rendered = computed(() => (isMd.value
  ? renderDocMarkdown(props.content, { filePath: props.path, workspaceId: props.workspaceId })
  : ''))

function setMode(next) {
  mode.value = next
  emit('update:mode', next)
}

// 站内相对链接由 markdownDoc 标好 data-file-path，这里只负责拦下默认跳转并交给宿主开页签
function onPreviewClick(event) {
  const link = event.target?.closest?.('a[data-file-path]')
  if (!link) return
  event.preventDefault()
  const path = link.getAttribute('data-file-path')
  if (path) emit('open-file', path)
}
</script>

<template>
  <div class="zc-wbe" :data-theme="theme">
    <div v-if="!path" class="zc-wb-empty">从左侧选择文件开始编辑</div>
    <a-spin v-else-if="loading" class="zc-wb-loading" />
    <div v-else-if="error" class="zc-wb-error">
      <p class="zc-wb-error-text">{{ errorText }}</p>
      <a class="zc-wb-download" :href="downloadUrl(workspaceId, path)">下载文件</a>
    </div>
    <template v-else>
      <div v-if="isMd" class="zc-wbe-bar">
        <button type="button" class="zc-wb-mode zc-wb-mode-source" :class="{ 'is-active': mode === 'source' }" @click="setMode('source')">源码</button>
        <button type="button" class="zc-wb-mode zc-wb-mode-render" :class="{ 'is-active': mode === 'render' }" @click="setMode('render')">渲染</button>
        <button type="button" class="zc-wb-mode zc-wb-mode-split" :class="{ 'is-active': mode === 'split' }" @click="setMode('split')">分屏</button>
      </div>
      <div class="zc-wbe-body" :class="bodyClass">
        <div v-show="showEditor" class="zc-wbe-editor">
          <MonacoEditor
            :workspace-id="workspaceId"
            :path="path"
            :value="content"
            :saved-value="savedContent"
            :language="language"
            :read-only="readOnly"
            :theme="theme"
            :open-paths="openPaths"
            @update:value="(v) => emit('update:content', v)"
            @update:dirty="(d) => emit('update:dirty', d)"
            @save="emit('save')"
            @cursor="(c) => emit('cursor', c)"
          />
        </div>
        <div
          v-if="showRender"
          class="zc-wbe-preview zc-md"
          v-html="rendered"
          @click="onPreviewClick"
        />
      </div>
    </template>
  </div>
</template>

<style scoped>
.zc-wbe {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  height: 100%;
}
.zc-wb-empty,
.zc-wb-loading {
  margin: 40px auto;
  font-size: 13px;
  color: var(--zc-text-sub);
}
.zc-wb-loading {
  display: block;
}
.zc-wb-error {
  margin: 16px;
  padding: 12px 14px;
  border: 1px solid var(--zc-border);
  border-radius: 4px;
  background: var(--zc-bg);
  font-size: 12px;
  color: var(--zc-text-sub);
}
.zc-wb-error-text {
  margin: 0 0 8px;
}
.zc-wb-download {
  color: var(--zc-primary, #1677ff);
}

/* Markdown 三态工具栏 */
.zc-wbe-bar {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 2px;
  padding: 4px 8px;
  border-bottom: 1px solid var(--zc-border);
}
.zc-wb-mode {
  border: 1px solid transparent;
  border-radius: 3px;
  background: transparent;
  color: var(--zc-text-sub);
  font-size: 12px;
  line-height: 20px;
  padding: 0 8px;
  cursor: pointer;
}
.zc-wb-mode:hover {
  color: var(--zc-text);
}
.zc-wb-mode.is-active {
  color: var(--zc-text);
  border-color: var(--zc-border);
  background: var(--zc-bg);
}
.zc-wbe-body {
  flex: 1;
  min-height: 0;
  display: flex;
}
.zc-wbe-editor {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
}
.zc-wbe-preview {
  flex: 1;
  min-width: 0;
  min-height: 0;
  overflow: auto;
  padding: 16px 20px;
  font-size: 14px;
  line-height: 1.7;
  color: var(--zc-text);
}
.zc-wbe-body.is-split .zc-wbe-preview {
  border-left: 1px solid var(--zc-border);
}

/* 渲染视图排版（v-html 内容拿不到 scoped 属性，统一走 :deep） */
.zc-wbe-preview :deep(h1),
.zc-wbe-preview :deep(h2),
.zc-wbe-preview :deep(h3),
.zc-wbe-preview :deep(h4),
.zc-wbe-preview :deep(h5),
.zc-wbe-preview :deep(h6) {
  margin: 1.2em 0 0.6em;
  line-height: 1.35;
  color: var(--zc-text);
}
.zc-wbe-preview :deep(h1) {
  font-size: 1.7em;
  padding-bottom: 0.3em;
  border-bottom: 1px solid var(--zc-border);
}
.zc-wbe-preview :deep(h2) {
  font-size: 1.4em;
}
.zc-wbe-preview :deep(p) {
  margin: 0.6em 0;
}
.zc-wbe-preview :deep(a) {
  color: var(--zc-primary, #1677ff);
  text-decoration: none;
}
.zc-wbe-preview :deep(a:hover) {
  text-decoration: underline;
}
.zc-wbe-preview :deep(img) {
  max-width: 100%;
}
.zc-wbe-preview :deep(blockquote) {
  margin: 0.8em 0;
  padding: 2px 12px;
  border-left: 3px solid var(--zc-border);
  color: var(--zc-text-sub);
}
.zc-wbe-preview :deep(table) {
  border-collapse: collapse;
  margin: 0.8em 0;
}
.zc-wbe-preview :deep(th),
.zc-wbe-preview :deep(td) {
  border: 1px solid var(--zc-border);
  padding: 4px 10px;
}
.zc-wbe-preview :deep(th) {
  background: rgba(127, 127, 127, 0.12);
  font-weight: 600;
}
.zc-wbe-preview :deep(hr) {
  border: 0;
  border-top: 1px solid var(--zc-border);
  margin: 1.2em 0;
}
.zc-wbe-preview :deep(code) {
  font-family: Consolas, 'Courier New', Menlo, monospace;
  font-size: 0.9em;
  padding: 1px 4px;
  border-radius: 3px;
  background: rgba(127, 127, 127, 0.18);
}
.zc-wbe-preview :deep(pre) {
  margin: 0.8em 0;
  padding: 12px 14px;
  border-radius: 6px;
  overflow: auto;
}
.zc-wbe-preview :deep(pre code) {
  padding: 0;
  background: transparent;
  font-size: 13px;
}

/* 代码高亮自带一套配色，不依赖全局 .hljs 主题：深浅两套跟着工作台主题走 */
.zc-wbe[data-theme='dark'] .zc-wbe-preview :deep(pre.hljs) {
  background: #1e1e1e;
  color: #d4d4d4;
}
.zc-wbe[data-theme='dark'] .zc-wbe-preview :deep(.hljs-keyword),
.zc-wbe[data-theme='dark'] .zc-wbe-preview :deep(.hljs-built_in),
.zc-wbe[data-theme='dark'] .zc-wbe-preview :deep(.hljs-type),
.zc-wbe[data-theme='dark'] .zc-wbe-preview :deep(.hljs-tag) {
  color: #569cd6;
}
.zc-wbe[data-theme='dark'] .zc-wbe-preview :deep(.hljs-string),
.zc-wbe[data-theme='dark'] .zc-wbe-preview :deep(.hljs-regexp),
.zc-wbe[data-theme='dark'] .zc-wbe-preview :deep(.hljs-attr) {
  color: #ce9178;
}
.zc-wbe[data-theme='dark'] .zc-wbe-preview :deep(.hljs-number),
.zc-wbe[data-theme='dark'] .zc-wbe-preview :deep(.hljs-literal) {
  color: #b5cea8;
}
.zc-wbe[data-theme='dark'] .zc-wbe-preview :deep(.hljs-comment) {
  color: #6a9955;
  font-style: italic;
}
.zc-wbe[data-theme='dark'] .zc-wbe-preview :deep(.hljs-title),
.zc-wbe[data-theme='dark'] .zc-wbe-preview :deep(.hljs-function) {
  color: #dcdcaa;
}
.zc-wbe[data-theme='dark'] .zc-wbe-preview :deep(.hljs-variable),
.zc-wbe[data-theme='dark'] .zc-wbe-preview :deep(.hljs-name),
.zc-wbe[data-theme='dark'] .zc-wbe-preview :deep(.hljs-template-variable) {
  color: #9cdcfe;
}
.zc-wbe[data-theme='dark'] .zc-wbe-preview :deep(.hljs-meta),
.zc-wbe[data-theme='dark'] .zc-wbe-preview :deep(.hljs-selector-tag) {
  color: #c586c0;
}
.zc-wbe[data-theme='light'] .zc-wbe-preview :deep(pre.hljs) {
  background: #f6f8fa;
  color: #24292f;
}
.zc-wbe[data-theme='light'] .zc-wbe-preview :deep(.hljs-keyword),
.zc-wbe[data-theme='light'] .zc-wbe-preview :deep(.hljs-built_in),
.zc-wbe[data-theme='light'] .zc-wbe-preview :deep(.hljs-type),
.zc-wbe[data-theme='light'] .zc-wbe-preview :deep(.hljs-tag) {
  color: #cf222e;
}
.zc-wbe[data-theme='light'] .zc-wbe-preview :deep(.hljs-string),
.zc-wbe[data-theme='light'] .zc-wbe-preview :deep(.hljs-regexp),
.zc-wbe[data-theme='light'] .zc-wbe-preview :deep(.hljs-attr) {
  color: #0a3069;
}
.zc-wbe[data-theme='light'] .zc-wbe-preview :deep(.hljs-number),
.zc-wbe[data-theme='light'] .zc-wbe-preview :deep(.hljs-literal) {
  color: #0550ae;
}
.zc-wbe[data-theme='light'] .zc-wbe-preview :deep(.hljs-comment) {
  color: #6e7781;
  font-style: italic;
}
.zc-wbe[data-theme='light'] .zc-wbe-preview :deep(.hljs-title),
.zc-wbe[data-theme='light'] .zc-wbe-preview :deep(.hljs-function) {
  color: #8250df;
}
.zc-wbe[data-theme='light'] .zc-wbe-preview :deep(.hljs-variable),
.zc-wbe[data-theme='light'] .zc-wbe-preview :deep(.hljs-name),
.zc-wbe[data-theme='light'] .zc-wbe-preview :deep(.hljs-template-variable) {
  color: #953800;
}
.zc-wbe[data-theme='light'] .zc-wbe-preview :deep(.hljs-meta),
.zc-wbe[data-theme='light'] .zc-wbe-preview :deep(.hljs-selector-tag) {
  color: #6639ba;
}
</style>
