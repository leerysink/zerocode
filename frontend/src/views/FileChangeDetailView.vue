<script setup>
// 变更记录详情整页（新标签打开 /sessions/{会话}/changes/{记录}）。
// 为什么也是整页而不是弹框：看一份改动要长时间占用视线，两侧全文加行号压在聊天页右栏里谁都看不清；
// 独立地址还能收藏、能后退、能开在第二个屏幕上对照着写（ADR-0011 那套理由在这里同样成立）。
// 本页只管三件事：地址能不能发请求、这一条变更的取数、把行模型交给双栏面板。
// 行模型（哪几行改了什么、怎么对齐、折叠段怎么展开）全在 utils/sideBySide + ChangeDiffPane 里，
// 页面不碰 diff 一个字符。
import { computed, onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import { useRoute } from 'vue-router'
import { getFileChange } from '../api'
import { normalizeId, openWorkspaceFilesPage } from '../utils/openFolder'
import { buildSideBySide } from '../utils/sideBySide'
import { nextTheme, readWorkbenchTheme, writeWorkbenchTheme } from '../utils/theme'
import ChangeDiffPane from '../components/change/ChangeDiffPane.vue'

const route = useRoute()

// 两个 id 都要合法：详情接口挂在会话下面，归属校验走「会话 → 工作区 → 用户」那条链，
// 少一段就只能另立一条按记录号直查的鉴权路径。脏地址一律挡在发请求之前。
const sessionId = computed(() => normalizeId(route.params.sessionId))
const changeId = computed(() => normalizeId(route.params.changeId))
const addressOk = computed(() => !!sessionId.value && !!changeId.value)

const row = ref(null)
const loading = ref(false)
const failed = ref(false)

async function load() {
  if (!addressOk.value) return
  loading.value = true
  failed.value = false
  row.value = null
  try {
    const res = await getFileChange(sessionId.value, changeId.value)
    row.value = res.data
  } catch {
    failed.value = true
  } finally {
    loading.value = false
  }
}

onMounted(load)

const model = computed(() => (row.value
  ? buildSideBySide({
    diff: row.value.diff,
    before: row.value.before_content,
    after: row.value.after_content,
  })
  : null))

// 与后端 tools.py 的 action 取值对应（create/edit/delete），未知值原样展示
const ACTION_LABEL = { create: '新建', edit: '修改', delete: '删除' }
const actionLabel = computed(() => {
  const a = row.value?.action || ''
  return ACTION_LABEL[a] || a
})

const workspaceLabel = computed(() => {
  const r = row.value
  if (!r) return ''
  return r.workspace_name || (r.workspace_id ? `#${r.workspace_id}` : '')
})

const createdAt = computed(() => {
  const raw = row.value?.created_at
  if (!raw) return ''
  const d = new Date(raw)
  if (Number.isNaN(d.getTime())) return String(raw)
  const p = (x) => String(x).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
})

const statusText = computed(() => {
  const m = model.value
  if (!m) return ''
  if (m.status === 'identical') return '本次写入无行级变化'
  if (m.status === 'unaligned') return '未逐行对齐'
  return `+${m.added} -${m.removed}`
})

const theme = ref(readWorkbenchTheme())
const themeLabel = computed(() => (theme.value === 'dark' ? '浅色' : '深色'))

function toggleTheme() {
  theme.value = nextTheme(theme.value)
  writeWorkbenchTheme(theme.value)
}

// 死内容看够了要能跳回活文件：整页复用「打开文件夹」那一条地址，同一工作区仍复用同一个标签
function openInEditor() {
  const r = row.value
  if (!r) return
  if (!openWorkspaceFilesPage(r.workspace_id, r.workspace_name || '', r.path)) {
    message.error('浏览器拦住了新标签，允许弹窗后再点一次')
  }
}
</script>

<template>
  <div class="zc-fcd" :data-theme="theme">
    <div class="zc-fcd-head">
      <div class="zc-fcd-title">
        <span v-if="actionLabel" class="zc-fcd-action">{{ actionLabel }}</span>
        <span class="zc-fcd-path" :title="row?.path || ''">{{ row?.path || '变更记录' }}</span>
        <span v-if="workspaceLabel" class="zc-fcd-ws" :title="workspaceLabel">{{ workspaceLabel }}</span>
        <span v-if="createdAt" class="zc-fcd-time">{{ createdAt }}</span>
      </div>
      <div class="zc-fcd-acts">
        <button
          v-if="row"
          type="button"
          class="zc-fcd-open-editor"
          @click="openInEditor"
        >在编辑器中打开该文件</button>
        <button
          type="button"
          class="zc-fcd-theme-toggle"
          :title="`切换到${themeLabel}主题`"
          @click="toggleTheme"
        >{{ themeLabel }}</button>
      </div>
    </div>

    <div v-if="!addressOk" class="zc-fcd-bad">
      地址不正确：/sessions/…/changes/… 里的会话号或记录号不是数字，请从聊天页右栏的变更记录里重新打开。
    </div>
    <div v-else-if="loading" class="zc-fcd-loading">读取中…</div>
    <div v-else-if="failed" class="zc-fcd-error">
      看不到这条变更：它可能不属于这个会话，也可能已经被删掉了。
    </div>
    <ChangeDiffPane
      v-else-if="model"
      class="zc-fcd-pane"
      :model="model"
      :before="row.before_content"
      :after="row.after_content"
      :action="row.action"
      :theme="theme"
    />

    <div v-if="statusText" class="zc-fcd-status">{{ statusText }}</div>
  </div>
</template>

<style scoped>
/* 壳层账户栏占 40px（App.vue/AccountBar），与文件管理器整页同一口径 */
.zc-fcd {
  box-sizing: border-box;
  height: calc(100vh - 40px);
  display: flex;
  flex-direction: column;
  padding: 0 16px 16px;
  overflow: hidden;
}
.zc-fcd[data-theme='dark'] {
  --zc-bg: #1e1e1e;
  --zc-sidebar-bg: #252526;
  --zc-border: #3c3c3c;
  --zc-text: #cccccc;
  --zc-text-sub: #8c8c8c;
}
.zc-fcd[data-theme='light'] {
  --zc-bg: #ffffff;
  --zc-sidebar-bg: #f3f3f3;
  --zc-border: #e0e0e0;
  --zc-text: #333333;
  --zc-text-sub: #6b6b6b;
}
.zc-fcd-head {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 0;
  border-bottom: 1px solid var(--zc-border);
}
.zc-fcd-title {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: baseline;
  gap: 10px;
}
.zc-fcd-action {
  flex: none;
  padding: 1px 7px;
  border-radius: 3px;
  background: #007acc;
  color: #ffffff;
  font-size: 11px;
}
.zc-fcd-path {
  min-width: 0;
  max-width: 60%;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-size: 14px;
  font-weight: 600;
  color: var(--zc-text);
}
.zc-fcd-ws,
.zc-fcd-time {
  flex: none;
  font-size: 12px;
  color: var(--zc-text-sub);
}
.zc-fcd-acts {
  flex: none;
  display: flex;
  align-items: center;
  gap: 8px;
}
.zc-fcd-open-editor,
.zc-fcd-theme-toggle {
  border: 1px solid var(--zc-border);
  border-radius: 3px;
  background: var(--zc-sidebar-bg);
  color: var(--zc-text);
  font-size: 12px;
  line-height: 18px;
  padding: 2px 8px;
  cursor: pointer;
}
.zc-fcd-open-editor:hover,
.zc-fcd-theme-toggle:hover {
  border-color: #007acc;
}
.zc-fcd-bad,
.zc-fcd-error {
  margin-top: 16px;
  padding: 12px 14px;
  border: 1px solid var(--zc-border);
  border-radius: 4px;
  background: var(--zc-sidebar-bg);
  color: var(--zc-text);
  font-size: 13px;
}
.zc-fcd-loading {
  margin-top: 24px;
  text-align: center;
  font-size: 12px;
  color: var(--zc-text-sub);
}
.zc-fcd-pane {
  flex: 1;
  min-height: 0;
  margin-top: 10px;
  border: 1px solid var(--zc-border);
  border-radius: 6px;
  overflow: hidden;
}
/* VS Code 那根蓝底状态条：深浅两色下都不跟着主体翻色 */
.zc-fcd-status {
  flex: 0 0 auto;
  height: 24px;
  display: flex;
  align-items: center;
  padding: 0 10px;
  background: #007acc;
  color: #ffffff;
  font-size: 12px;
  user-select: none;
}
</style>
