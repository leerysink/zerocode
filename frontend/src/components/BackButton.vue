<script setup>
// 页头「返回」按钮：应用页/设置页共用。语义固定为回对话页，不走浏览器历史——
// 直接输入 /apps 或 /settings 时 history 为空，router.back() 会点了没反应。
import { useRouter } from 'vue-router'
import { ArrowLeftOutlined } from '@ant-design/icons-vue'

const props = defineProps({
  to: { type: String, default: '/' },
})
const router = useRouter()
</script>

<template>
  <a-button class="zc-back" type="link" @click="router.push(props.to)">
    <arrow-left-outlined /> 返回
  </a-button>
</template>

<style scoped>
.zc-back {
  padding: 0 4px;
  font-size: 14px;
  color: var(--zc-text-sub);
}
.zc-back:hover {
  color: var(--zc-primary);
}
</style>
