<script setup>
// 上下文菜单：entries = [{label, icon, action, disabled, sep}]
import { onMounted, onBeforeUnmount, ref } from 'vue'

const props = defineProps({
  menu: { type: Object, required: true }, // { x, y, entries }
})
const emit = defineEmits(['close'])
const el = ref(null)

function pick(e) {
  if (e.sep || e.disabled) return
  emit('close')
  e.action?.()
}

function onDown(ev) {
  if (el.value && !el.value.contains(ev.target)) emit('close')
}
function onKey(ev) {
  if (ev.key === 'Escape') emit('close')
}
onMounted(() => {
  document.addEventListener('pointerdown', onDown, true)
  document.addEventListener('keydown', onKey)
})
onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', onDown, true)
  document.removeEventListener('keydown', onKey)
})

function style() {
  const m = props.menu
  const W = 232
  const H = Math.min(420, m.entries.length * 36 + 10)
  const x = Math.min(m.x, window.innerWidth - W - 6)
  const y = Math.min(m.y, Math.max(6, window.innerHeight - H - 6))
  return { left: `${x}px`, top: `${y}px`, width: `${W}px` }
}
</script>

<template>
  <div ref="el" class="ctx-menu acrylic" :style="style()" @click.stop @contextmenu.prevent>
    <template v-for="(e, i) in menu.entries" :key="i">
      <div v-if="e.sep" class="ctx-sep" />
      <div v-else class="ctx-item" :class="{ disabled: e.disabled }" @click="pick(e)">
        <span v-if="e.icon" class="ic" v-html="e.icon" />
        <span>{{ e.label }}</span>
        <span v-if="e.sub" class="ctx-sub">{{ e.sub }}</span>
      </div>
    </template>
  </div>
</template>

<style scoped>
.ic {
  display: inline-flex;
  width: 16px;
  height: 16px;
  color: var(--menu-fg);
}
.ic :deep(svg) {
  width: 16px;
  height: 16px;
}
</style>
