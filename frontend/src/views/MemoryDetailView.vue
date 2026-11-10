<script setup>
// 记忆详情整页（新标签打开 /workspaces/{工作区}/memory/{记忆}）。
// 与变更详情页同构：左栏索引、右侧当前条、顶部主题切换、底部状态条；
// 但记忆不是只读内容——整页承担启用/停用、编辑、删除，改完就地回写列表，不再多取一次。
// 取数只有列表接口：它本来就带全文、时间与停用行（还带截断响应头），
// 单条详情没有轻量可言，不为「按条取」另立一条后端路径。
import { computed, onMounted, ref } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { useRoute, useRouter } from 'vue-router'
import { deleteMemory, listMemories, updateMemory } from '../api'
import { normalizeId } from '../utils/openFolder'
import { TYPE_LABELS, groupMemories } from '../utils/memory'
import { nextTheme, readWorkbenchTheme, writeWorkbenchTheme } from '../utils/theme'
import MemoryItemList from '../components/memory/MemoryItemList.vue'

const route = useRoute()
const router = useRouter()

// 两个 id 都要合法：列表接口挂在工作区下面，归属校验走「工作区 → 用户」；
// 脏地址一律挡在发请求之前，页面也不会挂出一个空壳。
const workspaceId = computed(() => normalizeId(route.params.id))
const memoryId = computed(() => normalizeId(route.params.memoryId))
const addressOk = computed(() => !!workspaceId.value && !!memoryId.value)

// 当前正在看的记忆：初始取地址里的那条，点左栏后换成本地值。
// 不 watch 路由参数——页内只有左栏这一处导航，切完自己改状态就够（地址栏手改会整页重载）。
const activeId = ref(memoryId.value)

const memories = ref([])
const loading = ref(false)
const failed = ref(false)
const truncated = ref(false)
const limitUsed = ref(0)

// 连点重试时先发的列表请求可能后到：按序号丢掉过期响应
let loadSeq = 0

async function load() {
  const ws = workspaceId.value
  if (!ws) return
  const seq = ++loadSeq
  loading.value = true
  failed.value = false
  try {
    const res = await listMemories(ws)
    if (seq !== loadSeq) return
    memories.value = res.data || []
    truncated.value = res.headers?.['x-memory-truncated'] === 'true'
    limitUsed.value = Number(res.headers?.['x-memory-limit'] || 0)
  } catch {
    if (seq === loadSeq) {
      memories.value = []
      failed.value = true
    }
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

const active = computed(
  () => memories.value.find((m) => String(m.id) === String(activeId.value)) || null,
)

// 删后取邻条按左栏可见顺序（分组顺序），而不是接口原始顺序：
// 用户眼里的「下一条」就是列表里它下面那条，跳过组边界会跳到看不见的地方。
const ordered = computed(() => groupMemories(memories.value).flatMap((g) => g.items))

function replaceRow(row) {
  if (!row?.id) return
  memories.value = memories.value.map(
    (m) => (String(m.id) === String(row.id) ? { ...m, ...row } : m),
  )
}

function selectMemory(m) {
  const id = normalizeId(m?.id)
  if (!id || id === activeId.value) return
  activeId.value = id
  editing.value = false
  router.replace({
    name: 'workspace-memory-detail',
    params: { id: workspaceId.value, memoryId: id },
  })
}

// ---------------- 启用/停用 ----------------
const togglingId = ref(null)

async function toggleEnabled() {
  const m = active.value
  if (!m || togglingId.value !== null) return
  togglingId.value = m.id
  try {
    const { data } = await updateMemory(workspaceId.value, m.id, { enabled: !m.enabled })
    replaceRow(data) // 以服务端返回为准，不本地取反
  } catch (e) {
    if (e?.response?.status === 404) {
      message.info('该记忆已不存在，已刷新列表')
      await load()
    } else {
      message.error(e?.response?.data?.detail || '更新失败')
    }
  } finally {
    togglingId.value = null
  }
}

// ---------------- 编辑 ----------------
const editing = ref(false)
const editType = ref('fact')
const editTitle = ref('')
const editContent = ref('')
const saving = ref(false)

function startEdit() {
  const m = active.value
  if (!m) return
  editType.value = m.type
  editTitle.value = m.title || ''
  editContent.value = m.content || ''
  editing.value = true
}

function cancelEdit() {
  editing.value = false
}

async function saveEdit() {
  const m = active.value
  if (!m) return
  if (!editContent.value.trim()) {
    message.warning('内容不能为空')
    return
  }
  saving.value = true
  try {
    const { data } = await updateMemory(workspaceId.value, m.id, {
      type: editType.value,
      content: editContent.value,
      title: editTitle.value.trim(), // 空串 → 后端归一成 NULL；不可退成 null（= 保持原值）
    })
    replaceRow(data)
    editing.value = false
    message.success('已更新')
  } catch (e) {
    if (e?.response?.status === 404) {
      message.info('该记忆已不存在，已刷新列表')
      editing.value = false
      await load()
    } else {
      message.error(e?.response?.data?.detail || '更新失败')
    }
  } finally {
    saving.value = false
  }
}

// ---------------- 删除 ----------------
function confirmDelete() {
  const m = active.value
  if (!m) return
  Modal.confirm({
    title: '确认删除',
    content: `删除后不可恢复。确定要删除这条记忆吗？${m.title ? `（${m.title}）` : ''}`,
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    onOk: async () => {
      const idx = ordered.value.findIndex((x) => String(x.id) === String(m.id))
      const neighbor = ordered.value[idx + 1] || ordered.value[idx - 1] || null
      try {
        await deleteMemory(workspaceId.value, m.id)
        message.success('已删除')
        memories.value = memories.value.filter((x) => String(x.id) !== String(m.id))
        editing.value = false
        if (neighbor) {
          activeId.value = String(neighbor.id)
          router.replace({
            name: 'workspace-memory-detail',
            params: { id: workspaceId.value, memoryId: String(neighbor.id) },
          })
        } else {
          // 删空：侧栏与右栏都进空态；地址保留原记忆号，重开时按「已不存在」处理
          activeId.value = ''
        }
      } catch (e) {
        if (e?.response?.status === 404) {
          message.info('该记忆已不存在，已刷新列表')
          await load()
        } else {
          message.error(e?.response?.data?.detail || '删除失败')
        }
      }
    },
  })
}

// ---------------- 展示辅助 ----------------
function fmtTime(raw) {
  if (!raw) return ''
  const d = new Date(raw)
  if (Number.isNaN(d.getTime())) return String(raw)
  const p = (x) => String(x).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

const typeLabel = computed(() => {
  const m = active.value
  if (!m) return ''
  return TYPE_LABELS[m.type] || m.type || '未分类'
})

const statusText = computed(() => {
  if (loading.value) return '读取中…'
  if (failed.value) return '列表加载失败'
  const parts = []
  if (truncated.value) parts.push(`仅展示最近 ${limitUsed.value} 条`)
  parts.push(`共 ${memories.value.length} 条`)
  const m = active.value
  if (m) {
    parts.push(typeLabel.value)
    parts.push(m.enabled ? '启用' : '停用')
    if (m.updated_at) parts.push(`更新于 ${fmtTime(m.updated_at)}`)
  }
  return parts.join(' · ')
})

const theme = ref(readWorkbenchTheme())
const themeLabel = computed(() => (theme.value === 'dark' ? '浅色' : '深色'))

function toggleTheme() {
  theme.value = nextTheme(theme.value)
  writeWorkbenchTheme(theme.value)
}

onMounted(() => {
  if (!addressOk.value) return
  load()
})
</script>

<template>
  <div class="zc-memd" :data-theme="theme">
    <div class="zc-memd-head">
      <div class="zc-memd-title">
        <span v-if="typeLabel" class="zc-memd-type">{{ typeLabel }}</span>
        <span class="zc-memd-name" :title="active?.title || ''">{{ active?.title || '记忆详情' }}</span>
        <span v-if="active && !active.enabled" class="zc-memd-off">已停用</span>
      </div>
      <button
        type="button"
        class="zc-memd-theme-toggle"
        :title="`切换到${themeLabel}主题`"
        @click="toggleTheme"
      >{{ themeLabel }}</button>
    </div>

    <div v-if="!addressOk" class="zc-memd-bad">
      地址不正确：/workspaces/…/memory/… 里的工作区号或记忆号不是数字，请从聊天页右栏的记忆里重新打开。
    </div>
    <div v-else class="zc-memd-body">
      <MemoryItemList
        :memories="memories"
        :active-id="activeId"
        :loading="loading"
        :failed="failed"
        @select="selectMemory"
        @retry="load"
      />
      <div class="zc-memd-main">
        <div v-if="loading" class="zc-memd-loading">读取中…</div>
        <div v-else-if="failed" class="zc-memd-note">记忆列表加载失败，左侧可重试。</div>
        <div v-else-if="!active" class="zc-memd-note">
          {{ memories.length ? '该记忆已不存在，请从左侧重新选择一条。' : '暂无记忆。' }}
        </div>
        <template v-else>
          <div class="zc-memd-pane-head">
            <span class="zc-memd-when">
              创建于 {{ fmtTime(active.created_at) || '未知时间' }}
              <template v-if="active.updated_at"> · 更新于 {{ fmtTime(active.updated_at) }}</template>
            </span>
            <div class="zc-memd-acts">
              <a-switch
                :checked="active.enabled"
                :disabled="togglingId !== null"
                size="small"
                checked-children="启用"
                un-checked-children="停用"
                @change="toggleEnabled"
              />
              <a-button v-if="!editing" size="small" @click="startEdit">编辑</a-button>
              <a-button v-if="!editing" size="small" danger @click="confirmDelete">删除</a-button>
            </div>
          </div>

          <div v-if="editing" class="zc-memd-form">
            <a-select v-model:value="editType" size="small" class="zc-memd-form-type">
              <a-select-option v-for="(label, key) in TYPE_LABELS" :key="key" :value="key">
                {{ label }}
              </a-select-option>
            </a-select>
            <a-input v-model:value="editTitle" placeholder="标题（可选）" size="small" />
            <a-textarea
              v-model:value="editContent"
              :rows="12"
              class="zc-memd-form-content"
            />
            <div class="zc-memd-form-acts">
              <a-button size="small" type="primary" :loading="saving" @click="saveEdit">保存</a-button>
              <a-button size="small" @click="cancelEdit">取消</a-button>
            </div>
          </div>
          <div v-else class="zc-memd-content">{{ active.content }}</div>
        </template>
      </div>
    </div>

    <div v-if="statusText" class="zc-memd-status">{{ statusText }}</div>
  </div>
</template>

<style scoped>
/* 壳层账户栏占 40px，与变更详情页同一口径 */
.zc-memd {
  box-sizing: border-box;
  height: calc(100vh - 40px);
  display: flex;
  flex-direction: column;
  padding: 0 16px 16px;
  overflow: hidden;
}
.zc-memd[data-theme='dark'] {
  --zc-bg: #1e1e1e;
  --zc-sidebar-bg: #252526;
  --zc-border: #3c3c3c;
  --zc-text: #cccccc;
  --zc-text-sub: #8c8c8c;
}
.zc-memd[data-theme='light'] {
  --zc-bg: #ffffff;
  --zc-sidebar-bg: #f3f3f3;
  --zc-border: #e0e0e0;
  --zc-text: #333333;
  --zc-text-sub: #6b6b6b;
}
.zc-memd-head {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 0;
  border-bottom: 1px solid var(--zc-border);
}
.zc-memd-title {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: baseline;
  gap: 10px;
}
.zc-memd-type {
  flex: none;
  padding: 1px 7px;
  border-radius: 3px;
  background: #007acc;
  color: #ffffff;
  font-size: 11px;
}
.zc-memd-name {
  min-width: 0;
  max-width: 60%;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-size: 14px;
  font-weight: 600;
  color: var(--zc-text);
}
.zc-memd-off {
  flex: none;
  font-size: 12px;
  color: var(--zc-text-sub);
}
.zc-memd-theme-toggle {
  flex: none;
  border: 1px solid var(--zc-border);
  border-radius: 3px;
  background: var(--zc-sidebar-bg);
  color: var(--zc-text);
  font-size: 12px;
  line-height: 18px;
  padding: 2px 8px;
  cursor: pointer;
}
.zc-memd-theme-toggle:hover {
  border-color: #007acc;
}
.zc-memd-bad,
.zc-memd-note {
  margin-top: 16px;
  padding: 12px 14px;
  border: 1px solid var(--zc-border);
  border-radius: 4px;
  background: var(--zc-sidebar-bg);
  color: var(--zc-text);
  font-size: 13px;
}
.zc-memd-body {
  flex: 1;
  min-height: 0;
  display: flex;
  gap: 10px;
  margin-top: 10px;
}
.zc-memd-main {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  border: 1px solid var(--zc-border);
  border-radius: 6px;
  background: var(--zc-bg);
  overflow: hidden;
}
.zc-memd-pane-head {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--zc-border);
  background: var(--zc-sidebar-bg);
}
.zc-memd-when {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  color: var(--zc-text-sub);
}
.zc-memd-acts {
  flex: none;
  display: flex;
  align-items: center;
  gap: 8px;
}
.zc-memd-content {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 14px 16px;
  font-size: 13px;
  line-height: 1.8;
  color: var(--zc-text);
  white-space: pre-wrap;
  word-break: break-word;
}
.zc-memd-form {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px;
}
.zc-memd-form-type {
  width: 160px;
}
.zc-memd-form-content {
  flex: 1;
  min-height: 0;
  resize: none;
}
.zc-memd-form-acts {
  display: flex;
  gap: 8px;
}
.zc-memd-loading {
  margin-top: 24px;
  text-align: center;
  font-size: 12px;
  color: var(--zc-text-sub);
}
/* VS Code 那根蓝底状态条：深浅两色下都不跟着主体翻色 */
.zc-memd-status {
  flex: 0 0 auto;
  height: 24px;
  display: flex;
  align-items: center;
  padding: 0 10px;
  background: #007acc;
  color: #ffffff;
  font-size: 12px;
  user-select: none;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
</style>
