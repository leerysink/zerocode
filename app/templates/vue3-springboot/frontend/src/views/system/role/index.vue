<template>
  <div>
    <a-card :bordered="false" class="page-card">
      <div class="search-bar">
        <a-input v-model:value="roleName" placeholder="角色名称" allow-clear style="width: 200px" />
        <a-button type="primary" @click="loadData">
          <SearchOutlined /> 查询
        </a-button>
      </div>

      <div class="action-bar">
        <div class="action-title-group">
          <span class="page-title">
            <TeamOutlined /> 角色管理
          </span>
          <a-tag color="blue">共 {{ pagination.total }} 个角色</a-tag>
        </div>
        <a-button type="primary" size="large" class="add-btn" @click="handleAdd">
          <PlusOutlined /> 新增角色
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
          <template v-if="column.dataIndex === 'roleName'">
            <span class="role-name">
              <TeamOutlined style="color: var(--primary-color); margin-right: 6px" />
              {{ record.roleName }}
              <a-tag color="default" style="margin-left: 6px">{{ record.roleKey }}</a-tag>
            </span>
          </template>
          <template v-if="column.dataIndex === 'dataScope'">
            <a-tag :color="dataScopeColor(record.dataScope)">
              {{ scopeText(record.dataScope) }}
            </a-tag>
          </template>
          <template v-if="column.dataIndex === 'status'">
            <a-tag :color="record.status === 1 ? 'success' : 'default'">
              {{ record.status === 1 ? '启用' : '停用' }}
            </a-tag>
          </template>
          <template v-if="column.dataIndex === 'action'">
            <a-space :size="4">
              <a-button type="link" size="small" @click="handleEdit(record)">
                <EditOutlined /> 编辑
              </a-button>
              <a-button type="link" size="small" @click="handleMember(record)">
                <UsergroupAddOutlined /> 人员管理
              </a-button>
              <a-popconfirm
                title="确认删除该角色?"
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

    <a-modal
      v-model:open="modalVisible"
      :title="isEdit ? '编辑角色' : '新增角色'"
      :footer="null"
      :width="760"
      :destroy-on-close="true"
      class="form-modal"
    >
      <div class="modal-header">
        <div class="modal-header-icon"><TeamOutlined /></div>
        <div>
          <div class="modal-header-title">{{ isEdit ? '编辑角色' : '新增角色' }}</div>
          <div class="modal-header-subtitle">配置角色信息、数据范围和权限</div>
        </div>
      </div>
      <a-form :model="form" layout="vertical" :rules="rules" @finish="handleSubmit">
        <a-row :gutter="16">
          <a-col :span="12">
            <a-form-item label="角色名称" name="roleName">
              <a-input v-model:value="form.roleName" placeholder="例如：部门管理员" size="large" allow-clear />
            </a-form-item>
          </a-col>
          <a-col :span="12">
            <a-form-item label="角色标识" name="roleKey">
              <a-input v-model:value="form.roleKey" placeholder="例如：dept_manager" size="large" allow-clear />
            </a-form-item>
          </a-col>
          <a-col :span="12">
            <a-form-item label="数据范围">
              <a-select v-model:value="form.dataScope" size="large">
                <a-select-option :value="1">
                  <GlobalOutlined style="color: #1d4eff" /> 全部数据
                </a-select-option>
                <a-select-option :value="2">
                  <HomeOutlined style="color: #10b981" /> 本部门
                </a-select-option>
                <a-select-option :value="3">
                  <ClusterOutlined style="color: #06b6d4" /> 本部门及以下
                </a-select-option>
                <a-select-option :value="4">
                  <ApartmentOutlined style="color: #f59e0b" /> 自定义部门
                </a-select-option>
              </a-select>
            </a-form-item>
          </a-col>
          <a-col :span="12">
            <a-form-item label="排序">
              <a-input-number v-model:value="form.sort" :min="0" style="width: 100%" size="large" />
            </a-form-item>
          </a-col>
          <a-col :span="24">
            <a-form-item label="菜单权限">
              <!-- check-strictly 不能省：a-tree 默认父子联动，回填时会对传入的 key 做 conductCheck
                   向下填满整棵子树（vc-tree/Tree.js L198-206），用户点一下后又把**展开后的全量集合**
                   emit 回来（L612-651）。种子数据里 role 2 只装了「系统管理」目录（id=100）与 dept/dict，
                   不关联动就是：打开编辑→随手点一下→用户/角色/菜单管理连同按钮权限全部静默授予。
                   关掉后所见=所存；漏勾父目录也不影响侧边栏（getRouters 会自动补齐父菜单）。 -->
              <a-tree
                :checked-keys="checkedMenuKeys"
                :tree-data="menuTreeData"
                checkable
                check-strictly
                :field-names="{ title: 'label', key: 'id', children: 'children' }"
                tree-default-expand-all
                @check="onMenuCheck"
              />
            </a-form-item>
          </a-col>
          <a-col :span="24" v-if="form.dataScope === 4">
            <a-form-item label="可见部门（自定义）">
              <!-- 不用 tree-checkable：data_scope=4 在后端不展开子树（SysUserMapper.xml 该路 expand=FALSE），
                   父子联动的勾选框会显示出库里根本不存在的授权 -->
              <a-tree-select
                v-model:value="form.deptIds"
                :tree-data="deptTreeData"
                :field-names="{ label: 'label', value: 'id', children: 'children' }"
                multiple
                :max-tag-count="4"
                tree-node-filter-prop="label"
                allow-clear
                placeholder="请选择可见部门"
                style="width: 100%"
                size="large"
              />
            </a-form-item>
          </a-col>
          <a-col :span="24">
            <a-form-item label="备注">
              <a-textarea v-model:value="form.remark" :rows="2" placeholder="角色说明" :maxlength="200" show-count />
            </a-form-item>
          </a-col>
          <a-col :span="24">
            <a-form-item label="状态">
              <a-radio-group v-model:value="form.status" button-style="solid" size="large">
                <a-radio-button :value="1">启用</a-radio-button>
                <a-radio-button :value="0">停用</a-radio-button>
              </a-radio-group>
            </a-form-item>
          </a-col>
        </a-row>
        <div class="modal-footer">
          <a-button @click="modalVisible = false" size="large">取消</a-button>
          <a-button type="primary" html-type="submit" :loading="submitting" size="large">
            <SaveIcon v-if="isEdit" />
            <PlusIcon v-else />
            {{ isEdit ? '保存修改' : '确认添加' }}
          </a-button>
        </div>
      </a-form>
    </a-modal>

    <MemberModal v-model:open="memberVisible" :role="memberRole" />
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { message } from 'ant-design-vue'
import { PlusOutlined as PlusIcon, SaveOutlined as SaveIcon } from '@ant-design/icons-vue'
import { rolePage, roleAdd, roleUpdate, roleDelete, roleGet, roleMenuIds, roleDeptIds, type SysRole, type SysRoleDTO } from '@/api/role'
import { menuTree, type TreeNode } from '@/api/menu'
import { deptTree } from '@/api/dept'
import MemberModal from './MemberModal.vue'

const loading = ref(false)
const submitting = ref(false)
const tableData = ref<SysRole[]>([])
const modalVisible = ref(false)
const memberVisible = ref(false)
const memberRole = ref<SysRole | null>(null)
const isEdit = ref(false)
const roleName = ref('')
const menuTreeData = ref<TreeNode[]>([])
const checkedMenuKeys = ref<number[]>([])
const deptTreeData = ref<any[]>([])

const form = reactive<SysRoleDTO>({
  roleName: '', roleKey: '', dataScope: 2, sort: 0, status: 1, menuIds: [], deptIds: []
})

const rules = {
  roleName: [{ required: true, message: '请输入角色名称' }],
  roleKey: [{ required: true, message: '请输入角色标识' }],
}

const pagination = reactive({
  current: 1, pageSize: 10, total: 0, showSizeChanger: true,
  showTotal: (total: number) => `共 ${total} 个`,
})

const columns = [
  { title: '角色', dataIndex: 'roleName', width: 280 },
  { title: '数据范围', dataIndex: 'dataScope', width: 150 },
  { title: '排序', dataIndex: 'sort', width: 80 },
  { title: '状态', dataIndex: 'status', width: 100, align: 'center' as const },
  { title: '备注', dataIndex: 'remark', ellipsis: true },
  { title: '创建时间', dataIndex: 'createTime', width: 180 },
  { title: '操作', dataIndex: 'action', width: 250, fixed: 'right' as const },
]

const scopeMap: Record<number, { text: string; color: string }> = {
  1: { text: '全部', color: 'blue' },
  2: { text: '本部门', color: 'green' },
  3: { text: '本部门及以下', color: 'cyan' },
  4: { text: '自定义', color: 'orange' },
}
function scopeText(s: number) { return scopeMap[s]?.text || '-' }
function dataScopeColor(s: number) { return scopeMap[s]?.color || 'default' }

async function loadData() {
  loading.value = true
  try {
    const res = await rolePage({ current: pagination.current, size: pagination.pageSize, roleName: roleName.value || undefined })
    tableData.value = res.data.records
    pagination.total = res.data.total
  } finally { loading.value = false }
}

function handleTableChange(pag: any) { pagination.current = pag.current; pagination.pageSize = pag.pageSize; loadData() }

function handleAdd() {
  isEdit.value = false
  Object.assign(form, { id: undefined, roleName: '', roleKey: '', dataScope: 2, sort: 0, status: 1, remark: '', menuIds: [], deptIds: [] })
  checkedMenuKeys.value = []
  modalVisible.value = true
}

async function handleEdit(record: SysRole) {
  isEdit.value = true
  Object.assign(form, record)
  const [menuRes, deptRes] = await Promise.all([roleMenuIds(record.id), roleDeptIds(record.id)])
  checkedMenuKeys.value = menuRes.data
  form.deptIds = deptRes.data
  modalVisible.value = true
}

/**
 * check-strictly 下 a-tree 的 check 回调参数是 { checked, halfChecked } 对象（非联动时才是纯数组），
 * 这里归一成 number[]，所以不用 v-model:checked-keys，避免把对象写进 checkedMenuKeys
 */
function onMenuCheck(checked: any) {
  checkedMenuKeys.value = Array.isArray(checked) ? checked : (checked && checked.checked) || []
}

async function handleSubmit() {
  form.menuIds = checkedMenuKeys.value
  submitting.value = true
  try {
    if (isEdit.value) { await roleUpdate(form); message.success('修改成功') }
    else { await roleAdd(form); message.success('新增成功') }
    modalVisible.value = false
    loadData()
  } finally { submitting.value = false }
}

function handleMember(record: SysRole) {
  memberRole.value = record
  memberVisible.value = true
}

async function handleDelete(record: SysRole) {
  await roleDelete(record.id)
  message.success('删除成功')
  loadData()
}

onMounted(async () => {
  const [menuRes, deptRes] = await Promise.all([menuTree(), deptTree()])
  menuTreeData.value = menuRes.data
  deptTreeData.value = deptRes.data || []
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
:deep(.role-name) { font-weight: 500; }

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
