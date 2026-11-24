<script setup>
// 卡片封面截图轮播薄件：多图每 3s 自动切下一张并循环，悬停暂停、移开恢复。
// 单图与 reduce 动效用户静止只出首张——封面轮播是锦上添花，不该跟系统偏好较劲。
// 只管播图，不取数、不含业务判断；无图时的占位仍由调用点的 v-else 负责。
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps({
  shots: { type: Array, required: true },
})

const idx = ref(0)
let timer = null
let reduced = false

const multi = computed(() => props.shots.length > 1)

function stop () {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}
function start () {
  stop()
  if (!multi.value || reduced) return
  timer = setInterval(() => {
    idx.value = (idx.value + 1) % props.shots.length
  }, 3000)
}
const pause = stop
const resume = start

// 补传/删除截图后刷新列表会换一份数组：回到首张，并按新图数重起节奏
watch(() => props.shots, () => {
  idx.value = 0
  start()
})

onMounted(() => {
  reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  // 预载全部截图，切换时不会闪白
  props.shots.forEach((src) => { new Image().src = src })
  start()
})
onBeforeUnmount(stop)
</script>

<template>
  <div class="zc-shot-carousel" data-test="shot-carousel"
       @mouseenter="pause" @mouseleave="resume">
    <transition name="zc-shot-fade">
      <img :key="shots[idx]" :src="shots[idx]" class="zc-cover-img" alt="" />
    </transition>
    <span v-if="multi" class="zc-shot-dots" aria-hidden="true">
      <i v-for="(s, i) in shots" :key="i" :class="{ on: i === idx }" />
    </span>
  </div>
</template>

<style scoped>
/* 铺满卡片封面位（父容器已给 aspect-ratio 与 overflow: hidden），叠放让淡入淡出能交叉 */
.zc-shot-carousel { position: absolute; inset: 0; }
.zc-shot-carousel img { position: absolute; inset: 0; width: 100%; height: 100%;
  object-fit: cover; }
.zc-shot-fade-enter-active, .zc-shot-fade-leave-active { transition: opacity 0.4s ease; }
.zc-shot-fade-enter-from, .zc-shot-fade-leave-to { opacity: 0; }
/* 指示点只是状态提示：不吃点击，封面点击仍归外层卡片 */
.zc-shot-dots { position: absolute; left: 50%; bottom: 8px; transform: translateX(-50%);
  display: inline-flex; gap: 6px; pointer-events: none; }
.zc-shot-dots i { width: 6px; height: 6px; border-radius: 50%;
  background: rgba(255, 255, 255, 0.55); box-shadow: 0 0 2px rgba(0, 0, 0, 0.35); }
.zc-shot-dots i.on { background: #fff; }
@media (prefers-reduced-motion: reduce) {
  .zc-shot-fade-enter-active, .zc-shot-fade-leave-active { transition: none; }
}
</style>
