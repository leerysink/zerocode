<script setup>
// 应用骨架首页：纯前端，数据落在浏览器 localStorage。AI 在此基础上实现功能。
// 本栈没有后端——要持久化到服务端得先换技术栈，别在这里写「假装能连库」的代码。
import { onMounted, ref } from 'vue'

const KEY = '{{SLUG}}.notes'
const notes = ref([])
const title = ref('')

function load() {
  try {
    notes.value = JSON.parse(localStorage.getItem(KEY) || '[]')
  } catch {
    notes.value = []      // 手改过存储内容时按空列表起步，不让首页白屏
  }
}
function save() {
  localStorage.setItem(KEY, JSON.stringify(notes.value))
}
function add() {
  const text = title.value.trim()
  if (!text) return
  notes.value = [...notes.value, { title: text, at: new Date().toISOString() }]
  title.value = ''
  save()
}
function remove(at) {
  notes.value = notes.value.filter((_, i) => i !== at)
  save()
}
function exportJson() {
  const blob = new Blob([JSON.stringify(notes.value, null, 2)], { type: 'application/json' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = '{{SLUG}}-notes.json'
  a.click()
  URL.revokeObjectURL(a.href)
}
onMounted(load)
</script>

<template>
  <a-card title="{{APP_NAME}}" style="max-width: 720px; margin: 40px auto">
    <a-typography-paragraph type="secondary">
      纯前端静态页：数据保存在本机浏览器（localStorage），换设备或清缓存即丢。
    </a-typography-paragraph>
    <a-space-compact style="display:flex; gap:8px; margin-bottom:16px">
      <a-input v-model:value="title" placeholder="记点什么…" style="width: 300px" @pressEnter="add" />
      <a-button type="primary" @click="add">添加</a-button>
      <a-button @click="exportJson">导出 JSON</a-button>
    </a-space-compact>
    <a-list :data-source="notes" bordered>
      <template #renderItem="{ item, index }">
        <a-list-item>
          <span v-text="item.title"></span>
          <template #actions><a @click="remove(index)">删除</a></template>
        </a-list-item>
      </template>
    </a-list>
  </a-card>
</template>
