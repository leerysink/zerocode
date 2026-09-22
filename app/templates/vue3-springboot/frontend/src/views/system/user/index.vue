<template>
  <div>
    <a-card :bordered="false" class="page-card">
      <div class="search-bar">
        <a-input v-model:value="query.username" placeholder="用户名" allow-clear style="width: 200px" />
        <a-select v-model:value="query.status" placeholder="状态" allow-clear style="width: 120px">
          <a-select-option :value="1">正常</a-select-option>
          <a-select-option :value="0">停用</a-select-option>
        </a-select>
        <a-button type="primary" @click="loadData">
          <SearchOutlined /> 查询
        </a-button>
        <a-button @click="resetQuery">重置</a-button>
      </div>

      <div class="action-bar">
        <div class="action-title-group">
          <span class="page-title">
            <UserOutlined /> 用户管理
          </span>
          <a-tag color="blue">共 {{ pagination.total }} 个用户</a-tag>
        </div>
        <a-button type="primary" size="large" class="add-btn" @click="handleAdd">
          <PlusOutlined /> 新增用户
        </a-button>
      </div>

      <a-table
        bordered
        size="small"
        :columns="columns"
        :data-source="tableData"
        :pagination="pagination"
        :loading="loading"
        row-key="id"
        @change="handleTableChange"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.dataIndex === 'username'">
            <a-avatar class="user-avatar">{{ record.username.charAt(0).toUpperCase() }}</a-avatar>
            <span class="username-cell">{{ record.username }}</span>
          </template>
          <template v-if="column.dataIndex === 'nickname'">
            <span>{{ record.nickname || '-' }}</span>
          </template>
          <template v-if="column.dataIndex === 'deptName'">
            <span v-if="record.deptId" class="dept-cell">{{ deptLabel(record) }}</span>
            <span v-else>-</span>
          </template>
          <template v-if="column.dataIndex === 'phone'">
            <span style="font-family: 'SF Mono', monospace">{{ record.phone || '-' }}</span>
          </template>
          <template v-if="column.dataIndex === 'email'">
            <span style="color: var(--text-secondary); font-size: 13px">{{ record.email || '-' }}</span>
          </template>
          <template v-if="column.dataIndex === 'status'">
            <a-tag :color="record.status === 1 ? 'success' : 'default'">
              {{ record.status === 1 ? '正常' : '停用' }}
            </a-tag>
          </template>
          <template v-if="column.dataIndex === 'action'">
            <a-space :size="4">
              <a-button type="link" size="small" @click="handleEdit(record)">
                <EditOutlined /> 编辑
              </a-button>
              <a-button type="link" size="small" @click="handleResetPwd(record)">
                <KeyOutlined /> 重置密码
              </a-button>
              <a-popconfirm
                title="确认删除该用户?"
                ok-text="确定"
                cancel-text="取消"
                @confirm="handleDelete(record)"
              >
                <a-button type="link" size="small" danger>
                  <DeleteOutlined /> 删除
                </a-button>
              </a-popconfirm>
            </a-space>
          </template>
        </template>
      </a-table>
    </a-card>

    <!-- 新增/编辑弹窗 -->
    <a-modal
      v-model:open="modalVisible"
      :title="isEdit ? '编辑用户' : '新增用户'"
      :footer="null"
      :width="720"
      :destroy-on-close="true"
      class="form-modal"
    >
      <div class="modal-header">
        <div class="modal-header-icon"><UserOutlined /></div>
        <div>
          <div class="modal-header-title">{{ isEdit ? '编辑用户' : '新增用户' }}</div>
          <div class="modal-header-subtitle">
            {{ isEdit ? '修改用户基础信息和角色' : '添加系统用户并分配角色' }}
          </div>
        </div>
      </div>
      <a-form :model="form" layout="vertical" :rules="rules" @finish="handleSubmit">
        <a-row :gutter="16">
          <a-col :span="12">
            <a-form-item label="用户名" name="username">
              <a-input
                v-model:value="form.username"
                :disabled="isEdit"
                placeholder="登录用户名"
                size="large"
                allow-clear
              >
                <template #prefix><UserOutlined /></template>
              </a-input>
            </a-form-item>
          </a-col>
          <a-col :span="12">
            <a-form-item label="昵称">
              <a-input v-model:value="form.nickname" placeholder="显示名称" size="large" allow-clear>
                <template #prefix><SmileOutlined /></template>
              </a-input>
            </a-form-item>
          </a-col>
          <a-col :span="12" v-if="!isEdit">
            <a-form-item label="登录密码">
              <a-input-password
                v-model:value="form.password"
                placeholder="留空默认 123456"
                size="large"
              >
                <template #prefix><LockOutlined /></template>
              </a-input-password>
            </a-form-item>
          </a-col>
          <a-col :span="12">
            <a-form-item label="所属部门">
              <!-- 编辑态不给清空入口：后端把 deptId 为 null 当作「不改部门」（SysUserServiceImpl 会跳过 null 字段），
                   传 undefined 后 axios 直接把这个键从 JSON 里丢掉了，
                   能清空就变成「点了保存、提示修改成功、部门一点没变」的静默无效操作 -->
              <a-tree-select
                v-model:value="form.deptId"
                :field-names="{ label: 'label', value: 'id', children: 'children' }"
                :tree-data="deptTreeData"
                tree-node-filter-prop="label"
                :allow-clear="!isEdit"
                placeholder="请选择所属部门"
                style="width: 100%"
                size="large"
              />
            </a-form-item>
          </a-col>
          <a-col :span="12">
            <a-form-item label="手机号">
              <a-input v-model:value="form.phone" placeholder="联系电话" size="large" allow-clear>
                <template #prefix><PhoneOutlined /></template>
              </a-input>
            </a-form-item>
          </a-col>
          <a-col :span="12">
            <a-form-item label="邮箱">
              <a-input v-model:value="form.email" placeholder="邮箱地址" size="large" allow-clear>
                <template #prefix><MailOutlined /></template>
              </a-input>
            </a-form-item>
          </a-col>
          <a-col :span="24">
            <a-form-item label="分配角色">
              <a-select
                v-model:value="form.roleIds"
                mode="multiple"
                placeholder="请选择角色（可多选）"
                :options="roleOptions"
                size="large"
              />
            </a-form-item>
          </a-col>
          <a-col :span="24">
            <a-form-item label="状态">
              <a-radio-group v-model:value="form.status" button-style="solid" size="large">
                <a-radio-button :value="1">正常</a-radio-button>
                <a-radio-button :value="0">停用</a-radio-button>
              </a-radio-group>
            </a-form-item>
          </a-col>
        </a-row>
        <div class="modal-footer">
          <a-button @click="modalVisible = false" size="large">取消</a-button>
          <a-button type="primary" html-type="submit" :loading="submitting" size="large">
            <SaveOutlined v-if="isEdit" />
            <PlusOutlined v-else />
            {{ isEdit ? '保存修改' : '确认添加' }}
          </a-button>
        </div>
      </a-form>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { message } from 'ant-design-vue'
import { PlusOutlined, SaveOutlined } from '@ant-design/icons-vue'
import { userPage, userAdd, userUpdate, userDelete, userRoleIds, resetPassword, type SysUser, type SysUserDTO } from '@/api/user'
import { roleList, type SysRole } from '@/api/role'
import { deptTree } from '@/api/dept'

const loading = ref(false)
const submitting = ref(false)
const tableData = ref<SysUser[]>([])
const modalVisible = ref(false)
const isEdit = ref(false)
const deptTreeData = ref<any[]>([])
const roleOptions = ref<{ label: string; value: number }[]>([])

const query = reactive({ username: '', status: undefined as number | undefined })
const form = reactive<SysUserDTO>({
  username: '', status: 1, roleIds: []
})

const rules = {
  username: [{ required: true, message: '请输入用户名' }],
}

const pagination = reactive({
  current: 1, pageSize: 10, total: 0, showSizeChanger: true,
  showTotal: (total: number) => `共 ${total} 个`,
})

const columns = [
  { title: '用户名', dataIndex: 'username', width: 200 },
  { title: '昵称', dataIndex: 'nickname', width: 140 },
  { title: '所属部门', dataIndex: 'deptName', width: 160 },
  { title: '手机号', dataIndex: 'phone', width: 140 },
  { title: '邮箱', dataIndex: 'email', ellipsis: true },
  { title: '状态', dataIndex: 'status', width: 100, align: 'center' as const },
  { title: '创建时间', dataIndex: 'createTime', width: 180 },
  { title: '操作', dataIndex: 'action', width: 270, fixed: 'right' as const },
]

/* 部门名称：后端左连接 sys_dept 直接返回（含已软删部门），兜底显示 ID 仅在部门被物理删除时命中 */
function deptLabel(record: SysUser) {
  return record.deptName || `部门#${record.deptId}`
}

async function loadData() {
  loading.value = true
  try {
    const res = await userPage({
      current: pagination.current, size: pagination.pageSize,
      username: query.username || undefined, status: query.status,
    })
    tableData.value = res.data.records
    pagination.total = res.data.total
  } finally { loading.value = false }
}

function resetQuery() { query.username = ''; query.status = undefined; pagination.current = 1; loadData() }
function handleTableChange(pag: any) { pagination.current = pag.current; pagination.pageSize = pag.pageSize; loadData() }

function handleAdd() {
  isEdit.value = false
  Object.assign(form, {
    id: undefined, username: '', password: '', nickname: '', deptId: undefined,
    phone: '', email: '', status: 1, roleIds: []
  })
  modalVisible.value = true
}

async function handleEdit(record: SysUser) {
  isEdit.value = true
  Object.assign(form, record, { password: '' })
  const res = await userRoleIds(record.id)
  form.roleIds = res.data
  modalVisible.value = true
}

async function handleSubmit() {
  submitting.value = true
  try {
    if (isEdit.value) { await userUpdate(form); message.success('修改成功') }
    else { await userAdd(form); message.success('新增成功') }
    modalVisible.value = false
    loadData()
  } finally { submitting.value = false }
}

async function handleDelete(record: SysUser) {
  await userDelete(record.id)
  message.success('删除成功')
  loadData()
}

async function handleResetPwd(record: SysUser) {
  await resetPassword(record.id, '123456')
  message.success('密码已重置为 123456')
}

onMounted(async () => {
  const [deptRes, roleRes] = await Promise.all([deptTree(), roleList()])
  deptTreeData.value = deptRes.data || []
  roleOptions.value = roleRes.data.map((r: SysRole) => ({ label: r.roleName, value: r.id }))
  loadData()
})
</script>

<style scoped>
.page-card { background: #fff !important; border-radius: var(--radius-md) !important; }
.search-bar {
  display: flex; flex-wrap: wrap; gap: 12px; align-items: center;
  padding: 16px 20px; background: linear-gradient(to right, #f8fafc 0%, #ffffff 100%);
  border-radius: var(--radius-md); margin-bottom: 16px;
  border: 1px solid var(--border-color);
}
.action-bar {
  display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 16px; padding: 0 4px;
}
.action-title-group {
  display: flex;
  align-items: center;
  gap: 8px;
}
.page-title {
  font-size: 18px; font-weight: 600; color: var(--text-primary);
  display: inline-flex; align-items: center; gap: 8px;
}
.page-title :deep(.anticon) { color: var(--primary-color); font-size: 20px; }
.add-btn {
  background: var(--primary-gradient) !important;
  border-color: transparent !important;
  box-shadow: 0 4px 14px rgba(29, 78, 255, 0.35);
}
:deep(.user-avatar) {
  background: var(--primary-gradient) !important;
  color: #fff !important;
  font-weight: 600;
  margin-right: 8px;
}
:deep(.username-cell) { font-weight: 500; color: var(--text-primary); }
:deep(.dept-cell) {
  background: #f0f5ff; color: var(--primary-color);
  padding: 2px 8px; border-radius: 4px;
  font-size: 13px;
}

/* 弹窗 */
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
.modal-footer {
  display: flex; justify-content: flex-end; gap: 12px;
  padding-top: 16px; margin-top: 16px; border-top: 1px solid var(--border-color);
}
.modal-footer .ant-btn-primary { min-width: 140px; }
</style>
