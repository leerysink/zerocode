<script setup>
// 输入区：工作区/模型元信息条 + a-textarea（Enter 发送）+ 操作行（压缩/发送|停止）。
// @ 触发文件补全（/api/fs/tree 展平过滤 top8，Enter/点击选中）；模型下拉按会话记忆。
// 发送决策（能不能发、被拦时留不留文本）全在 chat store.send 里，本组件只负责
// 「成功才清空输入框」+「busy 回填草稿」+「把 hint 摊在操作行上」，避免执行中静默吞输入。
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import {
  FolderOutlined, PaperClipOutlined, ScissorOutlined, SendOutlined, StopOutlined,
} from '@ant-design/icons-vue'
import { useChatStore } from '../stores/chat'
import { tree } from '../api'
import ChatAppActions from './ChatAppActions.vue'
import { activeMentionToken, applyMention } from '../utils/mentions'
import { flattenTree } from '../utils/tree'
import { useImageAttachments } from '../composables/useImageAttachments'
import { MAX_ATTACHMENTS, formatBytes } from '../utils/imageAttachments'

// running 只驱动「停止/发送」切换与占位文案；能不能发由 store.send 判定（并回 hint/草稿）
defineProps({ running: { type: Boolean, default: false } })
const emit = defineEmits(['cancel'])

const chatStore = useChatStore()
const text = ref('')
const textareaRef = ref(null)

// ---- 图片附件：粘贴 / 拖拽 / 文件选择三入口共用同一条 校验→压缩→上传 队列 ----
const fileInputRef = ref(null)
const dragOver = ref(false)
const {
  items: attachmentItems, error: attachmentError, count: attachmentCount,
  uploading: attachmentsUploading, hasFailed: attachmentsFailed,
  addFiles, remove: removeAttachment, retry: retryAttachment,
  clear: clearAttachments, readyItems, mergeRejected,
} = useImageAttachments(() => chatStore.currentSessionId)

const visionBlocked = computed(
  () => attachmentCount.value > 0 && !chatStore.visionSupported)
const canSend = computed(() => {
  const ready = readyItems().length
  return (!!text.value.trim() || ready > 0)
    && !attachmentsUploading.value
    && !attachmentsFailed.value
    && !chatStore.compacting
    && (ready === 0 || chatStore.visionSupported)
})
const hintText = computed(() => {
  if (chatStore.hint) return chatStore.hint
  if (attachmentError.value) return attachmentError.value
  if (attachmentsUploading.value) return '图片上传中…'
  return 'Enter 发送 / Shift+Enter 换行'
})

function onPaste(e) {
  const files = Array.from(e.clipboardData?.files || [])
  if (!files.length) return
  const hasText = Array.from(e.clipboardData?.types || []).includes('text/plain')
  if (!hasText) e.preventDefault()
  addFiles(files)
}
function onDrop(e) {
  dragOver.value = false
  addFiles(Array.from(e.dataTransfer?.files || []))
}
function openFilePicker() {
  fileInputRef.value?.click()
}
function onFileChange(e) {
  addFiles(Array.from(e.target.files || []))
  e.target.value = ''
}

// busy 交还的附件与 rejectedDraft 配套：并回附件条，用户改完文字可原样再发
watch(
  () => chatStore.rejectedAttachments,
  (list) => {
    if (!list || !list.length) return
    mergeRejected(list)
    chatStore.rejectedAttachments = []
  }
)
// 切会话：附件属于上一会话的上传结果，不能带进新会话；文字草稿保留
watch(
  () => chatStore.currentSessionId,
  (id, old) => { if (id !== old) clearAttachments() }
)
// 离开聊天页时回收 blob 预览地址，避免预览 URL 泄漏
onUnmounted(clearAttachments)

// busy（后端拒收）或被拦的一句话：文本交还给输入框，用户改改就能再发，不必重打
watch(
  () => chatStore.rejectedDraft,
  (draft) => {
    if (!draft) return
    if (!text.value.trim()) text.value = draft
    chatStore.rejectedDraft = ''
  }
)

const wsName = computed(
  () => chatStore.workspaces.find((w) => w.id === chatStore.currentWorkspaceId)?.name || ''
)
const currentModelLabel = computed(() => {
  if (chatStore.currentModelId == null) return '默认模型'
  const m = chatStore.models.find((x) => x.id === chatStore.currentModelId)
  return m ? m.name : '默认模型'
})

// ---- @ 文件补全 ----
const files = ref([]) // [{path, name}]
const mention = ref(null) // {query, start, end} | null
const mentionIndex = ref(0)
const mentionItems = computed(() => {
  if (!mention.value) return []
  const q = mention.value.query.toLowerCase()
  return files.value.filter((f) => f.path.toLowerCase().includes(q)).slice(0, 8)
})

async function loadFiles() {
  if (!chatStore.currentWorkspaceId) return
  try {
    const { data } = await tree(chatStore.currentWorkspaceId)
    files.value = flattenTree(data)
  } catch (e) { /* 树加载失败不阻塞输入 */ }
}
onMounted(() => {
  loadFiles()
  if (!chatStore.models.length) chatStore.loadModels()
})

function onInput() {
  const el = textareaRef.value?.resizableTextArea?.textArea
  const caret = el ? el.selectionStart : text.value.length
  mention.value = activeMentionToken(text.value, caret)
  mentionIndex.value = 0
}

function pickMention(item) {
  const t = mention.value
  text.value = applyMention(text.value, t, item.path)
  mention.value = null
}

function onKeydown(e) {
  if (mention.value && mentionItems.value.length) {
    if (e.key === 'ArrowDown') { e.preventDefault(); mentionIndex.value = (mentionIndex.value + 1) % mentionItems.value.length; return }
    if (e.key === 'ArrowUp') { e.preventDefault(); mentionIndex.value = (mentionIndex.value - 1 + mentionItems.value.length) % mentionItems.value.length; return }
    if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); pickMention(mentionItems.value[mentionIndex.value]); return }
    if (e.key === 'Escape') { mention.value = null; return }
  }
  if (e.key !== 'Enter') return
  if (e.shiftKey) return
  if (e.isComposing || e.keyCode === 229) return
  e.preventDefault()
  doSend()
}

function doSend() {
  if (!canSend.value) return
  const content = text.value.trim()
  mention.value = null
  // 只有后端真收下才清空输入框与附件；被拦（执行中/未就绪）时原样留着，store 已备好 hint
  if (chatStore.send({ text: content, attachments: readyItems() })) {
    text.value = ''
    clearAttachments()
  }
}

async function onModelChange(id) {
  try {
    await chatStore.setModel(id === 0 ? null : id) // 0 = 默认模型哨兵
  } catch (e) { /* 失败保持原值 */ }
}

function requestCompact() {
  chatStore.requestCompact()
}
</script>

<template>
  <div
    class="zc-input"
    :class="{ 'is-dragover': dragOver }"
    @paste="onPaste"
    @dragover.prevent="dragOver = true"
    @dragleave="dragOver = false"
    @drop.prevent="onDrop"
  >
    <div v-if="wsName || chatStore.models.length" class="zc-input-meta">
      <span v-if="wsName" class="zc-meta-item"><folder-outlined /> {{ wsName }}</span>
      <a-select
        v-if="chatStore.models.length"
        :value="chatStore.currentModelId ?? 0"
        size="small"
        class="zc-model-select"
        @change="onModelChange"
      >
        <a-select-option :value="0">默认模型</a-select-option>
        <a-select-option v-for="m in chatStore.models" :key="m.id" :value="m.id">
          {{ m.name }}（{{ m.model }}）{{ m.supports_vision ? ' · 图片' : '' }}
        </a-select-option>
      </a-select>
    </div>
    <div v-if="attachmentCount" class="zc-attachments">
      <div v-if="visionBlocked" class="zc-attach-warn">
        当前模型不支持图片输入，请切换支持视觉的模型
      </div>
      <div class="zc-attach-list">
        <div v-for="it in attachmentItems" :key="it.key" class="zc-attach-item" :class="`is-${it.status}`">
          <img class="zc-attach-thumb" :src="it.previewUrl" :alt="it.filename" />
          <a-spin v-if="it.status === 'compressing' || it.status === 'uploading'" size="small" class="zc-attach-spin" />
          <div v-else-if="it.status === 'failed'" class="zc-attach-fail">
            <a-button type="link" size="small" @click="retryAttachment(it.key)">重试</a-button>
            <a-button type="link" size="small" danger @click="removeAttachment(it.key)">移除</a-button>
          </div>
          <a-button v-else type="text" size="small" class="zc-attach-remove" @click="removeAttachment(it.key)">×</a-button>
          <span v-if="it.size_bytes" class="zc-attach-size">{{ formatBytes(it.size_bytes) }}</span>
        </div>
      </div>
      <span class="zc-attach-count">{{ attachmentCount }}/{{ MAX_ATTACHMENTS }}</span>
    </div>
    <div class="zc-input-wrap">
      <a-textarea
        ref="textareaRef"
        v-model:value="text"
        :auto-size="{ minRows: 2, maxRows: 6 }"
        :placeholder="running ? '执行中发的消息会作为插话，在当前这步做完后生效（Enter 发送）' : '输入消息，@ 引用文件，Enter 发送，Shift+Enter 换行'"
        @keydown="onKeydown"
        @input="onInput"
      />
      <div v-if="mention && mentionItems.length" class="zc-mention">
        <div
          v-for="(f, i) in mentionItems"
          :key="f.path"
          class="zc-mention-item"
          :class="{ 'is-active': i === mentionIndex }"
          @mousedown.prevent="pickMention(f)"
        >
          {{ f.path }}
        </div>
      </div>
    </div>
    <div class="zc-input-actions">
      <span class="zc-input-hint" :class="{ 'is-busy': !!chatStore.hint }">
        {{ hintText }}
      </span>
      <div class="zc-input-buttons">
        <a-button
          size="small"
          class="zc-attach-btn"
          :disabled="attachmentCount >= MAX_ATTACHMENTS"
          @click="openFilePicker"
        >
          <template #icon><paper-clip-outlined /></template>
        </a-button>
        <input
          ref="fileInputRef"
          type="file"
          accept="image/png,image/jpeg,image/webp,image/gif"
          multiple
          class="zc-file-input"
          @change="onFileChange"
        />
        <chat-app-actions />
        <a-button
          size="small"
          :disabled="running || chatStore.compacting"
          :loading="chatStore.compacting"
          @click="requestCompact"
        >
          <template #icon><scissor-outlined /></template>
          压缩上下文
        </a-button>
        <a-button
          class="zc-send-btn"
          type="primary"
          :disabled="!canSend"
          @click="doSend"
        >
          <template #icon><send-outlined /></template>
          <!-- 执行中这条就是插话入口，别只留一个 Enter 的隐性出路 -->
          {{ running ? '插话' : '发送' }}
        </a-button>
        <a-button v-if="running" danger @click="emit('cancel')">
          <template #icon><stop-outlined /></template>
          停止
        </a-button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.zc-input {
  flex: 0 0 auto;
  padding: 10px 28px 14px;
  border-top: 1px solid var(--zc-border);
  background: #fff;
}
.zc-input-meta {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 8px;
  font-size: 12px;
  color: var(--zc-text-sub);
}
.zc-meta-item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  max-width: 320px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.zc-input-wrap {
  position: relative;
}
.zc-mention {
  position: absolute;
  left: 0;
  right: 0;
  bottom: calc(100% + 4px);
  background: #fff;
  border: 1px solid var(--zc-border);
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(47, 58, 71, 0.12);
  overflow: hidden;
  z-index: 20;
}
.zc-mention-item {
  padding: 6px 12px;
  font-size: 12.5px;
  cursor: pointer;
}
.zc-mention-item.is-active {
  background: rgba(91, 122, 157, 0.1);
}
.zc-model-select {
  min-width: 180px;
}
.zc-input-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  /* 窄窗口下让提示自己占一行，而不是被按钮组压成三四行（按钮组是这一行的主角） */
  flex-wrap: wrap;
  margin-top: 8px;
}
.zc-input-hint {
  min-width: 0;
  font-size: 12px;
  color: var(--zc-text-sub);
}
/* 执行中被拦下的一次性提示：低饱和暖色，只点状态，不抢错误级的红 */
.zc-input-hint.is-busy {
  color: #a17c5b;
}
.zc-input-buttons {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
  column-gap: 8px;
  row-gap: 6px;
}
.zc-file-input { display: none; }
.zc-input.is-dragover { outline: 2px dashed var(--zc-primary); outline-offset: -4px; }
.zc-attachments { margin-bottom: 8px; }
.zc-attach-warn {
  margin-bottom: 6px; font-size: 12px; color: #a17c5b;
}
.zc-attach-list { display: flex; flex-wrap: wrap; gap: 8px; }
.zc-attach-item {
  position: relative; width: 56px; height: 56px; border-radius: 6px;
  overflow: hidden; background: #f4f6f9;
}
.zc-attach-thumb { width: 56px; height: 56px; object-fit: cover; display: block; }
.zc-attach-spin { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; background: rgba(255,255,255,.6); }
.zc-attach-remove { position: absolute; top: 0; right: 0; min-width: 18px; height: 18px; padding: 0; line-height: 18px; }
.zc-attach-fail {
  position: absolute; inset: 0; display: flex; flex-direction: column;
  align-items: center; justify-content: center; background: rgba(253,238,238,.92);
}
.zc-attach-size {
  position: absolute; left: 2px; bottom: 2px; font-size: 10px;
  color: #fff; background: rgba(0,0,0,.45); border-radius: 3px; padding: 0 3px;
}
.zc-attach-count { font-size: 12px; color: var(--zc-text-sub); margin-left: 4px; }
</style>
