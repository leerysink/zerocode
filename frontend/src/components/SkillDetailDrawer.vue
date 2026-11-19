<script setup>
// 技能详情/预览抽屉（ADR-0061 / ticket 001/002/004）：只读浏览母本文件树+内容。
// 纯展示组件：文件清单与内容加载器由父组件注入（详情按 skillId 走 API，预览按 token 走快照），
// 同一壳复用两种来源。markdown 复用 MarkdownView，图片走 rawUrl，二进制仅元数据。无写入口。
import { computed, ref, watch } from 'vue'
import MarkdownView from './MarkdownView.vue'

const props = defineProps({
  open: { type: Boolean, default: false },
  title: { type: String, default: '技能详情' },
  files: { type: Array, default: () => [] },
  skillMd: { type: String, default: null },
  loadContent: { type: Function, required: true },
  rawUrl: { type: Function, required: true },
  confirmable: { type: Boolean, default: false },
  confirming: { type: Boolean, default: false },
})
const emit = defineEmits(['close', 'confirm'])

const selected = ref(null)
const content = ref('')
const kind = ref('')        // md | text | image | binary
const loading = ref(false)
const error = ref('')

const selectedFile = computed(() => props.files.find((f) => f.path === selected.value) || null)

function fmtSize(n) {
  if (typeof n !== 'number') return ''
  return n < 1024 ? `${n} B` : `${(n / 1024).toFixed(1)} KB`
}

async function selectFile(f) {
  selected.value = f.path
  error.value = ''
  content.value = ''
  loading.value = false
  if (f.kind === 'image') {
    kind.value = 'image'
    content.value = props.rawUrl(f.path)
    return
  }
  if (f.kind === 'binary') {
    kind.value = 'binary'
    return
  }
  kind.value = /\.md$/i.test(f.path) ? 'md' : 'text'
  loading.value = true
  try {
    const text = await props.loadContent(f.path)
    if (selected.value === f.path) content.value = text   // 防并发选择：旧加载不覆盖新选中
  } catch (e) {
    if (selected.value === f.path) error.value = e?.response?.data?.detail || '内容加载失败'
  } finally {
    if (selected.value === f.path) loading.value = false
  }
}

watch(() => props.open, (v) => {
  if (!v) return
  selected.value = null
  content.value = ''
  kind.value = ''
  error.value = ''
  const md = props.files.find((f) => /(^|\/)SKILL\.md$/i.test(f.path))
  if (md) {
    if (props.skillMd != null && md.path === 'SKILL.md') {
      selected.value = md.path
      kind.value = 'md'
      content.value = props.skillMd
    } else {
      selectFile(md)
    }
  }
}, { immediate: true })
</script>

<template>
  <a-drawer :open="open" :width="760" :title="title" placement="right" @close="emit('close')">
    <div class="zc-skill-detail">
      <div class="zc-sd-tree">
        <div
          v-for="f in files" :key="f.path"
          class="zc-sd-file" :class="{ 'is-active': selected === f.path }"
          :data-test="`sd-file-${f.path}`"
          @click="selectFile(f)"
        >
          <span class="zc-sd-path">{{ f.path }}</span>
          <span class="zc-sd-meta">{{ fmtSize(f.size) }} · {{ f.kind }}</span>
        </div>
        <div v-if="!files.length" class="zc-sd-empty">无文件</div>
      </div>
      <div class="zc-sd-content">
        <div v-if="!selected" class="zc-sd-placeholder">选择左侧文件查看内容</div>
        <div v-else-if="loading" class="zc-sd-placeholder">加载中…</div>
        <div v-else-if="error" class="zc-sd-error" data-test="sd-error">{{ error }}</div>
        <img v-else-if="kind === 'image'" :src="content" class="zc-sd-img" data-test="sd-image" :alt="selected" />
        <div v-else-if="kind === 'binary'" class="zc-sd-placeholder" data-test="sd-binary">
          二进制文件，仅展示元数据（{{ fmtSize(selectedFile?.size) }}）
        </div>
        <MarkdownView v-else-if="kind === 'md'" :content="content" data-test="sd-md" />
        <pre v-else class="zc-sd-pre" data-test="sd-text">{{ content }}</pre>
      </div>
    </div>
    <template #footer>
      <div class="zc-sd-footer">
        <template v-if="confirmable">
          <a-button type="primary" :loading="confirming" data-test="sd-confirm"
                    @click="emit('confirm')">确认安装</a-button>
          <a-button @click="emit('close')">取消</a-button>
        </template>
        <a-button v-else @click="emit('close')">关闭</a-button>
      </div>
    </template>
  </a-drawer>
</template>

<style scoped>
.zc-skill-detail { display: flex; gap: 12px; height: 100%; }
.zc-sd-tree { width: 240px; flex-shrink: 0; overflow: auto; border-right: 1px solid var(--zc-border); padding-right: 8px; }
.zc-sd-file { padding: 6px 8px; border-radius: 6px; cursor: pointer; }
.zc-sd-file:hover { background: rgba(91, 122, 157, 0.06); }
.zc-sd-file.is-active { background: rgba(91, 122, 157, 0.12); }
.zc-sd-path { display: block; font-size: 13px; font-family: ui-monospace, Consolas, Menlo, monospace; word-break: break-all; }
.zc-sd-meta { display: block; font-size: 11px; color: var(--zc-text-sub); }
.zc-sd-content { flex: 1; overflow: auto; min-width: 0; }
.zc-sd-placeholder { color: var(--zc-text-sub); font-size: 13px; padding: 12px; }
.zc-sd-error { color: #a05a5a; font-size: 13px; padding: 12px; }
.zc-sd-pre { font-size: 12px; line-height: 1.6; white-space: pre-wrap; word-break: break-word; margin: 0; }
.zc-sd-img { max-width: 100%; }
.zc-sd-empty { color: var(--zc-text-sub); font-size: 13px; padding: 8px; }
.zc-sd-footer { display: flex; gap: 8px; justify-content: flex-end; }
</style>