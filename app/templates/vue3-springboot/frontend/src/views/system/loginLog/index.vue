<template>
  <div>
    <a-card :bordered="false" class="page-card">
      <!-- 搜索栏 -->
      <div class="search-bar">
        <a-input v-model:value="query.userName" placeholder="登录账号" allow-clear style="width: 150px" />
        <a-select v-model:value="query.loginType" placeholder="日志类型" allow-clear style="width: 120px">
          <a-select-option :value="1">登录</a-select-option>
          <a-select-option :value="2">登出</a-select-option>
        </a-select>
        <a-select v-model:value="query.status" placeholder="登录状态" allow-clear style="width: 120px">
          <a-select-option :value="1">成功</a-select-option>
          <a-select-option :value="0">失败</a-select-option>
        </a-select>
        <a-input v-model:value="query.ipaddr" placeholder="IP地址" allow-clear style="width: 150px" />
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
            <LoginOutlined /> 登录日志
          </span>
          <a-tag color="blue">共 {{ pagination.total }} 条记录</a-tag>
        </div>
        <a-popconfirm
          title="确认清空所有登录日志? 此操作不可恢复!"
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
          <template v-if="column.dataIndex === 'userName'">
            <a-avatar class="user-avatar">{{ record.userName?.charAt(0)?.toUpperCase() }}</a-avatar>
            <span class="username-cell">{{ record.userName }}</span>
          </template>
          <template v-if="column.dataIndex === 'loginType'">
            <a-tag :color="record.loginType === 1 ? 'green' : 'orange'">
              {{ record.loginType === 1 ? '登录' : '登出' }}
            </a-tag>
          </template>
          <template v-if="column.dataIndex === 'ipaddr'">
            <span class="mono-text">{{ record.ipaddr }}</span>
          </template>
          <template v-if="column.dataIndex === 'browser'">
            <span>{{ record.browser || '-' }}</span>
          </template>
          <template v-if="column.dataIndex === 'os'">
            <span>{{ record.os || '-' }}</span>
          </template>
          <template v-if="column.dataIndex === 'status'">
            <a-tag :color="record.status === 1 ? 'success' : 'error'">
              {{ record.status === 1 ? '成功' : '失败' }}
            </a-tag>
          </template>
          <template v-if="column.dataIndex === 'msg'">
            <span :class="record.status === 0 ? 'error-msg' : 'success-msg'">{{ record.msg }}</span>
          </template>
          <template v-if="column.dataIndex === 'action'">
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
          </template>
        </template>
      </a-table>
    </a-card>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { message } from 'ant-design-vue'
import type { Dayjs } from 'dayjs'
import { loginLogPage, loginLogDelete, loginLogClean, type SysLoginLog } from '@/api/loginLog'

const loading = ref(false)
const tableData = ref<SysLoginLog[]>([])

const query = reactive({
  userName: '',
  loginType: undefined as number | undefined,
  status: undefined as number | undefined,
  ipaddr: '',
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
  { title: '登录账号', dataIndex: 'userName', width: 160 },
  { title: 'IP地址', dataIndex: 'ipaddr', width: 140 },
  { title: '登录地点', dataIndex: 'loginLocation', width: 100 },
  { title: '浏览器', dataIndex: 'browser', width: 110 },
  { title: '操作系统', dataIndex: 'os', width: 130 },
  { title: '日志类型', dataIndex: 'loginType', width: 90, align: 'center' as const },
  { title: '状态', dataIndex: 'status', width: 80, align: 'center' as const },
  { title: '消息', dataIndex: 'msg', ellipsis: true },
  { title: '登录时间', dataIndex: 'loginTime', width: 180 },
  { title: '操作', dataIndex: 'action', width: 90, fixed: 'right' as const },
]

function onTimeChange(_dates: any, formatStrings: [string, string]) {
  query.beginTime = formatStrings[0] || ''
  query.endTime = formatStrings[1] || ''
}

async function loadData() {
  loading.value = true
  try {
    const res = await loginLogPage({
      current: pagination.current,
      size: pagination.pageSize,
      userName: query.userName || undefined,
      loginType: query.loginType,
      status: query.status,
      ipaddr: query.ipaddr || undefined,
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
  query.userName = ''
  query.loginType = undefined
  query.status = undefined
  query.ipaddr = ''
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

async function handleDelete(record: SysLoginLog) {
  await loginLogDelete(record.id)
  message.success('删除成功')
  loadData()
}

async function handleClean() {
  await loginLogClean()
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
:deep(.user-avatar) {
  background: var(--primary-gradient) !important;
  color: #fff !important;
  font-weight: 600;
  margin-right: 8px;
  width: 26px;
  height: 26px;
  font-size: 12px;
  line-height: 26px;
}
.username-cell {
  font-weight: 500;
  color: var(--text-primary);
}
.mono-text {
  font-family: 'SF Mono', 'Consolas', monospace;
  font-size: 13px;
  color: var(--text-secondary);
}
.error-msg {
  color: #ff4d4f;
  font-size: 13px;
}
.success-msg {
  color: var(--text-secondary);
  font-size: 13px;
}
</style>
