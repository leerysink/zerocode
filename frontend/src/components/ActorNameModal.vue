<script setup>
// 轻身份弹窗（规格 M3-a §6.2）：无密码、无邮箱，一个名字。cookie 即设备标识。
// get-container=false 内联渲染：不依赖 body portal，测试与嵌入页面都按组件树走。
import { ref, watch } from 'vue'

const props = defineProps({ open: Boolean })
const emit = defineEmits(['submit', 'update:open'])
const name = ref('')
const busy = ref(false)
watch(() => props.open, (v) => { if (v) name.value = '' })

function submit () {
  const v = name.value.trim()
  if (!v) return
  busy.value = true
  emit('submit', v)
  emit('update:open', false)
  busy.value = false
}
</script>

<template>
  <a-modal :open="open" :get-container="false" title="留下你的名字" :footer="null"
           @cancel="emit('update:open', false)">
    <div data-test="actor-modal">
      <p class="zc-actor-tip">商店用这个名字记录「谁申请的、这台设备是谁」，不需要密码。</p>
      <a-input v-model:value="name" data-test="actor-name" placeholder="昵称（1-50 字）" />
      <a-button type="primary" data-test="actor-submit" :loading="busy" @click="submit">进商店</a-button>
    </div>
  </a-modal>
</template>

<style scoped>
.zc-actor-tip { color: rgba(0, 0, 0, 0.55); font-size: 13px; }
</style>
