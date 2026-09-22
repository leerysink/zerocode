<template>
  <div>
    <a-row :gutter="16" class="page-row">
      <!-- 左侧: 字典类型 -->
      <a-col :span="8">
        <a-card :bordered="false" class="page-card" title="字典类型">
          <template #extra>
            <a-button type="primary" size="small" @click="handleAddType"><PlusOutlined /> 新增</a-button>
          </template>
          <div class="search-bar" style="margin-bottom:12px">
            <a-input v-model:value="typeSearch" placeholder="搜索字典名称" allow-clear size="small" style="width:100%" />
          </div>
          <a-list :data-source="filteredTypes" size="small" :loading="typeLoading">
            <template #renderItem="{ item }">
              <a-list-item
                :class="['type-item', { active: selectedType?.id === item.id }]"
                @click="selectType(item)"
              >
                <a-list-item-meta>
                  <template #title>{{ item.dictName }}</template>
                  <template #description><a-tag>{{ item.dictType }}</a-tag></template>
                </a-list-item-meta>
                <template #actions>
                  <a-button type="link" size="small" @click.stop="handleEditType(item)"><EditOutlined /></a-button>
                  <a-popconfirm title="确认删除?" @confirm="handleDeleteType(item)" ok-text="确定" cancel-text="取消">
                    <a-button type="link" size="small" danger @click.stop><DeleteOutlined /></a-button>
                  </a-popconfirm>
                </template>
              </a-list-item>
            </template>
          </a-list>
        </a-card>
      </a-col>
      <!-- 右侧: 字典项 -->
      <a-col :span="16">
        <a-card :bordered="false" class="page-card">
          <template #title>
            <span v-if="selectedType">{{ selectedType.dictName }} <a-tag color="blue">{{ selectedType.dictType }}</a-tag></span>
            <span v-else style="color:var(--text-muted)">请从左侧选择字典类型</span>
          </template>
          <template #extra>
            <a-button type="primary" size="small" :disabled="!selectedType" @click="handleAddData"><PlusOutlined /> 新增字典项</a-button>
          </template>
          <a-table
            bordered
            size="small"
            :columns="dataColumns"
            :data-source="dataList"
            :loading="dataLoading"
            row-key="id"
            :pagination="false"
          >
            <template #bodyCell="{ column, record }">
              <template v-if="column.dataIndex === 'status'">
                <a-tag :color="record.status === 1 ? 'success' : 'default'">{{ record.status === 1 ? '启用' : '停用' }}</a-tag>
              </template>
              <template v-if="column.dataIndex === 'action'">
                <a-space :size="4">
                  <a-button type="link" size="small" @click="handleEditData(record)"><EditOutlined /> 编辑</a-button>
                  <a-popconfirm title="确认删除?" @confirm="handleDeleteData(record)" ok-text="确定" cancel-text="取消">
                    <a-button type="link" size="small" danger><DeleteOutlined /> 删除</a-button>
                  </a-popconfirm>
                </a-space>
              </template>
            </template>
          </a-table>
        </a-card>
      </a-col>
    </a-row>

    <!-- 字典类型弹窗 -->
    <a-modal v-model:open="typeModalVisible" :title="isEditType ? '编辑字典类型' : '新增字典类型'" :footer="null" :width="480" :destroy-on-close="true">
      <a-form :model="typeForm" layout="vertical" :rules="typeRules" @finish="handleTypeSubmit">
        <a-form-item label="字典名称" name="dictName">
          <a-input v-model:value="typeForm.dictName" placeholder="例如：职务" allow-clear />
        </a-form-item>
        <a-form-item label="字典类型编码" name="dictType">
          <a-input v-model:value="typeForm.dictType" placeholder="例如：position" allow-clear :disabled="isEditType" />
        </a-form-item>
        <a-form-item label="状态">
          <a-radio-group v-model:value="typeForm.status" button-style="solid">
            <a-radio-button :value="1">正常</a-radio-button>
            <a-radio-button :value="0">停用</a-radio-button>
          </a-radio-group>
        </a-form-item>
        <a-form-item label="备注">
          <a-textarea v-model:value="typeForm.remark" :rows="2" placeholder="备注" :maxlength="200" show-count />
        </a-form-item>
        <div class="modal-footer">
          <a-button @click="typeModalVisible = false">取消</a-button>
          <a-button type="primary" html-type="submit" :loading="typeSubmitting">{{ isEditType ? '保存' : '添加' }}</a-button>
        </div>
      </a-form>
    </a-modal>

    <!-- 字典项弹窗 -->
    <a-modal v-model:open="dataModalVisible" :title="isEditData ? '编辑字典项' : '新增字典项'" :footer="null" :width="480" :destroy-on-close="true">
      <a-form :model="dataForm" layout="vertical" :rules="dataRules" @finish="handleDataSubmit">
        <a-form-item label="字典标签" name="dictLabel">
          <a-input v-model:value="dataForm.dictLabel" placeholder="显示文本，如：院长" allow-clear />
        </a-form-item>
        <a-form-item label="字典值" name="dictValue">
          <a-input v-model:value="dataForm.dictValue" placeholder="存储值，如：dean" allow-clear />
        </a-form-item>
        <a-row :gutter="16">
          <a-col :span="12">
            <a-form-item label="排序">
              <a-input-number v-model:value="dataForm.sort" :min="0" style="width:100%" />
            </a-form-item>
          </a-col>
          <a-col :span="12">
            <a-form-item label="状态">
              <a-radio-group v-model:value="dataForm.status" button-style="solid">
                <a-radio-button :value="1">启用</a-radio-button>
                <a-radio-button :value="0">停用</a-radio-button>
              </a-radio-group>
            </a-form-item>
          </a-col>
        </a-row>
        <a-form-item label="备注">
          <a-textarea v-model:value="dataForm.remark" :rows="2" placeholder="备注" :maxlength="200" show-count />
        </a-form-item>
        <div class="modal-footer">
          <a-button @click="dataModalVisible = false">取消</a-button>
          <a-button type="primary" html-type="submit" :loading="dataSubmitting">{{ isEditData ? '保存' : '添加' }}</a-button>
        </div>
      </a-form>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { message } from 'ant-design-vue'
import { PlusOutlined, EditOutlined, DeleteOutlined } from '@ant-design/icons-vue'
import {
  dictTypeList, dictTypeAdd, dictTypeUpdate, dictTypeDelete,
  dictDataList, dictDataAdd, dictDataUpdate, dictDataDelete,
  type DictType, type DictTypeDTO, type DictData, type DictDataDTO,
} from '@/api/dict'

// === 字典类型 ===
const typeLoading = ref(false)
const typeSubmitting = ref(false)
const types = ref<DictType[]>([])
const typeSearch = ref('')
const selectedType = ref<DictType | null>(null)
const typeModalVisible = ref(false)
const isEditType = ref(false)
const typeForm = reactive<DictTypeDTO>({ dictName: '', dictType: '', status: 1 })
const typeRules = {
  dictName: [{ required: true, message: '请输入字典名称' }],
  dictType: [{ required: true, message: '请输入字典类型编码' }],
}

const filteredTypes = computed(() => {
  if (!typeSearch.value) return types.value
  return types.value.filter(t => t.dictName.includes(typeSearch.value))
})

async function loadTypes() {
  typeLoading.value = true
  try {
    const res = await dictTypeList()
    types.value = res.data
    if (selectedType.value) {
      selectedType.value = types.value.find(t => t.id === selectedType.value!.id) || null
    }
    if (!selectedType.value && types.value.length > 0) {
      selectType(types.value[0])
    }
  } finally { typeLoading.value = false }
}

function selectType(item: DictType) {
  selectedType.value = item
  loadDataList()
}

function handleAddType() {
  isEditType.value = false
  Object.assign(typeForm, { id: undefined, dictName: '', dictType: '', status: 1, remark: '' })
  typeModalVisible.value = true
}

function handleEditType(item: DictType) {
  isEditType.value = true
  Object.assign(typeForm, item)
  typeModalVisible.value = true
}

async function handleTypeSubmit() {
  typeSubmitting.value = true
  try {
    if (isEditType.value) { await dictTypeUpdate(typeForm); message.success('修改成功') }
    else { await dictTypeAdd(typeForm); message.success('新增成功') }
    typeModalVisible.value = false
    await loadTypes()
  } finally { typeSubmitting.value = false }
}

async function handleDeleteType(item: DictType) {
  await dictTypeDelete(item.id)
  message.success('删除成功')
  if (selectedType.value?.id === item.id) { selectedType.value = null; dataList.value = [] }
  loadTypes()
}

// === 字典数据 ===
const dataLoading = ref(false)
const dataSubmitting = ref(false)
const dataList = ref<DictData[]>([])
const dataModalVisible = ref(false)
const isEditData = ref(false)
const dataForm = reactive<DictDataDTO>({ dictType: '', dictLabel: '', dictValue: '', sort: 0, status: 1 })
const dataRules = {
  dictLabel: [{ required: true, message: '请输入字典标签' }],
  dictValue: [{ required: true, message: '请输入字典值' }],
}

const dataColumns = [
  { title: '标签', dataIndex: 'dictLabel', width: 160 },
  { title: '值', dataIndex: 'dictValue', width: 120 },
  { title: '排序', dataIndex: 'sort', width: 80, align: 'center' as const },
  { title: '状态', dataIndex: 'status', width: 90, align: 'center' as const },
  { title: '备注', dataIndex: 'remark', ellipsis: true },
  { title: '操作', dataIndex: 'action', width: 160, fixed: 'right' as const },
]

async function loadDataList() {
  if (!selectedType.value) return
  dataLoading.value = true
  try {
    const res = await dictDataList(selectedType.value.dictType)
    dataList.value = res.data
  } finally { dataLoading.value = false }
}

function handleAddData() {
  isEditData.value = false
  Object.assign(dataForm, { id: undefined, dictType: selectedType.value!.dictType, dictLabel: '', dictValue: '', sort: 0, status: 1, remark: '' })
  dataModalVisible.value = true
}

function handleEditData(record: DictData) {
  isEditData.value = true
  Object.assign(dataForm, record)
  dataModalVisible.value = true
}

async function handleDataSubmit() {
  dataSubmitting.value = true
  try {
    if (isEditData.value) { await dictDataUpdate(dataForm); message.success('修改成功') }
    else { await dictDataAdd(dataForm); message.success('新增成功') }
    dataModalVisible.value = false
    loadDataList()
  } finally { dataSubmitting.value = false }
}

async function handleDeleteData(record: DictData) {
  await dictDataDelete(record.id)
  message.success('删除成功')
  loadDataList()
}

onMounted(loadTypes)
</script>

<style scoped>
.page-card { background: #fff !important; border-radius: var(--radius-md) !important; }
.search-bar { display: flex; gap: 12px; align-items: center; }
/* 左侧字典类型列表：占据卡片剩余高度并内部滚动 */
:deep(.ant-list) {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
}
:deep(.type-item) { cursor: pointer; border-radius: var(--radius-sm); transition: all 0.2s; padding: 8px 12px !important; }
:deep(.type-item:hover) { background: #f0f5ff; }
:deep(.type-item.active) { background: linear-gradient(135deg, #eff6ff 0%, #dbeafe 100%); border-left: 3px solid var(--primary-color); }
.modal-footer { display: flex; justify-content: flex-end; gap: 12px; padding-top: 16px; margin-top: 16px; border-top: 1px solid var(--border-color); }
</style>
