<script setup>
// 思考内容折叠块：流式与历史都默认折叠（流式只显「思考中…」），点开才看正文。
// 展开态归父层所有：`open` 只是初值，点击后由内部持有并回吐 update:open，
// 收口时父层把它接到固化后的历史块上，正在读的人才不会被突然折叠打断（ADR-0065）。
import { ref, watch } from 'vue'

const props = defineProps({
  thinking: { type: String, default: '' },
  streaming: { type: Boolean, default: false },
  open: { type: Boolean, default: false },
})
const emit = defineEmits(['update:open'])

const expanded = ref(props.open)
watch(() => props.open, (v) => { expanded.value = v })
function toggle() {
  expanded.value = !expanded.value
  emit('update:open', expanded.value)
}
</script>

<template>
  <div class="zc-think">
    <button class="zc-think-head" type="button" @click="toggle">
      <span v-if="streaming" class="zc-think-live">思考中…</span>
      <span v-else>查看思考</span>
    </button>
    <pre v-show="expanded" class="zc-think-body">{{ thinking }}</pre>
  </div>
</template>

<style scoped>
.zc-think {
  width: 100%;
  margin-bottom: 8px;
  border: 1px dashed var(--zc-border);
  border-radius: 8px;
  overflow: hidden;
}
.zc-think-head {
  width: 100%;
  text-align: left;
  border: none;
  background: rgba(var(--zc-primary-rgb), 0.06);
  color: var(--zc-text-sub);
  font-size: 12px;
  padding: 6px 10px;
  cursor: pointer;
}
.zc-think-live {
  animation: zc-think-blink 1.4s ease-in-out infinite;
}
.zc-think-body {
  margin: 0;
  max-height: 220px;
  overflow: auto;
  padding: 8px 10px;
  font-family: inherit;
  font-size: 12.5px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--zc-text-sub);
  background: var(--zc-bg-card);
}
@keyframes zc-think-blink {
  50% { opacity: 0.45; }
}
</style>
