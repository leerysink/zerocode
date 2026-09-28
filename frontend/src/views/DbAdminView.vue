<script setup>
// 应用 DB 操作台（设计 2026-09-28）。骨架：顶栏 + dev/prod 切换 + prod 警示 + 左表树 + 结构页。
// 数据网格在 Task 10 填充，SQL 控制台/审计抽屉在 Task 11 填充。
import { onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { message } from 'ant-design-vue'
import { dbTables, dbTableSchema } from '../api'

const route = useRoute()
const appId = route.params.id
const env = ref('dev')
const tables = ref([])
const selected = ref(null)
const schema = ref(null)

async function loadTables() {
  try {
    tables.value = (await dbTables(appId, env.value)).data.tables
  } catch (e) {
    message.error(e?.response?.data?.detail || '表列表加载失败')
  }
}
async function selectTable(name) {
  selected.value = name
  try {
    schema.value = (await dbTableSchema(appId, env.value, name)).data
  } catch (e) {
    message.error(e?.response?.data?.detail || '表结构加载失败')
    schema.value = null
  }
}
watch(env, () => { selected.value = null; schema.value = null; loadTables() })
onMounted(loadTables)
defineExpose({ selected, schema, selectTable })
</script>

<template>
  <div class="zc-db">
    <div class="zc-db-head">
      <h2>DB 操作 · 应用 {{ appId }}</h2>
      <a-segmented v-model:value="env" class="zc-db-env" :options="[{ value: 'dev' }, { value: 'prod' }]" />
    </div>
    <a-alert v-if="env === 'prod'" class="zc-db-prod-warn" type="warning" show-icon
             message="直改 prod 会绕过发布迁移轨，请确认你知道自己在做什么" />
    <div class="zc-db-body">
      <div class="zc-db-left">
        <a-tree class="zc-db-tree" :tree-data="tables.map(t => ({ key: t.name, title: t.name }))"
                @select="(k) => k[0] && selectTable(k[0])" />
      </div>
      <div class="zc-db-right">
        <a-tabs>
          <a-tab-pane key="data" tab="数据">
            <span v-if="!selected">选择左侧表以查看数据</span>
            <!-- Task 10 填充数据网格 -->
          </a-tab-pane>
          <a-tab-pane key="schema" tab="结构">
            <a-table v-if="schema" size="small" rowKey="name" :dataSource="schema.columns"
                     :columns="[{ title: '列', dataIndex: 'name' }, { title: '类型', dataIndex: 'type' },
                               { title: '默认值', dataIndex: 'default' }]" />
            <div v-if="schema && schema.primary_key.length" class="zc-db-pk">主键：{{ schema.primary_key.join(', ') }}</div>
          </a-tab-pane>
          <a-tab-pane key="query" tab="查询"><!-- Task 11 填充 SQL 控制台 --></a-tab-pane>
        </a-tabs>
      </div>
    </div>
  </div>
</template>

<style scoped>
.zc-db { padding: 16px 20px; height: 100vh; display: flex; flex-direction: column; box-sizing: border-box; }
.zc-db-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.zc-db-head h2 { font-size: 18px; margin: 0; }
.zc-db-body { flex: 1; display: flex; gap: 12px; min-height: 0; }
.zc-db-left { width: 240px; overflow: auto; border: 1px solid #e5e7eb; border-radius: 6px; padding: 8px; }
.zc-db-right { flex: 1; overflow: auto; }
.zc-db-pk { margin-top: 8px; color: #6b7280; font-size: 12px; }
</style>
