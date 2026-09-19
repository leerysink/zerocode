<script setup>
// 应用骨架首页：展示后端健康与示例数据，AI 在此基础上实现功能
import { onMounted, ref } from 'vue'
import axios from 'axios'

const health = ref('检测中…')
const notes = ref([])
const title = ref('')

async function load() {
  try {
    const h = await axios.get('/api/health')
    health.value = h.data.db ? '后端正常，数据库已连接' : '后端正常，数据库未连接'
    notes.value = (await axios.get('/api/notes')).data
  } catch {
    health.value = '后端未就绪（dev 环境启动中或代码有误）'
  }
}
async function add() {
  if (!title.value.trim()) return
  await axios.post('/api/notes', { title: title.value.trim() })
  title.value = ''
  await load()
}
onMounted(load)
</script>

<template>
  <a-card :title="'{{APP_NAME}}'" style="max-width: 720px; margin: 40px auto">
    <a-typography-paragraph><span v-text="health"></span></a-typography-paragraph>
    <a-space-compact style="display:flex; gap:8px; margin-bottom:16px">
      <a-input v-model:value="title" placeholder="记点什么…" style="width: 300px" @pressEnter="add" />
      <a-button type="primary" @click="add">添加</a-button>
    </a-space-compact>
    <a-list :data-source="notes" bordered>
      <template #renderItem="{ item }">
        <a-list-item><span v-text="item.title"></span></a-list-item>
      </template>
    </a-list>
  </a-card>
</template>
