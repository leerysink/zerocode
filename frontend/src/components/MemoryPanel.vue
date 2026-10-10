<script setup>
// 工作区记忆面板（Ticket 008）：CRUD + type 分组 + 搜索 + 启用开关 + 截断条幅。
// 与 Agent 写的是同一份 `workspace_memory`（无双轨），面板里加一条 → 下轮召回带上它。
// id 全程按字符串透传（19 位大整数过 Number() 会被 JS 精度悄悄截断）。
import { computed, onMounted, ref, watch } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { PlusOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons-vue'
import { createMemory, deleteMemory, listMemories, updateMemory } from '../api'

const props = defineProps({
  workspaceId: { type: [Number, String], required: true },
})

// type 中文标签（与后端 MEMORY_TYPES 一一对应）
const TYPE_LABELS = {
  preference: '偏好',
  decision: '决策',
  fact: '事实',
  pitfall: '踩坑',
}

const memories = ref([])
const loading = ref(false)
const truncated = ref(false)
const limitUsed = ref(0)
const search = ref('')
const showForm = ref(false)
const formType = ref('fact')
const formContent = ref('')
const formTitle = ref('')
const submitting = ref(false)

// 编辑态：editingId 非空表示该行在编辑
const editingId = ref(null)
const editContent = ref('')
const editTitle = ref('')
const editType = ref('fact')
const saving = ref(false)

// 启用开关在飞互斥（对齐 SkillsView I-1/M-4 口径）
const togglingId = ref(null)

async function load() {
  loading.value = true
  try {
    const res = await listMemories(props.workspaceId)
    memories.value = res.data
    truncated.value = res.headers['x-memory-truncated'] === 'true'
    limitUsed.value = Number(res.headers['x-memory-limit'] || 0)
  } catch (e) {
    message.error(e?.response?.data?.detail || '记忆列表加载失败')
  } finally {
    loading.value = false
  }
}

// 前端关键字过滤（标题 + 正文）；不传后端（面板只做列表与关键字过滤，006 的语义检索不重复）
const filtered = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return memories.value
  return memories.value.filter((m) =>
    (m.title || '').toLowerCase().includes(q) ||
    (m.content || '').toLowerCase().includes(q),
  )
})

// 按 type 分组（保持 MEMORY_TYPES 顺序：preference / decision / fact / pitfall）
// 未知 type 兜底归末组，不丢行（后端扩展分类时面板不静默少给）
const grouped = computed(() => {
  const groups = []
  const known = new Set(Object.keys(TYPE_LABELS))
  for (const type of Object.keys(TYPE_LABELS)) {
    const items = filtered.value.filter((m) => m.type === type)
    if (items.length) groups.push({ type, label: TYPE_LABELS[type], items })
  }
  const extras = filtered.value.filter((m) => !known.has(m.type))
  if (extras.length) groups.push({ type: '_other', label: '其他', items: extras })
  return groups
})

async function submit() {
  if (!formContent.value.trim()) {
    message.warning('内容不能为空')
    return
  }
  submitting.value = true
  try {
    await createMemory(props.workspaceId, {
      type: formType.value,
      content: formContent.value,
      title: formTitle.value.trim() || null,
    })
    message.success('已添加')
    showForm.value = false
    formContent.value = ''
    formTitle.value = ''
    formType.value = 'fact'
    await load()
  } catch (e) {
    message.error(e?.response?.data?.detail || '添加失败')
  } finally {
    submitting.value = false
  }
}

function startEdit(m) {
  editingId.value = m.id
  editContent.value = m.content
  editTitle.value = m.title || ''
  editType.value = m.type
}

function cancelEdit() {
  editingId.value = null
}

async function saveEdit(m) {
  if (!editContent.value.trim()) {
    message.warning('内容不能为空')
    return
  }
  saving.value = true
  try {
    await updateMemory(props.workspaceId, m.id, {
      type: editType.value,
      content: editContent.value,
      title: editTitle.value.trim(), // 空串 → 后端 normalize_title → NULL；不可退成 null（= 保持原值）
    })
    message.success('已更新')
    editingId.value = null
    await load()
  } catch (e) {
    if (e?.response?.status === 404) {
      message.info('该记忆已不存在，已刷新列表')
      editingId.value = null
      await load()
    } else {
      message.error(e?.response?.data?.detail || '更新失败')
    }
  } finally {
    saving.value = false
  }
}

async function toggleEnabled(m) {
  if (togglingId.value !== null) return // 在飞时忽略重复/其他行的翻转
  togglingId.value = m.id
  try {
    const { data } = await updateMemory(props.workspaceId, m.id, { enabled: !m.enabled })
    m.enabled = data.enabled // 以服务端返回为准，不本地取反
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

function confirmDelete(m) {
  Modal.confirm({
    title: '确认删除',
    content: `删除后不可恢复。确定要删除这条记忆吗？${m.title ? `（${m.title}）` : ''}`,
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    onOk: async () => {
      try {
        await deleteMemory(props.workspaceId, m.id)
        message.success('已删除')
        await load()
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

// 工作区切换：重新拉取
watch(() => props.workspaceId, () => load())

onMounted(load)
</script>

<template>
  <div class="zc-mem">
    <div class="zc-mem-toolbar">
      <a-button size="small" :loading="loading" @click="load">
        <template #icon><reload-outlined /></template>
        刷新
      </a-button>
      <a-button size="small" type="primary" @click="showForm = !showForm">
        <template #icon><plus-outlined /></template>
        新增
      </a-button>
      <a-input-search
        v-model:value="search"
        placeholder="搜索记忆"
        size="small"
        class="zc-mem-search"
        allow-clear
      >
        <template #prefix><search-outlined /></template>
      </a-input-search>
    </div>

    <!-- 截断条幅：X-Memory-Truncated 为 true 时显示，不能静默少给 -->
    <a-alert
      v-if="truncated"
      type="info"
      show-icon
      class="zc-mem-truncated"
    >
      <template #message>
        仅展示最近 {{ limitUsed }} 条，更早的记忆未列出
      </template>
    </a-alert>

    <!-- 新增表单 -->
    <div v-if="showForm" class="zc-mem-form">
      <a-select v-model:value="formType" size="small" class="zc-mem-form-type">
        <a-select-option v-for="(label, key) in TYPE_LABELS" :key="key" :value="key">
          {{ label }}
        </a-select-option>
      </a-select>
      <a-input v-model:value="formTitle" placeholder="标题（可选）" size="small" class="zc-mem-form-title" />
      <a-textarea
        v-model:value="formContent"
        placeholder="记忆内容"
        :rows="3"
        class="zc-mem-form-content"
      />
      <div class="zc-mem-form-actions">
        <a-button size="small" type="primary" :loading="submitting" @click="submit">添加</a-button>
        <a-button size="small" @click="showForm = false">取消</a-button>
      </div>
    </div>

    <a-spin v-if="loading && !memories.length" class="zc-mem-loading" />
    <a-empty v-else-if="!filtered.length && !loading" description="没有记忆" class="zc-mem-empty" />

    <div v-else class="zc-mem-list">
      <div v-for="group in grouped" :key="group.type" class="zc-mem-group">
        <div class="zc-mem-group-title">{{ group.label }}（{{ group.items.length }}）</div>
        <div v-for="m in group.items" :key="m.id" class="zc-mem-item">
          <!-- 编辑态 -->
          <template v-if="editingId === m.id">
            <a-select v-model:value="editType" size="small" class="zc-mem-edit-type">
              <a-select-option v-for="(label, key) in TYPE_LABELS" :key="key" :value="key">
                {{ label }}
              </a-select-option>
            </a-select>
            <a-input v-model:value="editTitle" placeholder="标题（可选）" size="small" class="zc-mem-edit-title" />
            <a-textarea v-model:value="editContent" :rows="3" class="zc-mem-edit-content" />
            <div class="zc-mem-edit-actions">
              <a-button size="small" type="primary" :loading="saving" @click="saveEdit(m)">保存</a-button>
              <a-button size="small" @click="cancelEdit">取消</a-button>
            </div>
          </template>
          <!-- 展示态 -->
          <template v-else>
            <div class="zc-mem-item-head">
              <span class="zc-mem-title">{{ m.title || '(无标题)' }}</span>
              <a-switch
                :checked="m.enabled"
                :disabled="togglingId === m.id"
                size="small"
                checked-children="启用"
                un-checked-children="停用"
                @change="toggleEnabled(m)"
              />
            </div>
            <div class="zc-mem-content">{{ m.content }}</div>
            <div class="zc-mem-item-actions">
              <a-button size="small" type="link" @click="startEdit(m)">编辑</a-button>
              <a-button size="small" type="link" danger @click="confirmDelete(m)">删除</a-button>
            </div>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.zc-mem {
  padding: 8px 0;
}
.zc-mem-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.zc-mem-search {
  flex: 1;
  min-width: 120px;
}
.zc-mem-truncated {
  margin-bottom: 8px;
}
.zc-mem-form {
  border: 1px solid var(--zc-border, #e8e8e8);
  border-radius: 4px;
  padding: 8px;
  margin-bottom: 12px;
  background: var(--zc-bg, #fafafa);
}
.zc-mem-form-type,
.zc-mem-form-title {
  width: 100%;
  margin-bottom: 6px;
}
.zc-mem-form-content {
  width: 100%;
  margin-bottom: 6px;
}
.zc-mem-form-actions {
  display: flex;
  gap: 8px;
}
.zc-mem-loading {
  display: block;
  margin: 24px auto;
}
.zc-mem-empty {
  margin: 24px 0;
}
.zc-mem-group {
  margin-bottom: 12px;
}
.zc-mem-group-title {
  font-weight: 600;
  font-size: 13px;
  color: var(--zc-text, #333);
  margin-bottom: 6px;
  padding-bottom: 4px;
  border-bottom: 1px solid var(--zc-border, #e8e8e8);
}
.zc-mem-item {
  border: 1px solid var(--zc-border, #e8e8e8);
  border-radius: 4px;
  padding: 8px;
  margin-bottom: 6px;
  background: #fff;
}
.zc-mem-item-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 4px;
}
.zc-mem-title {
  font-weight: 500;
  font-size: 13px;
  color: var(--zc-text, #333);
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.zc-mem-content {
  font-size: 12px;
  color: var(--zc-text-sub, #666);
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  margin-bottom: 4px;
  max-height: 120px;
  overflow: hidden;
}
.zc-mem-item-actions {
  display: flex;
  gap: 4px;
}
.zc-mem-edit-type,
.zc-mem-edit-title {
  width: 100%;
  margin-bottom: 6px;
}
.zc-mem-edit-content {
  width: 100%;
  margin-bottom: 6px;
}
.zc-mem-edit-actions {
  display: flex;
  gap: 8px;
}
</style>
