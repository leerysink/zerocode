<script setup>
// 思考内容折叠块：流式形态展开+“思考中…”动画；历史形态默认折叠（点击回看）
import { ref, watch } from 'vue'

const props = defineProps({
  thinking: { type: String, default: '' },
  streaming: { type: Boolean, default: false },
})
const open = ref(props.streaming)
watch(() => props.streaming, (v) => { if (v) open.value = true })
</script>

<template>
  <div class="zc-think">
    <button class="zc-think-head" type="button" @click="open = !open">
      <span v-if="streaming" class="zc-think-live">思考中…</span>
      <span v-else>查看思考</span>
    </button>
    <pre v-show="open" class="zc-think-body">{{ thinking }}</pre>
  </div>
</template>

<style scoped>
.zc-think {
  width: 100%;
  max-width: 640px;
  margin-bottom: 8px;
  border: 1px dashed var(--zc-border);
  border-radius: 8px;
  overflow: hidden;
}
.zc-think-head {
  width: 100%;
  text-align: left;
  border: none;
  background: rgba(91, 122, 157, 0.06);
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
  background: #fff;
}
@keyframes zc-think-blink {
  50% { opacity: 0.45; }
}
</style>
