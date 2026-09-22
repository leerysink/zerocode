<template>
  <div>
    <a-card :bordered="false" class="page-card">
      <div class="search-bar">
        <a-input v-model:value="query.deptName" placeholder="部门名称" allow-clear style="width: 200px" />
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
            <ApartmentOutlined /> 部门管理
          </span>
          <a-tag color="blue">共 {{ flatCount }} 个</a-tag>
        </div>
        <a-button type="primary" size="large" class="add-btn" @click="handleAdd">
          <PlusOutlined /> 新增部门
        </a-button>
      </div>

      <a-table
        bordered
        size="small"
        :columns="columns"
        :data-source="tableData"
        :loading="loading"
        :pagination="false"
        row-key="id"
        v-model:expanded-row-keys="expandedRowKeys"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.dataIndex === 'deptName'">
            <span class="dept-name">
              <ApartmentOutlined style="color: var(--primary-color); margin-right: 6px" />
              {{ record.deptName }}
            </span>
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
              <a-popconfirm
                :title="`确认删除部门 ${record.deptName} ?`"
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
      :title="isEdit ? '编辑部门' : '新增部门'"
      :footer="null"
      :width="560"
      :destroy-on-close="true"
      class="form-modal"
    >
      <div class="modal-header">
        <div class="modal-header-icon"><ApartmentOutlined /></div>
        <div>
          <div class="modal-header-title">{{ isEdit ? '编辑部门' : '新增部门' }}</div>
          <div class="modal-header-subtitle">{{ isEdit ? '修改部门基础信息与上级归属' : '在指定上级部门下添加子部门' }}</div>
        </div>
      </div>
      <a-form :model="form" layout="vertical" :rules="rules" @finish="handleSubmit">
        <a-form-item label="上级部门" name="parentId">
          <a-tree-select
            v-model:value="form.parentId"
            :tree-data="parentTreeData"
            :field-names="{ label: 'label', value: 'id', children: 'children' }"
            tree-node-filter-prop="label"
            allow-clear
            placeholder="不选表示顶级部门"
            size="large"
            style="width: 100%"
          />
        </a-form-item>
        <a-form-item label="部门名称" name="deptName">
          <a-input v-model:value="form.deptName" placeholder="例如：研发中心" size="large" allow-clear />
        </a-form-item>
        <a-row :gutter="16">
          <a-col :span="12">
            <a-form-item label="显示排序" name="orderNum">
              <a-input-number v-model:value="form.orderNum" :min="0" size="large" style="width: 100%" />
            </a-form-item>
          </a-col>
          <a-col :span="12">
            <a-form-item label="负责人">
              <a-input v-model:value="form.leader" placeholder="例如：张三" size="large" allow-clear />
            </a-form-item>
          </a-col>
        </a-row>
        <a-form-item label="联系电话">
          <a-input v-model:value="form.phone" placeholder="例如：010-88880001" size="large" allow-clear>
            <template #prefix><PhoneOutlined /></template>
          </a-input>
        </a-form-item>
        <a-form-item label="状态">
          <a-radio-group v-model:value="form.status" button-style="solid" size="large">
            <a-radio-button :value="1">正常</a-radio-button>
            <a-radio-button :value="0">停用</a-radio-button>
          </a-radio-group>
        </a-form-item>
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
import { ref, reactive, computed, onMounted } from 'vue'
import { message } from 'ant-design-vue'
import {
  ApartmentOutlined,
  DeleteOutlined,
  EditOutlined,
  PhoneOutlined,
  PlusOutlined,
  SaveOutlined,
  SearchOutlined,
} from '@ant-design/icons-vue'
import {
  deptList,
  deptTree,
  deptAdd,
  deptUpdate,
  deptDelete,
  type SysDept,
  type SysDeptDTO,
  type DeptTreeNode,
} from '@/api/dept'

const loading = ref(false)
const submitting = ref(false)
const modalVisible = ref(false)
const isEdit = ref(false)
const tableData = ref<SysDept[]>([])
const treeData = ref<DeptTreeNode[]>([])

const query = reactive<{ deptName: string; status?: number }>({ deptName: '', status: undefined })

const form = reactive<SysDeptDTO>({
  id: undefined,
  parentId: 0,
  deptName: '',
  orderNum: 0,
  leader: '',
  phone: '',
  status: 1,
})

const rules = {
  deptName: [{ required: true, message: '请输入部门名称', trigger: 'blur' }],
}

const columns = [
  { title: '部门名称', dataIndex: 'deptName', width: 240 },
  { title: '排序', dataIndex: 'orderNum', width: 80 },
  { title: '负责人', dataIndex: 'leader', width: 120 },
  { title: '联系电话', dataIndex: 'phone', width: 150 },
  { title: '状态', dataIndex: 'status', width: 90 },
  { title: '创建时间', dataIndex: 'createTime', width: 170 },
  { title: '操作', dataIndex: 'action', width: 150, fixed: 'right' },
]

/** 部门总数：必须数扁平结果，tableData 已被组装成树，取 length 只会得到顶层节点数 */
const flatCount = ref(0)

/**
 * 受控展开。`default-expand-all-rows` 在本页行不通：vc-table 用一个 watchEffect 算出展开集
 * 然后立即 stop()（Table.js L90-98），“default 只第一次生效”，而数据是 onMounted
 * 异步到达的，首帧 mergedData 为空 → 树永远全收起。
 */
const expandedRowKeys = ref<number[]>([])

/** 上级部门候选：编辑时排除自己与自己的子树 */
const parentTreeData = computed(() => {
  if (!form.id) return treeData.value
  return filterOutSubtree(treeData.value, form.id)
})

function filterOutSubtree(nodes: DeptTreeNode[], excludeId: number): DeptTreeNode[] {
  return nodes
    .filter((n) => n.id !== excludeId)
    .map((n) => ({ ...n, children: n.children ? filterOutSubtree(n.children, excludeId) : [] }))
}

async function loadData() {
  loading.value = true
  try {
    const res = await deptList({ deptName: query.deptName || undefined, status: query.status })
    const flat = res.data || []
    flatCount.value = flat.length
    tableData.value = buildNested(flat)
    // 默认展开全部：搜索后只剩命中路径上的节点，铺开才能看到层级而不用逐个点
    expandedRowKeys.value = flat.map((d) => d.id)
  } finally {
    loading.value = false
  }
}

/** 列表接口返回扁平数据，按 parentId 组装 a-table 需要的 children 结构 */
type DeptRow = SysDept & { children?: DeptRow[] }

function buildNested(list: SysDept[]): DeptRow[] {
  const byId = new Map<number, DeptRow>()
  list.forEach((d) => byId.set(d.id, { ...d, children: [] }))
  const roots: DeptRow[] = []
  byId.forEach((node) => {
    const parent = node.parentId ? byId.get(node.parentId) : undefined
    if (parent) {
      parent.children!.push(node)
    } else {
      roots.push(node)
    }
  })
  // 必须剥掉空 children：a-table 判行可展开用的是**真值**而不是长度
  // （vc-table BodyRow.js：hasNestChildren = record[childrenColumnName]，空数组也是 truthy），
  // 不剥则每个末级部门都带一个点了没反应的 + 号（菜单页 cleanup 同一处理）
  dropEmptyChildren(roots)
  return roots
}

function dropEmptyChildren(nodes: DeptRow[]) {
  for (const node of nodes) {
    if (node.children && node.children.length > 0) {
      dropEmptyChildren(node.children)
    } else {
      delete node.children
    }
  }
}

async function loadTree() {
  const res = await deptTree()
  treeData.value = res.data || []
}

function resetQuery() {
  query.deptName = ''
  query.status = undefined
  loadData()
}

function resetForm() {
  Object.assign(form, {
    id: undefined,
    parentId: 0,
    deptName: '',
    orderNum: 0,
    leader: '',
    phone: '',
    status: 1,
  })
}

function handleAdd() {
  resetForm()
  isEdit.value = false
  modalVisible.value = true
}

function handleEdit(record: SysDept) {
  Object.assign(form, {
    id: record.id,
    parentId: record.parentId || 0,
    deptName: record.deptName,
    orderNum: record.orderNum,
    leader: record.leader || '',
    phone: record.phone || '',
    status: record.status,
  })
  isEdit.value = true
  modalVisible.value = true
}

async function handleSubmit() {
  submitting.value = true
  try {
    const payload = { ...form, parentId: form.parentId ?? 0 }
    if (isEdit.value) {
      await deptUpdate(payload)
      message.success('修改成功')
    } else {
      await deptAdd(payload)
      message.success('新增成功')
    }
    modalVisible.value = false
    await Promise.all([loadData(), loadTree()])
  } finally {
    submitting.value = false
  }
}

async function handleDelete(record: SysDept) {
  await deptDelete(record.id)
  message.success('删除成功')
  await Promise.all([loadData(), loadTree()])
}

onMounted(() => {
  loadData()
  loadTree()
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
:deep(.dept-name) { font-weight: 500; }

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
