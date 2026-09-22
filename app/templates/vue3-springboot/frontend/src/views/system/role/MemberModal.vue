<template>
  <a-modal
    v-model:open="visible"
    title="人员管理"
    :footer="null"
    :width="820"
    :destroy-on-close="true"
    class="form-modal"
  >
    <div class="modal-header">
      <div class="modal-header-icon"><UsergroupAddOutlined /></div>
      <div>
        <div class="modal-header-title">人员管理</div>
        <div class="modal-header-subtitle">
          {{ role ? `${role.roleName}（${role.roleKey}）` : '' }} · 共 {{ members.length }} 人
        </div>
      </div>
    </div>

    <div class="member-toolbar">
      <a-select
        v-model:value="selectedUserIds"
        mode="multiple"
        class="member-select"
        placeholder="选择要新增的成员"
        :options="candidateOptions"
        :max-tag-count="'responsive'"
        show-search
        option-filter-prop="label"
        :not-found-content="'所有启用用户均已授权'"
      />
      <a-button type="primary" :disabled="!selectedUserIds.length" :loading="binding" @click="handleBind">
        <PlusOutlined /> 添加
      </a-button>
    </div>

    <a-table
      bordered
      size="small"
      :columns="columns"
      :data-source="members"
      :loading="loading"
      row-key="userId"
      :pagination="false"
      :scroll="{ y: 320 }"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.dataIndex === 'username'">
          <a-avatar class="user-avatar">{{ (record.username || '').charAt(0).toUpperCase() }}</a-avatar>
          <span class="username-cell">{{ record.username }}</span>
          <a-tag v-if="record.nickname" color="blue" style="margin-left: 6px">{{ record.nickname }}</a-tag>
        </template>
        <template v-if="column.dataIndex === 'phone'">
          <span style="font-family: 'SF Mono', monospace">{{ record.phone || '-' }}</span>
        </template>
        <template v-if="column.dataIndex === 'deptName'">
          <span>{{ record.deptName || '-' }}</span>
        </template>
        <template v-if="column.dataIndex === 'status'">
          <a-tag :color="record.status === 1 ? 'success' : 'default'">
            {{ record.status === 1 ? '正常' : '已停用' }}
          </a-tag>
        </template>
        <template v-if="column.dataIndex === 'action'">
          <a-popconfirm
            :title="unbindTitle(record)"
            ok-text="确定"
            cancel-text="取消"
            @confirm="handleUnbind(record)"
          >
            <a-button type="link" size="small" danger>
              <DeleteOutlined /> 移除
            </a-button>
          </a-popconfirm>
        </template>
      </template>
    </a-table>
  </a-modal>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { PlusOutlined } from '@ant-design/icons-vue'
import {
  roleMembers, roleMemberCandidates, roleMemberBind, roleMemberUnbind,
  type RoleMember, type SysRole,
} from '@/api/role'
import { useUserStore } from '@/store/user'

const props = defineProps<{ open: boolean; role: SysRole | null }>()
const emit = defineEmits<{ (e: 'update:open', value: boolean): void }>()

const visible = computed({
  get: () => props.open,
  set: (val: boolean) => emit('update:open', val),
})

const userStore = useUserStore()
const myUserId = computed(() => userStore.userInfo?.userId)

const loading = ref(false)
const binding = ref(false)
const members = ref<RoleMember[]>([])
const candidates = ref<RoleMember[]>([])
const selectedUserIds = ref<number[]>([])

const columns = [
  { title: '成员', dataIndex: 'username', width: 240 },
  { title: '手机号', dataIndex: 'phone', width: 140 },
  { title: '所属部门', dataIndex: 'deptName', width: 160 },
  { title: '状态', dataIndex: 'status', width: 100, align: 'center' as const },
  { title: '操作', dataIndex: 'action', width: 100 },
]

const candidateOptions = computed(() =>
  candidates.value.map(u => ({ label: `${u.nickname || u.username}（${u.username}）`, value: u.userId })),
)

function unbindTitle(record: RoleMember) {
  return record.userId === myUserId.value
    ? '将移除你自己的授权，移除后你将不再是该角色成员，确认?'
    : `确认将 ${record.nickname || record.username} 移出该角色?`
}

async function loadAll() {
  if (!props.role) return
  loading.value = true
  try {
    const [memberRes, candidateRes] = await Promise.all([
      roleMembers(props.role.id),
      roleMemberCandidates(props.role.id),
    ])
    members.value = memberRes.data
    candidates.value = candidateRes.data
  } finally {
    loading.value = false
  }
}

async function handleBind() {
  if (!props.role || !selectedUserIds.value.length) return
  binding.value = true
  try {
    const count = selectedUserIds.value.length
    await roleMemberBind(props.role.id, selectedUserIds.value)
    message.success(`已添加 ${count} 人`)
    selectedUserIds.value = []
    await loadAll()
  } finally {
    binding.value = false
  }
}

async function handleUnbind(record: RoleMember) {
  if (!props.role) return
  await roleMemberUnbind(props.role.id, record.userId)
  message.success(`已移除 ${record.nickname || record.username}`)
  await loadAll()
}

watch(() => props.open, (open) => {
  if (open) {
    selectedUserIds.value = []
    loadAll()
  } else {
    members.value = []
    candidates.value = []
  }
})
</script>

<style scoped>
.modal-header {
  display: flex; align-items: center; gap: 16px;
  padding: 4px 0 16px; border-bottom: 1px solid var(--border-color); margin-bottom: 16px;
}
.modal-header-icon {
  width: 48px; height: 48px; border-radius: 12px;
  background: linear-gradient(135deg, #f0f5ff 0%, #c7d8ff 100%);
  color: var(--primary-color);
  display: flex; align-items: center; justify-content: center; font-size: 22px;
}
.modal-header-title { font-size: 16px; font-weight: 600; color: var(--text-primary); }
.modal-header-subtitle { font-size: 13px; color: var(--text-muted); margin-top: 2px; }

.member-toolbar {
  display: flex; gap: 12px; align-items: center; margin-bottom: 16px;
}
.member-select { flex: 1; }
:deep(.user-avatar) {
  background: var(--primary-gradient) !important;
  color: #fff !important;
  font-weight: 600;
  margin-right: 8px;
}
:deep(.username-cell) { font-weight: 500; color: var(--text-primary); }
</style>
