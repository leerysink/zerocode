<script setup>
// 新会话弹窗：选择已有工作区，或输入/浏览新目录路径 → createWorkspace(如新) + createSession → openSession。
// 目录已存在时后端返回 409「该目录已存在」：重拉工作区列表按 root_path 匹配复用，绝不重复建工作区。
import { computed, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { createSession, createWorkspace } from '../api'
import { useChatStore } from '../stores/chat'
import DirBrowseModal from './DirBrowseModal.vue'

const props = defineProps({ open: Boolean })
const emit = defineEmits(['update:open', 'created'])

const store = useChatStore()
const wsId = ref(null) // 选中的已有工作区 id
const path = ref('') // 新工作区目录路径（与 wsId 二选一）
const submitting = ref(false)
const showBrowse = ref(false)

const wsOptions = computed(() => store.workspaces.map((w) => ({ label: w.name, value: w.id })))

// 每次打开重置表单
watch(
  () => props.open,
  (v) => {
    if (v) {
      wsId.value = null
      path.value = ''
      showBrowse.value = false // 防御：重开时内层目录弹窗不残留
    }
  }
)

// 两栏互斥：选了已有工作区就清路径，填了路径就清选择
function onWsChange(v) {
  if (v) path.value = ''
}
function onPathInput() {
  if (path.value) wsId.value = null
}
function onBrowseSelect(p) {
  path.value = p
  wsId.value = null
}

function close() {
  emit('update:open', false)
}

async function submit() {
  if (!wsId.value && !path.value.trim()) {
    message.warning('请选择已有工作区或填写目录路径')
    return
  }
  submitting.value = true
  try {
    let ws = null
    if (wsId.value) {
      ws = store.workspaces.find((w) => w.id === wsId.value)
    } else {
      const p = path.value.trim()
      ws = store.workspaces.find((w) => w.root_path === p)
      if (!ws) {
        try {
          ws = (await createWorkspace({ root_path: p })).data
        } catch (e) {
          if (e?.response?.status !== 409) throw e
          // 409：该目录已存在（后端对 root_path 规范化后判重）——重拉列表按路径匹配复用
          await store.loadWorkspaces()
          ws = store.workspaces.find((w) => w.root_path === p)
          if (!ws) {
            message.error('该目录已存在，请在「已有工作区」中选择')
            return
          }
        }
      }
    }
    const { data: session } = await createSession({ workspace_id: ws.id })
    await store.openSession(session.id)
    message.success('会话已创建')
    emit('created', session)
    close()
  } catch (e) {
    message.error(e?.response?.data?.detail || '创建失败')
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <a-modal
    :open="open"
    title="新会话"
    width="40%"
    :confirm-loading="submitting"
    ok-text="创建"
    cancel-text="取消"
    @ok="submit"
    @cancel="close"
  >
    <div class="zc-ns-field">
      <div class="zc-ns-label">已有工作区</div>
      <a-select
        v-model:value="wsId"
        :options="wsOptions"
        placeholder="选择已有工作区"
        allow-clear
        show-search
        option-filter-prop="label"
        class="zc-ns-select"
        @change="onWsChange"
      />
    </div>
    <a-divider plain>或新建工作区</a-divider>
    <div class="zc-ns-field">
      <div class="zc-ns-label">目录路径</div>
      <div class="zc-ns-row">
        <a-input
          v-model:value="path"
          placeholder="输入工作区目录，如 D:\work\demo"
          allow-clear
          @update:value="onPathInput"
        />
        <a-button @click="showBrowse = true">浏览目录</a-button>
      </div>
    </div>
    <DirBrowseModal v-model:open="showBrowse" @select="onBrowseSelect" />
  </a-modal>
</template>

<style scoped>
.zc-ns-field {
  margin-bottom: 8px;
}
.zc-ns-label {
  margin-bottom: 4px;
  color: var(--zc-text-sub);
  font-size: 12px;
}
.zc-ns-select {
  width: 100%;
}
.zc-ns-row {
  display: flex;
  gap: 8px;
}
</style>
