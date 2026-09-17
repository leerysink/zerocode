<script setup>
// 左栏：工作区分组 + 会话列表。进入 ChatView 时拉取工作区列表并逐组拉会话填 store.sessionsByWs
// （Task 11 只声明了该 state 未写入，本组件负责填充，不改 store 语义）。
// 组头点击折叠/展开成员（默认展开）；成员默认显 5 条，「展开其余 N 个会话」放开全部；hover 出删除。
import { onMounted, reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import { DeleteOutlined, FolderOutlined, SettingOutlined } from '@ant-design/icons-vue'
import { deleteSession, listSessions } from '../api'
import { useChatStore } from '../stores/chat'
import { relativeTime } from '../utils/time'
import NewSessionModal from './NewSessionModal.vue'

const store = useChatStore()
const showNew = ref(false)
const loading = ref(true)
const expand = reactive({}) // wsId -> 已展开全部（默认 false = 每组显 5 条）
const folded = reactive({}) // wsId -> 组已折叠（默认展开）

const VISIBLE = 5 // brief：默认每组显 5 个

function visibleSessions(wsId) {
  const list = store.sessionsByWs[wsId] || []
  return expand[wsId] ? list : list.slice(0, VISIBLE)
}
function hasMore(wsId) {
  return !expand[wsId] && (store.sessionsByWs[wsId] || []).length > VISIBLE
}
function moreCount(wsId) {
  return (store.sessionsByWs[wsId] || []).length - VISIBLE
}
function isOpen(wsId) {
  return folded[wsId] !== true
}
// 组头点击：折叠/展开组成员
function toggle(wsId) {
  folded[wsId] = !isOpen(wsId)
}

// 拉取工作区 + 逐组拉会话填 sessionsByWs
async function loadAll() {
  loading.value = true
  try {
    await store.loadWorkspaces()
    await Promise.all(
      store.workspaces.map(async (ws) => {
        store.sessionsByWs[ws.id] = (await listSessions(ws.id)).data
      })
    )
  } finally {
    loading.value = false
  }
}
onMounted(loadAll)

async function removeSession(wsId, s) {
  try {
    await deleteSession(s.id)
    store.sessionsByWs[wsId] = (store.sessionsByWs[wsId] || []).filter((x) => x.id !== s.id)
    if (store.currentSessionId === s.id) store.currentSessionId = null
    message.success('会话已删除')
  } catch (e) {
    message.error(e?.response?.data?.detail || '删除失败')
  }
}
</script>

<template>
  <aside class="zc-sidebar">
    <div class="zc-logo">zerocode</div>
    <a-button type="primary" block @click="showNew = true">＋ 新会话</a-button>
    <div class="zc-groups">
      <a-spin v-if="loading" class="zc-loading" />
      <a-empty v-else-if="!store.workspaces.length" description="暂无工作区，点击「新会话」创建" />
      <div v-for="ws in store.workspaces" :key="ws.id" class="zc-group">
        <div class="zc-group-head" @click="toggle(ws.id)">
          <folder-outlined />
          <span class="zc-ws-name">{{ ws.name }}</span>
          <span class="zc-count">{{ (store.sessionsByWs[ws.id] || []).length }}</span>
        </div>
        <div v-show="isOpen(ws.id)">
          <div v-if="!(store.sessionsByWs[ws.id] || []).length" class="zc-none">暂无会话</div>
          <template v-for="s in visibleSessions(ws.id)" :key="s.id">
            <div
              class="zc-session"
              :class="{ active: s.id === store.currentSessionId }"
              @click="store.openSession(s.id)"
            >
              <span class="zc-title">{{ s.title }}</span>
              <span class="zc-time">{{ relativeTime(s.updated_at) }}</span>
              <a-popconfirm title="删除该会话？" @confirm="removeSession(ws.id, s)">
                <delete-outlined class="zc-del" @click.stop />
              </a-popconfirm>
            </div>
          </template>
          <a v-if="hasMore(ws.id)" class="zc-more" @click="expand[ws.id] = true">
            展开其余 {{ moreCount(ws.id) }} 个会话
          </a>
        </div>
      </div>
    </div>
    <div class="zc-footer">
      <router-link to="/settings"><setting-outlined /> 设置</router-link>
    </div>
    <NewSessionModal v-model:open="showNew" @created="loadAll" />
  </aside>
</template>

<style scoped>
.zc-sidebar {
  flex: 0 0 260px;
  width: 260px;
  display: flex;
  flex-direction: column;
  background: var(--zc-sidebar-bg);
  border-right: 1px solid var(--zc-border);
  padding: 12px;
  box-sizing: border-box;
}
.zc-logo {
  color: var(--zc-primary);
  font-size: 18px;
  font-weight: 600;
  letter-spacing: 1px;
  padding: 2px 4px 10px;
}
.zc-groups {
  flex: 1;
  overflow-y: auto;
  margin-top: 12px;
  min-height: 0;
}
.zc-loading {
  display: block;
  margin: 24px auto;
}
.zc-group {
  margin-bottom: 10px;
}
.zc-group-head {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 8px;
  border-radius: 4px;
  cursor: pointer;
  font-weight: 500;
  color: var(--zc-text);
}
.zc-group-head:hover {
  background: var(--zc-bg);
}
.zc-ws-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.zc-count {
  color: var(--zc-text-sub);
  font-size: 12px;
  flex-shrink: 0;
}
.zc-session {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 8px 6px 24px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 13px;
  color: var(--zc-text);
}
.zc-session:hover {
  background: var(--zc-bg);
}
.zc-session.active {
  background: rgba(91, 122, 157, 0.12);
  color: var(--zc-primary);
}
.zc-title {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.zc-time {
  color: var(--zc-text-sub);
  font-size: 12px;
  flex-shrink: 0;
}
.zc-del {
  flex-shrink: 0;
  opacity: 0;
  transition: opacity 0.15s;
  color: var(--zc-text-sub);
}
.zc-del:hover {
  color: #b96a6a;
}
.zc-session:hover .zc-del {
  opacity: 1;
}
.zc-more {
  display: block;
  padding: 4px 8px 0 24px;
  font-size: 12px;
  color: var(--zc-primary);
  cursor: pointer;
}
.zc-none {
  padding: 2px 8px 2px 24px;
  font-size: 12px;
  color: var(--zc-text-sub);
}
.zc-footer {
  border-top: 1px solid var(--zc-border);
  padding-top: 10px;
}
.zc-footer a {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--zc-text-sub);
}
.zc-footer a:hover {
  color: var(--zc-primary);
}
</style>
