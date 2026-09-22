<template>
  <div>
    <a-card :bordered="false" class="page-card">
      <!-- 搜索栏 -->
      <div class="search-bar">
        <a-input v-model:value="query.title" placeholder="模块标题" allow-clear style="width: 160px" />
        <a-select v-model:value="query.businessType" placeholder="业务类型" allow-clear style="width: 120px">
          <a-select-option v-for="(label, key) in businessTypeMap" :key="key" :value="Number(key)">{{ label }}</a-select-option>
        </a-select>
        <a-select v-model:value="query.status" placeholder="操作状态" allow-clear style="width: 120px">
          <a-select-option :value="1">成功</a-select-option>
          <a-select-option :value="0">失败</a-select-option>
        </a-select>
        <a-input v-model:value="query.operUserName" placeholder="操作人（昵称/账号）" allow-clear style="width: 160px" />
        <a-range-picker
          v-model:value="query.timeRange"
          :show-time="{ format: 'HH:mm:ss' }"
          format="YYYY-MM-DD HH:mm:ss"
          :placeholder="['开始时间', '结束时间']"
          @change="onTimeChange"
        />
        <a-button type="primary" @click="loadData">
          <SearchOutlined /> 查询
        </a-button>
        <a-button @click="resetQuery">重置</a-button>
      </div>

      <!-- 操作栏 -->
      <div class="action-bar">
        <div class="action-title-group">
          <span class="page-title">
            <ProfileOutlined /> 操作日志
          </span>
          <a-tag color="blue">共 {{ pagination.total }} 条记录</a-tag>
        </div>
        <a-popconfirm
          title="确认清空所有操作日志? 此操作不可恢复!"
          ok-text="确定"
          cancel-text="取消"
          @confirm="handleClean"
        >
          <a-button danger size="large" class="clean-btn">
            <ClearOutlined /> 清空日志
          </a-button>
        </a-popconfirm>
      </div>

      <!-- 表格 -->
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
          <template v-if="column.dataIndex === 'title'">
            <span class="title-cell">{{ record.title }}</span>
          </template>
          <template v-if="column.dataIndex === 'businessType'">
            <a-tag :color="businessTypeColorMap[record.businessType] || 'default'">
              {{ businessTypeMap[record.businessType] || '其他' }}
            </a-tag>
          </template>
          <template v-if="column.dataIndex === 'requestMethod'">
            <a-tag :color="methodColor(record.requestMethod)">{{ record.requestMethod }}</a-tag>
          </template>
          <template v-if="column.dataIndex === 'status'">
            <a-tag :color="record.status === 1 ? 'success' : 'error'">
              {{ record.status === 1 ? '成功' : '失败' }}
            </a-tag>
          </template>
          <template v-if="column.dataIndex === 'operIp'">
            <span class="mono-text">{{ record.operIp }}</span>
          </template>
          <template v-if="column.dataIndex === 'costTime'">
            <span :class="record.costTime > 1000 ? 'cost-slow' : 'cost-fast'">
              {{ record.costTime }} ms
            </span>
          </template>
          <template v-if="column.dataIndex === 'action'">
            <a-space :size="4">
              <a-button type="link" size="small" @click="handleDetail(record)">
                <EyeOutlined /> 详情
              </a-button>
              <a-popconfirm
                title="确认删除该日志?"
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

    <!-- 详情弹窗 -->
    <a-modal
      v-model:open="detailVisible"
      title="操作日志详情"
      :footer="null"
      :width="780"
      :destroy-on-close="true"
    >
      <a-descriptions :column="2" bordered size="small" v-if="currentLog">
        <a-descriptions-item label="模块标题">{{ currentLog.title }}</a-descriptions-item>
        <a-descriptions-item label="业务类型">
          <a-tag :color="businessTypeColorMap[currentLog.businessType]">
            {{ businessTypeMap[currentLog.businessType] || '其他' }}
          </a-tag>
        </a-descriptions-item>
        <a-descriptions-item label="请求方式">
          <a-tag :color="methodColor(currentLog.requestMethod)">{{ currentLog.requestMethod }}</a-tag>
        </a-descriptions-item>
        <a-descriptions-item label="操作状态">
          <a-tag :color="currentLog.status === 1 ? 'success' : 'error'">
            {{ currentLog.status === 1 ? '成功' : '失败' }}
          </a-tag>
        </a-descriptions-item>
        <a-descriptions-item label="操作人">{{ currentLog.operUserName }}</a-descriptions-item>
        <a-descriptions-item label="耗时">{{ currentLog.costTime }} ms</a-descriptions-item>
        <a-descriptions-item label="操作IP">{{ currentLog.operIp }}</a-descriptions-item>
        <a-descriptions-item label="操作地点">{{ currentLog.operLocation }}</a-descriptions-item>
        <a-descriptions-item label="请求URL" :span="2">
          <span class="mono-text">{{ currentLog.operUrl }}</span>
        </a-descriptions-item>
        <a-descriptions-item label="方法名称" :span="2">
          <span class="mono-text">{{ currentLog.method }}</span>
        </a-descriptions-item>
        <a-descriptions-item label="操作时间" :span="2">{{ currentLog.createTime }}</a-descriptions-item>
        <a-descriptions-item label="请求参数" :span="2">
          <pre class="json-block">{{ formatJson(currentLog.operParam) }}</pre>
        </a-descriptions-item>
        <a-descriptions-item v-if="currentLog.status === 1" label="返回结果" :span="2">
          <pre class="json-block">{{ formatJson(currentLog.jsonResult) }}</pre>
        </a-descriptions-item>
        <a-descriptions-item v-if="currentLog.status === 0" label="错误消息" :span="2">
          <span class="error-msg">{{ currentLog.errorMsg }}</span>
        </a-descriptions-item>
      </a-descriptions>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { message } from 'ant-design-vue'
import type { Dayjs } from 'dayjs'
import {
  operLogPage,
  operLogDelete,
  operLogClean,
  businessTypeMap,
  businessTypeColorMap,
  type SysOperLog,
} from '@/api/operLog'

const loading = ref(false)
const tableData = ref<SysOperLog[]>([])
const detailVisible = ref(false)
const currentLog = ref<SysOperLog | null>(null)

const query = reactive({
  title: '',
  businessType: undefined as number | undefined,
  status: undefined as number | undefined,
  operUserName: '',
  timeRange: undefined as [Dayjs, Dayjs] | undefined,
  beginTime: '',
  endTime: '',
})

const pagination = reactive({
  current: 1,
  pageSize: 10,
  total: 0,
  showSizeChanger: true,
  showTotal: (total: number) => `共 ${total} 个`,
})

const columns = [
  { title: '模块', dataIndex: 'title', width: 120 },
  { title: '业务类型', dataIndex: 'businessType', width: 100, align: 'center' as const },
  { title: '请求方式', dataIndex: 'requestMethod', width: 100, align: 'center' as const },
  { title: '操作人', dataIndex: 'operUserName', width: 110 },
  { title: 'IP地址', dataIndex: 'operIp', width: 130 },
  { title: '操作地点', dataIndex: 'operLocation', width: 100 },
  { title: '状态', dataIndex: 'status', width: 80, align: 'center' as const },
  { title: '耗时', dataIndex: 'costTime', width: 100, align: 'center' as const },
  { title: '操作时间', dataIndex: 'createTime', width: 180 },
  { title: '操作', dataIndex: 'action', width: 140, fixed: 'right' as const },
]

function onTimeChange(_dates: any, formatStrings: [string, string]) {
  query.beginTime = formatStrings[0] || ''
  query.endTime = formatStrings[1] || ''
}

function methodColor(method: string) {
  const map: Record<string, string> = {
    GET: 'blue',
    POST: 'green',
    PUT: 'orange',
    DELETE: 'red',
  }
  return map[method] || 'default'
}

function formatJson(str: string) {
  if (!str) return '-'
  try {
    return JSON.stringify(JSON.parse(str), null, 2)
  } catch {
    return str
  }
}

async function loadData() {
  loading.value = true
  try {
    const res = await operLogPage({
      current: pagination.current,
      size: pagination.pageSize,
      title: query.title || undefined,
      businessType: query.businessType,
      status: query.status,
      operUserName: query.operUserName || undefined,
      beginTime: query.beginTime || undefined,
      endTime: query.endTime || undefined,
    })
    tableData.value = res.data.records
    pagination.total = res.data.total
  } finally {
    loading.value = false
  }
}

function resetQuery() {
  query.title = ''
  query.businessType = undefined
  query.status = undefined
  query.operUserName = ''
  query.timeRange = undefined
  query.beginTime = ''
  query.endTime = ''
  pagination.current = 1
  loadData()
}

function handleTableChange(pag: any) {
  pagination.current = pag.current
  pagination.pageSize = pag.pageSize
  loadData()
}

function handleDetail(record: SysOperLog) {
  currentLog.value = record
  detailVisible.value = true
}

async function handleDelete(record: SysOperLog) {
  await operLogDelete(record.id)
  message.success('删除成功')
  loadData()
}

async function handleClean() {
  await operLogClean()
  message.success('清空成功')
  loadData()
}

onMounted(() => {
  loadData()
})
</script>

<style scoped>
.page-card {
  background: #fff !important;
  border-radius: var(--radius-md) !important;
}
.search-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  padding: 16px 20px;
  background: linear-gradient(to right, #f8fafc 0%, #ffffff 100%);
  border-radius: var(--radius-md);
  margin-bottom: 16px;
  border: 1px solid var(--border-color);
}
.action-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
  padding: 0 4px;
}
.action-title-group {
  display: flex;
  align-items: center;
  gap: 8px;
}
.page-title {
  font-size: 18px;
  font-weight: 600;
  color: var(--text-primary);
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.page-title :deep(.anticon) {
  color: var(--primary-color);
  font-size: 20px;
}
.clean-btn {
  margin-left: auto;
}
.title-cell {
  font-weight: 500;
  color: var(--text-primary);
}
.mono-text {
  font-family: 'SF Mono', 'Consolas', monospace;
  font-size: 13px;
  color: var(--text-secondary);
}
.cost-fast {
  color: #52c41a;
  font-size: 13px;
}
.cost-slow {
  color: #ff4d4f;
  font-size: 13px;
}
.json-block {
  background: #f5f7fa;
  padding: 12px;
  border-radius: 6px;
  font-family: 'SF Mono', 'Consolas', monospace;
  font-size: 12px;
  max-height: 300px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
  margin: 0;
}
.error-msg {
  color: #ff4d4f;
  font-family: 'SF Mono', monospace;
  font-size: 13px;
}
</style>
