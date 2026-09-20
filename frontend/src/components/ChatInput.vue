<script setup>
// 输入区：工作区/模型元信息条 + a-textarea（Enter 发送）+ 操作行（压缩/发送|停止）。
// @ 触发文件补全（/api/fs/tree 展平过滤 top8，Enter/点击选中）；模型下拉按会话记忆。
// 发送决策（能不能发、被拦时留不留文本）全在 chat store.send 里，本组件只负责
// 「成功才清空输入框」+「busy 回填草稿」+「把 hint 摊在操作行上」，避免执行中静默吞输入。
import { computed, onMounted, ref, watch } from 'vue'
import { FolderOutlined, ScissorOutlined, SendOutlined, StopOutlined } from '@ant-design/icons-vue'
import { useChatStore } from '../stores/chat'
import { tree } from '../api'
import { activeMentionToken, applyMention } from '../utils/mentions'
import { flattenTree } from '../utils/tree'

// running 只驱动「停止/发送」切换与占位文案；能不能发由 store.send 判定（并回 hint/草稿）
defineProps({ running: { type: Boolean, default: false } })
const emit = defineEmits(['cancel'])

const chatStore = useChatStore()
const text = ref('')
const textareaRef = ref(null)

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
  const content = text.value.trim()
  if (!content) return
  mention.value = null
  // 只有后端真收下才清空输入框；被拦（执行中/未就绪）时文本原样留着，store 已备好 hint
  if (chatStore.send(content)) text.value = ''
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
  <div class="zc-input">
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
          {{ m.name }}（{{ m.model }}）
        </a-select-option>
      </a-select>
    </div>
    <div class="zc-input-wrap">
      <a-textarea
        ref="textareaRef"
        v-model:value="text"
        :auto-size="{ minRows: 2, maxRows: 6 }"
        :placeholder="running ? '会话执行中：可点右侧「停止」结束当前执行，或等它跑完再发言' : '输入消息，@ 引用文件，Enter 发送，Shift+Enter 换行'"
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
        {{ chatStore.hint || 'Enter 发送 / Shift+Enter 换行' }}
      </span>
      <div class="zc-input-buttons">
        <a-button
          size="small"
          :disabled="running || chatStore.compacting"
          :loading="chatStore.compacting"
          @click="requestCompact"
        >
          <template #icon><scissor-outlined /></template>
          压缩上下文
        </a-button>
        <a-button v-if="running" danger @click="emit('cancel')">
          <template #icon><stop-outlined /></template>
          停止
        </a-button>
        <a-button v-else type="primary" :disabled="!text.trim() || chatStore.compacting" @click="doSend">
          <template #icon><send-outlined /></template>
          发送
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
  margin-top: 8px;
}
.zc-input-hint {
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
  gap: 8px;
}
</style>
