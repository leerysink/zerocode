<script setup>
// 左栏：应用分组 + 会话列表。2026-09-20 起「文件夹」即应用——分组行由 GET /apps 驱动
// （一行 = 一个应用），手动挂目录的旧工作区不再作为入口出现（记录仍在 workspace 表里，只是不展示）。
// 分组行最右侧两个入口：`+` = 在该应用下直接建会话并打开（无弹窗）；`⋯` = 重命名 / 删除应用。
// 删除按规格 08 FR8-08 走「输入 slug 才能确认」；M1 只删平台记录，工程目录与数据库保留并在文案里说明。
// 组头左侧箭头示折叠态，点击折叠/展开成员（默认展开）；按住组头上下拖可改文件夹顺序——
// 折叠态与顺序都只记在本机 localStorage（见 useSidebarFolders），换浏览器回到服务端默认序。
// 成员默认显 5 条，「展开其余 N 个会话」放开全部；hover 出删除。
import { computed, onMounted, reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import {
  AppstoreOutlined, CheckCircleFilled, ClockCircleOutlined, CloseCircleFilled,
  DeleteOutlined, EllipsisOutlined, FolderOutlined, LoadingOutlined, PlusOutlined,
  RightOutlined, SettingOutlined, ShopOutlined,
} from '@ant-design/icons-vue'
import { createSession, deleteApp, deleteSession, listApps, listSessions, renameApp } from '../api'
import { useChatStore } from '../stores/chat'
import { useSessionLiveStatus } from '../composables/useSessionLiveStatus'
import { useSidebarFolders } from '../composables/useSidebarFolders'
import { relativeTime } from '../utils/time'
import NewSessionModal from './NewSessionModal.vue'

const props = defineProps({
  width: { type: Number, default: 260 },
  collapsed: { type: Boolean, default: false },
})
const panelStyle = computed(() => ({ width: props.collapsed ? '0px' : `${props.width}px` }))

const store = useChatStore()

// ---- 会话状态角标（spec 2026-11-04）----
// 所有会话都显示（含当前会话）；结束态只提醒「本页打开之后才结束」的那一批，
// 否则历史里大量 idle 会话会被误当成刚完成。消除只活在本次页面内，不落库。
const { statuses } = useSessionLiveStatus()
const pageOpenedAt = Date.now()
const dismissed = reactive(new Set())

const BADGE_TEXT = {
  waiting: '等待你回答或确认',
  running: '运行中',
  completed: '已完成',
  failed: '已停止或失败',
}

function badgeOf(s) {
  const st = statuses.value[s.id]
  if (!st) return null
  // 挂起等人时 run 仍在跑，等待必须压过转圈，否则用户看不出是在等自己
  if (st.waiting === 'ask' || st.waiting === 'confirm') return 'waiting'
  if (st.running) return 'running'
  if (!st.last_outcome) return null
  if (!(st.finished_at * 1000 > pageOpenedAt)) return null
  if (dismissed.has(`${s.id}:${st.finished_at}`)) return null
  return st.last_outcome === 'completed' ? 'completed' : 'failed'
}

function onBadgeClick(s, wsId) {
  const state = badgeOf(s)
  if (state === 'completed' || state === 'failed') {
    dismissed.add(`${s.id}:${statuses.value[s.id].finished_at}`)
  }
  store.openSession(s.id, wsId)
}
const showNew = ref(false)
const loading = ref(true)
const apps = ref([])
const expand = reactive({}) // wsId -> 已展开全部（默认 false = 每组显 5 条）
const rename = reactive({ open: false, app: null, name: '' })
const del = reactive({ open: false, app: null, typed: '' })

const VISIBLE = 5 // brief：默认每组显 5 个

// 拖拽落点按组行的实际几何算；读不到 DOM 时给空数组，让 startDrag 直接放弃而不是猜位置
const groupsEl = ref(null)
function getGroupRects() {
  const root = groupsEl.value
  if (!root) return []
  return [...root.children]
    .filter((el) => el.classList.contains('zc-group'))
    .map((el) => {
      const box = el.getBoundingClientRect()
      return { top: box.top, height: box.height }
    })
}
const { applyOrder, syncOrder, setFolded, isFolded, startDrag, onHeadClick, draggingId } =
  useSidebarFolders({ getGroupRects })
const groups = computed(() => applyOrder(apps.value))

const sessionsOf = (wsId) => store.sessionsByWs[wsId] || []
function visibleSessions(wsId) {
  return expand[wsId] ? sessionsOf(wsId) : sessionsOf(wsId).slice(0, VISIBLE)
}
function hasMore(wsId) {
  return !expand[wsId] && sessionsOf(wsId).length > VISIBLE
}
function moreCount(wsId) {
  return sessionsOf(wsId).length - VISIBLE
}

// 拉应用 + 逐应用拉会话填 sessionsByWs。loadWorkspaces 仍要跑：文件面板面包屑与输入区
// 的工作区名回显读的是 store.workspaces（按 id 查，含旧工作区），改名后也要靠它刷新。
async function loadAll() {
  loading.value = true
  try {
    const [res] = await Promise.all([listApps(), store.loadWorkspaces()])
    apps.value = res.data
    syncOrder(res.data) // 补进新建的应用、剔掉已删的，存的顺序才跟真实行对得上
    await Promise.all(
      apps.value.map(async (a) => {
        store.sessionsByWs[a.workspace_id] = (await listSessions(a.workspace_id)).data
      })
    )
  } catch (e) {
    message.error(e?.response?.data?.detail || '加载失败')
  } finally {
    loading.value = false
  }
}
onMounted(loadAll)

async function removeSession(wsId, s) {
  try {
    await deleteSession(s.id)
    store.forgetSession(s.id) // 删掉的会话不再留在切回缓存里
    store.sessionsByWs[wsId] = sessionsOf(wsId).filter((x) => x.id !== s.id)
    if (store.currentSessionId === s.id) store.currentSessionId = null
    message.success('会话已删除')
  } catch (e) {
    message.error(e?.response?.data?.detail || '删除失败')
  }
}

// ---- 行内操作：+ 建会话 ----
async function addSession(app) {
  try {
    const { data: s } = await createSession({ workspace_id: app.workspace_id })
    store.sessionsByWs[app.workspace_id] = [s, ...sessionsOf(app.workspace_id)]
    setFolded(app.id, false) // 新会话必须看得见：顺手展开被折叠的组
    await store.openSession(s.id, app.workspace_id)
  } catch (e) {
    message.error(e?.response?.data?.detail || '新建会话失败')
  }
}

// ---- 行内操作：⋯ 重命名 / 删除 ----
function openRename(app) {
  rename.app = app
  rename.name = app.name
  rename.open = true
}
const renameOk = computed(() => !!rename.name.trim())
async function doRename() {
  if (!rename.app || !renameOk.value) return
  try {
    await renameApp(rename.app.id, rename.name.trim())
    message.success('已重命名')
    rename.open = false
    await loadAll() // app_project.name 与 workspace.name 同步变了，两侧名字都要重新拉
  } catch (e) {
    message.error(e?.response?.data?.detail || '重命名失败')
  }
}

function openDelete(app) {
  del.app = app
  del.typed = ''
  del.open = true
}
const deleteOk = computed(() => !!del.app && del.typed === del.app.slug)
async function doDelete() {
  if (!del.app || !deleteOk.value) return
  const app = del.app
  try {
    const { data } = await deleteApp(app.id)
    const kept = data?.kept
    message.success(kept?.root_path ? `应用已删除，工程目录保留在 ${kept.root_path}` : '应用已删除')
    del.open = false
    if (store.currentWorkspaceId === app.workspace_id) {
      store.currentSessionId = null // 正在聊的就是这个应用：右栏渲染门与消息流一起收口
      store.currentWorkspaceId = null
    }
    delete store.sessionsByWs[app.workspace_id]
    await loadAll()
  } catch (e) {
    message.error(e?.response?.data?.detail || '删除失败')
  }
}
</script>

<template>
  <aside class="zc-sidebar" :class="{ 'is-collapsed': collapsed }" :style="panelStyle">
    <div class="zc-logo">zerocode</div>
    <a-button type="primary" block @click="showNew = true">＋ 新会话</a-button>
    <div ref="groupsEl" class="zc-groups">
      <a-spin v-if="loading" class="zc-loading" />
      <a-empty v-else-if="!apps.length" description="还没有应用">
        <router-link class="zc-empty-link" to="/apps">去「应用」页新建应用</router-link>
      </a-empty>
      <div v-for="app in groups" :key="app.id" class="zc-group" :class="{ 'is-dragging': draggingId === app.id }">
        <div
          class="zc-group-head"
          title="点击折叠/展开；按住上下拖动可调整顺序"
          @pointerdown="startDrag($event, app.id)"
          @click="onHeadClick(app.id)"
        >
          <right-outlined class="zc-fold-arrow" :class="{ 'is-open': !isFolded(app.id) }" />
          <folder-outlined />
          <span class="zc-ws-name">{{ app.name }}</span>
          <span class="zc-count">{{ sessionsOf(app.workspace_id).length }}</span>
          <plus-outlined class="zc-add" title="在该应用下新建会话"
                         @pointerdown.stop @click.stop="addSession(app)" />
          <a-dropdown :trigger="['click']" placement="bottomRight">
            <ellipsis-outlined class="zc-menu-btn" @pointerdown.stop @click.stop />
            <template #overlay>
              <div class="zc-app-menu">
                <div class="zc-app-menu-item zc-menu-rename" @click.stop="openRename(app)">重命名</div>
                <div class="zc-app-menu-item zc-app-menu-danger zc-menu-del" @click.stop="openDelete(app)">
                  删除应用
                </div>
              </div>
            </template>
          </a-dropdown>
        </div>
        <div v-show="!isFolded(app.id)" class="zc-group-body">
          <div v-if="!sessionsOf(app.workspace_id).length" class="zc-none">暂无会话</div>
          <template v-for="s in visibleSessions(app.workspace_id)" :key="s.id">
            <div
              class="zc-session"
              :class="{ active: s.id === store.currentSessionId }"
              @click="store.openSession(s.id, app.workspace_id)"
            >
              <span class="zc-title">{{ s.title }}</span>
              <span
                v-if="badgeOf(s)"
                class="zc-session-status"
                :class="`is-${badgeOf(s)}`"
                :title="BADGE_TEXT[badgeOf(s)]"
                :aria-label="BADGE_TEXT[badgeOf(s)]"
                @click.stop="onBadgeClick(s, app.workspace_id)"
              >
                <loading-outlined v-if="badgeOf(s) === 'running'" spin />
                <clock-circle-outlined v-else-if="badgeOf(s) === 'waiting'" />
                <check-circle-filled v-else-if="badgeOf(s) === 'completed'" />
                <close-circle-filled v-else />
              </span>
              <span class="zc-time">{{ relativeTime(s.updated_at) }}</span>
              <a-popconfirm title="删除该会话？" @confirm="removeSession(app.workspace_id, s)">
                <delete-outlined class="zc-del" @click.stop />
              </a-popconfirm>
            </div>
          </template>
          <a v-if="hasMore(app.workspace_id)" class="zc-more" @click="expand[app.workspace_id] = true">
            展开其余 {{ moreCount(app.workspace_id) }} 个会话
          </a>
          <a v-else-if="expand[app.workspace_id] && sessionsOf(app.workspace_id).length > VISIBLE"
             class="zc-more" @click="expand[app.workspace_id] = false">
            收起
          </a>
        </div>
      </div>
    </div>
    <div class="zc-footer">
      <div class="zc-footer-row">
        <router-link to="/apps"><appstore-outlined /> 应用</router-link>
        <router-link to="/store"><shop-outlined /> 商店</router-link>
        <router-link to="/settings"><setting-outlined /> 设置</router-link>
      </div>
    </div>
    <NewSessionModal v-model:open="showNew" :apps="apps" @created="loadAll" />

    <a-modal v-model:open="rename.open" title="重命名应用" ok-text="保存" destroy-on-close
             :ok-button-props="{ disabled: !renameOk }" @ok="doRename">
      <a-input v-model:value="rename.name" placeholder="应用名称" class="zc-rename-input" />
      <p class="zc-modal-tip">只改显示名；应用标识（{{ rename.app?.slug }}）与工程目录保持不变。</p>
    </a-modal>

    <a-modal v-model:open="del.open" title="删除应用" ok-text="确认删除" ok-type="danger" destroy-on-close
             :ok-button-props="{ disabled: !deleteOk }" @ok="doDelete">
      <p class="zc-modal-warn">
        将删除应用「{{ del.app?.name }}」及其下 {{ sessionsOf(del.app?.workspace_id ?? 0).length }} 个会话记录。
      </p>
      <p class="zc-modal-tip">
        工程目录保留在 {{ del.app?.root_path }}，数据库 {{ del.app?.slug }}_dev / {{ del.app?.slug }}_prod
        也保留；如需彻底清理，请自行删除目录与库。
      </p>
      <p class="zc-modal-tip">请输入应用标识 <b>{{ del.app?.slug }}</b> 以确认：</p>
      <a-input v-model:value="del.typed" :placeholder="del.app?.slug" />
    </a-modal>
  </aside>
</template>

<style scoped>
.zc-sidebar {
  flex: 0 0 auto;
  width: 260px;
  display: flex;
  flex-direction: column;
  background: var(--zc-sidebar-bg);
  border-right: 1px solid var(--zc-border);
  padding: 12px;
  box-sizing: border-box;
  overflow: hidden;
  transition: width 0.2s ease, padding 0.2s ease;
}
/* 0 宽 + 内边距会被 border-box 撑成 24px，必须一并归零；内容留在 DOM 里，收起不丢状态 */
.zc-sidebar.is-collapsed {
  padding-left: 0;
  padding-right: 0;
  border-right-width: 0;
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
.zc-empty-link {
  color: var(--zc-primary);
}
.zc-group {
  margin-bottom: 10px;
}
/* 拖动中的那一组：抬起来一点，跟其它行拉开层次（换位是实时提交的，不补位移动画） */
.zc-group.is-dragging .zc-group-head {
  background: var(--zc-sidebar-bg);
  box-shadow: 0 2px 10px rgba(47, 58, 71, 0.16);
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
  user-select: none;
  /* 触屏下按住组头是拖拽排序，不能让浏览器顺手把这段当成滚动/文字选择 */
  touch-action: none;
}
.zc-group-head:hover {
  background: var(--zc-bg);
}
/* 折叠指示箭头：默认朝右=已折叠，转 90° 朝下=已展开 */
.zc-fold-arrow {
  flex-shrink: 0;
  font-size: 10px;
  color: var(--zc-text-sub);
  transition: transform 0.15s ease;
}
.zc-fold-arrow.is-open {
  transform: rotate(90deg);
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
/* 行尾操作常驻但压暗（用户要的是「看得见 + 和 ⋯」，纯 hover 显形容易被当成没做），
   hover 到组头才提亮；与既有会话删除图标的 hover 语言同一套。 */
.zc-add,
.zc-menu-btn {
  flex-shrink: 0;
  opacity: 0.45;
  transition: opacity 0.15s;
  color: var(--zc-text-sub);
}
.zc-add:hover,
.zc-menu-btn:hover {
  opacity: 1;
  color: var(--zc-primary);
}
.zc-group-head:hover .zc-add,
.zc-group-head:hover .zc-menu-btn,
.zc-menu-btn:focus {
  opacity: 1;
}
.zc-app-menu {
  min-width: 120px;
  padding: 4px;
  background: var(--zc-sidebar-bg);
  border: 1px solid var(--zc-border);
  border-radius: 6px;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.08);
}
.zc-app-menu-item {
  padding: 6px 10px;
  font-size: 13px;
  color: var(--zc-text);
  border-radius: 4px;
  cursor: pointer;
}
.zc-app-menu-item:hover {
  background: var(--zc-bg);
}
.zc-app-menu-danger {
  color: #b96a6a;
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
.zc-session-status {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  font-size: 13px;
  line-height: 1;
  cursor: pointer;
}
.zc-session-status.is-running {
  color: var(--zc-primary);
}
.zc-session-status.is-waiting {
  color: #d9a12a;
}
.zc-session-status.is-completed {
  color: #3f9d5a;
}
.zc-session-status.is-failed {
  color: #c25b5b;
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
.zc-footer-row {
  display: flex;
  justify-content: space-between;
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
.zc-modal-tip {
  margin: 8px 0 0;
  color: var(--zc-text-sub);
  font-size: 12px;
  line-height: 1.6;
  word-break: break-all;
}
.zc-modal-warn {
  margin: 0 0 4px;
  font-size: 13px;
  color: var(--zc-text);
}
</style>
