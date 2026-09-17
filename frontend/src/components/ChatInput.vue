<script setup>
// 输入区：顶部小条（当前工作区/模型，只读，取自 store）+ a-textarea（Enter 发送、Shift+Enter 换行）
// + 底部操作行。running 中「发送」替换为「停止」（cancel）；中文输入法组词回车不触发发送。
import { computed, onMounted, ref } from 'vue'
import { FolderOutlined, SendOutlined, StopOutlined } from '@ant-design/icons-vue'
import { useChatStore } from '../stores/chat'
import { useConfigStore } from '../stores/config'

const props = defineProps({ running: { type: Boolean, default: false } })
const emit = defineEmits(['send', 'cancel'])

const chatStore = useChatStore()
const configStore = useConfigStore()
const text = ref('')

const wsName = computed(
  () => chatStore.workspaces.find((w) => w.id === chatStore.currentWorkspaceId)?.name || ''
)
const model = computed(() => configStore.form.model || '')

// 模型名惰性加载：设置页未打开过时此处兜底拉一次
onMounted(() => {
  if (!configStore.form.model) configStore.loadConfig()
})

function doSend() {
  const content = text.value.trim()
  if (!content || props.running) return // running 期间只允许停止，避免消息交错
  emit('send', content)
  text.value = ''
}

function onKeydown(e) {
  if (e.key !== 'Enter') return
  if (e.shiftKey) return // Shift+Enter 换行，走默认行为
  if (e.isComposing || e.keyCode === 229) return // 中文输入法组词回车不发送
  e.preventDefault()
  doSend()
}
</script>

<template>
  <div class="zc-input">
    <div v-if="wsName || model" class="zc-input-meta">
      <span v-if="wsName" class="zc-meta-item"><folder-outlined /> {{ wsName }}</span>
      <span v-if="model" class="zc-meta-item">模型：{{ model }}</span>
    </div>
    <a-textarea
      v-model:value="text"
      :auto-size="{ minRows: 2, maxRows: 6 }"
      placeholder="输入消息，Enter 发送，Shift+Enter 换行"
      @keydown="onKeydown"
    />
    <div class="zc-input-actions">
      <span class="zc-input-hint">Enter 发送 / Shift+Enter 换行</span>
      <a-button v-if="running" danger @click="emit('cancel')">
        <template #icon><stop-outlined /></template>
        停止
      </a-button>
      <a-button v-else type="primary" :disabled="!text.trim()" @click="doSend">
        <template #icon><send-outlined /></template>
        发送
      </a-button>
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
</style>
