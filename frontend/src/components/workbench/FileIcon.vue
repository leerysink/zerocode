<script setup>
// 自绘 SVG 类型图标（不引图标包）：形状按 fileKind 分四类——
// 文件夹、Vue 三角、Markdown 文档、图片山形，其余用带字母记号的圆角方块区分。
// 颜色一律取 fileTypes 的调色板，未知类型拿中性灰，树里不会出现空白图标。
import { computed } from 'vue'
import { fileKind, iconColor } from '../../utils/fileTypes'

const props = defineProps({
  path: { type: String, default: '' },
  dir: { type: Boolean, default: false },
})

const kind = computed(() => (props.dir ? 'folder' : fileKind(props.path)))
const color = computed(() => iconColor(kind.value))

const LABELS = {
  js: { text: 'JS', size: 6.4 },
  ts: { text: 'TS', size: 6.4 },
  json: { text: '{}', size: 6.4 },
  html: { text: '<>', size: 6.8 },
  css: { text: '#', size: 8 },
  py: { text: 'Py', size: 6.4 },
  java: { text: 'J', size: 8 },
  sql: { text: 'DB', size: 6.2 },
  yaml: { text: 'Y', size: 8 },
  shell: { text: '>_', size: 6 },
  bat: { text: 'B', size: 8 },
  docker: { text: 'D', size: 8 },
  xml: { text: '</>', size: 5.2 },
  ini: { text: 'IN', size: 5.8 },
}

const label = computed(() => LABELS[kind.value] || LABELS.file || { text: '', size: 0 })
const docKinds = new Set(['md', 'file'])
</script>

<template>
  <svg class="zc-fi" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" :style="{ color }">
    <path
      v-if="kind === 'folder'"
      d="M1.5 4.1c0-.5.4-.9.9-.9h3.1l1.5 1.6h6.6c.5 0 .9.4.9.9v6.8c0 .5-.4.9-.9.9H2.4a.9.9 0 0 1-.9-.9z"
      fill="currentColor"
    />
    <template v-else-if="kind === 'vue'">
      <path d="M1.2 3.2h3.5L8 8.1l3.3-4.9h3.5L8 14.3z" fill="currentColor" />
      <path d="M4.7 3.2h2.1L8 5.5l1.2-2.3h2.1L8 8.6z" fill="var(--zc-sidebar-bg, #252526)" />
    </template>
    <template v-else-if="kind === 'image'">
      <rect x="1.5" y="2.5" width="13" height="11" rx="2" fill="currentColor" opacity=".18" />
      <rect x="1.5" y="2.5" width="13" height="11" rx="2" fill="none" stroke="currentColor" stroke-width="1" />
      <circle cx="5.6" cy="6.2" r="1.2" fill="currentColor" />
      <path d="M3 12l3.4-3.6L9 11l1.9-2 2.6 3z" fill="currentColor" />
    </template>
    <template v-else-if="docKinds.has(kind)">
      <path
        d="M3 1.8h6l4 4v8.4H3z"
        fill="currentColor"
        opacity=".16"
        stroke="currentColor"
        stroke-width="1"
        stroke-linejoin="round"
      />
      <path d="M9 1.8v4h4" fill="none" stroke="currentColor" stroke-width="1" />
      <path d="M5 9h6M5 11.4h4" stroke="currentColor" stroke-width="1.1" stroke-linecap="round" />
    </template>
    <template v-else>
      <rect x="1.5" y="1.5" width="13" height="13" rx="3" fill="currentColor" opacity=".16" />
      <rect
        x="1.5"
        y="1.5"
        width="13"
        height="13"
        rx="3"
        fill="none"
        stroke="currentColor"
        stroke-width="1"
      />
      <text
        x="8"
        y="8.3"
        text-anchor="middle"
        dominant-baseline="central"
        :font-size="label.size"
        font-weight="700"
        font-family="Consolas, 'Courier New', monospace"
        fill="currentColor"
      >{{ label.text }}</text>
    </template>
  </svg>
</template>

<style scoped>
.zc-fi {
  flex: none;
  display: inline-block;
  vertical-align: middle;
}
</style>
