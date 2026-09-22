<script setup>
// 单个窗口：标题栏拖拽移动、八向缩放、最小化/最大化/关闭、点击聚焦置顶
import { computed, ref } from 'vue'
import { useWinStore } from '../stores/windows'
import { appOf } from './registry/apps'
import { ui, logo } from '../assets/icons'

const props = defineProps({ win: { type: Object, required: true } })
const wm = useWinStore()

const app = computed(() => appOf(props.win.appId))
const active = computed(() => wm.activeId === props.win.winId)
const cls = computed(() => [
  'window',
  active.value ? 'active' : 'inactive',
  props.win.maximized ? 'max' : '',
  'opening',
])

function startDrag(e) {
  if (e.button !== 0) return
  const w = props.win
  wm.focus(w.winId)
  if (w.maximized) return
  const sx = e.clientX - w.x
  const sy = e.clientY - w.y
  const move = (ev) => {
    const maxX = window.innerWidth - 60
    const maxY = window.innerHeight - 48 - 20
    w.x = Math.min(Math.max(-w.w + 80, ev.clientX - sx), maxX)
    w.y = Math.min(Math.max(0, ev.clientY - sy), maxY)
  }
  const up = () => {
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', up)
  }
  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', up)
}

function startResize(e, dir) {
  if (e.button !== 0 || props.win.maximized) return
  e.stopPropagation()
  const w = props.win
  wm.focus(w.winId)
  const s = { x: e.clientX, y: e.clientY, wx: w.x, wy: w.y, ww: w.w, wh: w.h }
  const maxW = window.innerWidth
  const maxH = window.innerHeight - 48
  const move = (ev) => {
    const dx = ev.clientX - s.x
    const dy = ev.clientY - s.y
    if (dir.includes('e')) w.w = clamp(s.ww + dx, w.minW, maxW - s.wx)
    if (dir.includes('s')) w.h = clamp(s.wh + dy, w.minH, maxH - s.wy)
    if (dir.includes('w')) {
      const nw = clamp(s.ww - dx, w.minW, s.wx + s.ww)
      w.x = s.wx + s.ww - nw
      w.w = nw
    }
    if (dir.includes('n')) {
      const nh = clamp(s.wh - dy, w.minH, s.wy + s.wh)
      w.y = s.wy + s.wh - nh
      w.h = nh
    }
  }
  const up = () => {
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', up)
  }
  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', up)
}

const clamp = (v, lo, hi) => Math.min(Math.max(lo, v), hi)

const style = computed(() => ({
  left: `${props.win.x}px`,
  top: `${props.win.y}px`,
  width: `${props.win.w}px`,
  height: `${props.win.h}px`,
  zIndex: props.win.z,
  display: props.win.minimized ? 'none' : 'flex',
}))
</script>

<template>
  <div :class="cls" :style="style" @pointerdown="wm.focus(win.winId)">
    <div class="win-titlebar" @pointerdown="startDrag"
         @dblclick="wm.toggleMax(win.winId)">
      <div class="win-drag">
        <span class="t-glyph" v-html="app.icon" />
        <span class="win-title">{{ win.title }}</span>
      </div>
      <div class="win-btns" @pointerdown.stop>
        <button class="win-btn" title="最小化" @click="wm.minimize(win.winId)" v-html="ui.min" />
        <button class="win-btn" :title="win.maximized ? '向下还原' : '最大化'"
                @click="wm.toggleMax(win.winId)" v-html="win.maximized ? ui.restore : ui.max" />
        <button class="win-btn close" title="关闭" @click="wm.close(win.winId)" v-html="ui.close" />
      </div>
    </div>

    <div class="win-body">
      <component :is="app.comp" :win="win" :app="app" />
    </div>

    <template v-if="!win.maximized">
      <div class="rs rs-n" @pointerdown="startResize($event, 'n')" />
      <div class="rs rs-s" @pointerdown="startResize($event, 's')" />
      <div class="rs rs-e" @pointerdown="startResize($event, 'e')" />
      <div class="rs rs-w" @pointerdown="startResize($event, 'w')" />
      <div class="rs rs-ne" @pointerdown="startResize($event, 'ne')" />
      <div class="rs rs-nw" @pointerdown="startResize($event, 'nw')" />
      <div class="rs rs-se" @pointerdown="startResize($event, 'se')" />
      <div class="rs rs-sw" @pointerdown="startResize($event, 'sw')" />
    </template>
  </div>
</template>

<style scoped>
.win-btns .win-btn:first-child {
  margin-left: auto;
}
.t-glyph :deep(svg) {
  width: 16px;
  height: 16px;
}
</style>
