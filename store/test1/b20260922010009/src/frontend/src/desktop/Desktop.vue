<script setup>
// 桌面主容器：壁纸 / 桌面图标（拖拽+框选） / 窗口层 / 任务栏 / 开始菜单 / 右键菜单 / 关机锁屏
import { ref, computed, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { useSettings } from '../stores/settings'
import { useWinStore } from '../stores/windows'
import { useIcons } from '../stores/icons'
import { desktopApps, WALLPAPERS, appOf } from './registry/apps'
import { ui, logo } from '../assets/icons'
import Window from './Window.vue'
import Taskbar from './Taskbar.vue'
import StartMenu from './StartMenu.vue'
import QuickPanel from './QuickPanel.vue'
import ContextMenu from './ContextMenu.vue'

const st = useSettings()
const wm = useWinStore()
const icons = useIcons()

const panel = ref('') // '' | start | quick | notif
const ctx = ref(null)
const boot = ref(true)
const power = ref('') // '' | sleep | lock | shutdown | restart
const band = ref(null)

const wallpaperStyle = computed(() => ({
  background: WALLPAPERS[st.cfg.wallpaper]?.css || WALLPAPERS.bloom.css,
}))

const apps = computed(() => desktopApps())

function closePanels() {
  panel.value = ''
  ctx.value = null
}

function togglePanel(name) {
  panel.value = panel.value === name ? '' : name
  ctx.value = null
}

// ---------- 桌面图标拖拽 ----------
const drag = ref(null)

function iconDown(e, app) {
  if (e.button !== 0) return
  e.stopPropagation()
  const pos = icons.positions[app.id]
  if (!e.shiftKey) icons.select([app.id])
  else icons.select(Array.from(new Set([...icons.selected, app.id])), true)
  const startX = e.clientX
  const startY = e.clientY
  const origin = { ...pos }
  const picked = icons.selected.slice()
  const origins = Object.fromEntries(picked.map((id) => [id, { ...icons.positions[id] }]))
  let moved = false
  const move = (ev) => {
    const dx = ev.clientX - startX
    const dy = ev.clientY - startY
    if (!moved && Math.hypot(dx, dy) < 4) return
    moved = true
    drag.value = true
    picked.forEach((id) => {
      const o = origins[id]
      icons.preview(id, { x: o.x + dx, y: o.y + dy })
    })
  }
  const up = () => {
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', up)
    drag.value = null
    if (!moved) return
    picked.forEach((id) => icons.commit(id))
  }
  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', up)
}

function iconDbl(e, app) {
  e.stopPropagation()
  icons.clear()
  wm.open(app.id)
}

// ---------- 框选 ----------
function deskDown(e) {
  if (e.button !== 0) return
  if (e.target.closest('.window') || e.target.closest('.desk-icon')) return
  closePanels()
  icons.clear()
  const layer = e.currentTarget.getBoundingClientRect()
  const sx = e.clientX - layer.left
  const sy = e.clientY - layer.top
  band.value = { x: sx, y: sy, w: 0, h: 0 }
  const move = (ev) => {
    const cx = ev.clientX - layer.left
    const cy = ev.clientY - layer.top
    band.value = {
      x: Math.min(sx, cx), y: Math.min(sy, cy), w: Math.abs(cx - sx), h: Math.abs(cy - sy),
    }
    const ids = apps.value.filter((a) => {
      const p = icons.positions[a.id]
      return p.x + 88 > band.value.x && p.x < band.value.x + band.value.w &&
             p.y + 96 > band.value.y && p.y < band.value.y + band.value.h
    }).map((a) => a.id)
    icons.select(ids)
  }
  const up = () => {
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', up)
    band.value = null
  }
  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', up)
}

// ---------- 桌面右键菜单 ----------
function deskContext(e) {
  if (e.target.closest('.window') || e.target.closest('.taskbar')) return
  e.preventDefault()
  closePanels()
  const onIcon = e.target.closest('.desk-icon')
  const appId = onIcon?.dataset?.app
  ctx.value = {
    x: e.clientX,
    y: e.clientY,
    entries: appId ? [
      { label: '打开', icon: ui.grid, action: () => wm.open(appId) },
      { label: '固定到任务栏', icon: ui.pin, disabled: !!appOf(appId).pinned, action: () => uiTip('该应用已在任务栏') },
      { sep: true },
      { label: '重命名', icon: ui.copy, disabled: true, action: () => {} },
      { label: '项目属性', icon: ui.info, action: () => uiTip(`${appOf(appId).name}（演示）`) },
      { sep: true },
      { label: '删除', icon: ui.trash, disabled: true, action: () => {} },
    ] : [
      { label: '查看', icon: ui.list, sub: '›', disabled: true, action: () => {} },
      { label: '排序方式', icon: ui.grid, sub: '›', disabled: true, action: () => {} },
      { label: '刷新', icon: ui.refresh, action: () => window.dispatchEvent(new Event('resize')) },
      { sep: true },
      { label: '新建文件夹', icon: ui.folder, disabled: true, action: () => {} },
      { label: '自动排列图标', icon: ui.grid, action: () => icons.autoArrange() },
      { sep: true },
      {
        label: st.cfg.theme === 'dark' ? '切换到浅色模式' : '切换到深色模式',
        icon: st.cfg.theme === 'dark' ? ui.sun : ui.moon,
        action: () => st.patch({ theme: st.cfg.theme === 'dark' ? 'light' : 'dark' }),
      },
      { label: '下一页壁纸', icon: ui.palette, action: nextWallpaper },
      { label: '个性化设置', icon: ui.settings, action: () => wm.open('settings') },
      { sep: true },
      { label: '显示桌面设置', icon: ui.monitor, action: () => wm.open('settings') },
    ],
  }
}

function nextWallpaper() {
  const keys = Object.keys(WALLPAPERS)
  const i = keys.indexOf(st.cfg.wallpaper)
  st.patch({ wallpaper: keys[(i + 1) % keys.length] })
}

let tipTimer = null
const tip = ref('')
function uiTip(text) {
  tip.value = text
  clearTimeout(tipTimer)
  tipTimer = setTimeout(() => (tip.value = ''), 2600)
}

// ---------- 电源 / 锁屏 ----------
function onPower(e) {
  const mode = e.detail
  power.value = mode
  closePanels()
  if (mode === 'shutdown' || mode === 'restart') {
    setTimeout(() => {
      wm.wins.splice(0, wm.wins.length)
      if (mode === 'restart') {
        boot.value = true
        power.value = ''
        setTimeout(() => (boot.value = false), 1800)
      } else {
        power.value = 'off'
      }
    }, 2600)
  }
}

function unlock() {
  power.value = ''
}

function wake() {
  if (power.value === 'sleep') power.value = ''
}

function powerOn() {
  if (power.value !== 'off') return
  boot.value = true
  power.value = ''
  setTimeout(() => (boot.value = false), 1800)
}

// ---------- 快捷键 ----------
function onKey(e) {
  const tag = (e.target?.tagName || '').toLowerCase()
  const typing = tag === 'input' || tag === 'textarea' || e.target?.isContentEditable
  if (e.key === 'Meta') {
    togglePanel('start')
    return
  }
  if (e.ctrlKey && e.key === 'Escape') {
    if (typing) return // Ctrl+E 是资源管理器快捷键，不劫持
    togglePanel('start')
    return
  }
  if (e.key === 'Escape') {
    if (typing) return
    if (power.value === 'lock') unlock()
    else closePanels()
    return
  }
  if (e.altKey && e.key === 'F4' && wm.activeId) {
    e.preventDefault()
    wm.close(wm.activeId)
  }
  if (e.key === 'd' && e.metaKey) {
    e.preventDefault()
    wm.minimizeAll()
  }
}

function onDocClick(e) {
  if (e.target.closest('.start-menu') || e.target.closest('.flyout') ||
      e.target.closest('.taskbar') || e.target.closest('.ctx-menu')) return
  panel.value = ''
  ctx.value = null
}

function onResize() {
  wm.clampAll()
}

onMounted(async () => {
  await st.load()
  document.addEventListener('pointerdown', onDocClick, true)
  document.addEventListener('contextmenu', deskContext)
  window.addEventListener('desktop-power', onPower)
  window.addEventListener('keydown', onKey)
  window.addEventListener('resize', onResize)
  await nextTick()
  setTimeout(() => {
    boot.value = false
    if (!sessionStorage.getItem('zr-welcomed')) {
      sessionStorage.setItem('zr-welcomed', '1')
      wm.open('welcome')
    }
  }, 900)
})

onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', onDocClick, true)
  document.removeEventListener('contextmenu', deskContext)
  window.removeEventListener('desktop-power', onPower)
  window.removeEventListener('keydown', onKey)
  window.removeEventListener('resize', onResize)
  st.flush()
})

const powerLabel = computed(() => ({
  sleep: '正在进入睡眠…', lock: '', restart: '正在重新启动…', shutdown: '正在关机…', off: '',
}[power.value] || ''))

const clockNow = ref(new Date())
let clockTimer = null
onMounted(() => { clockTimer = setInterval(() => (clockNow.value = new Date()), 20000) })
onBeforeUnmount(() => clearInterval(clockTimer))
const lockTime = computed(() => clockNow.value.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false }))
const lockDate = computed(() => clockNow.value.toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric', weekday: 'long' }))
</script>

<template>
  <div class="desktop">
    <div class="wallpaper" :style="wallpaperStyle" />

    <!-- 锁屏 -->
    <div v-if="power === 'lock'" class="screen-cover" @click="unlock">
      <div class="big">{{ lockTime }}</div>
      <div style="font-size: 15px; opacity: .8">{{ lockDate }}</div>
      <div style="margin-top: 44px; font-size: 13px; opacity: .75">点击任意位置或按 Esc 键解锁</div>
      <div class="sm-lock"><span class="ic" v-html="ui.pin" /> 陌如玉 · 本地账户</div>
    </div>

    <!-- 睡眠 -->
    <div v-else-if="power === 'sleep'" class="screen-cover dim" @click="wake">
      <div class="big">{{ lockTime }}</div>
      <div style="font-size: 13px; opacity: .7">点击任意位置唤醒</div>
    </div>

    <!-- 已关机 -->
    <div v-else-if="power === 'off'" class="screen-cover black">
      <button class="power-off-btn" @click="powerOn">
        <span class="ic" v-html="ui.power" />
        <div>开机</div>
      </button>
    </div>

    <!-- 开机 / 关机 -->
    <div v-else-if="power" class="screen-cover">
      <div class="win-spin" />
      <div>{{ powerLabel }}</div>
    </div>

    <div v-else class="desktop-stage" @pointerdown="deskDown">
      <!-- 图标层 -->
      <div v-if="st.cfg.showDesktopIcons" class="desk-icons">
        <div v-for="a in apps" :key="a.id" class="desk-icon"
             :class="{ selected: icons.selected.includes(a.id) }" :data-app="a.id"
             :style="{ left: icons.positions[a.id]?.x + 'px', top: icons.positions[a.id]?.y + 'px' }"
             @pointerdown="iconDown($event, a)" @dblclick="iconDbl($event, a)"
             @contextmenu.stop.prevent="deskContext($event)">
          <span class="glyph" v-html="a.icon" />
          <span class="label">{{ a.short }}</span>
        </div>
        <div v-if="band" class="rubber-band"
             :style="{ left: band.x + 'px', top: band.y + 'px', width: band.w + 'px', height: band.h + 'px' }" />
      </div>

      <!-- 窗口层 -->
      <Window v-for="w in wm.visibleWins" :key="w.winId" :win="w" />

      <!-- 提示条 -->
      <transition name="fade">
        <div v-if="tip" class="toast acrylic">{{ tip }}</div>
      </transition>
    </div>

    <Taskbar v-if="power === ''" :start-open="panel === 'start'"
             @open-start="togglePanel('start')" @open-quick="togglePanel('quick')"
             @open-notif="togglePanel('notif')" />

    <StartMenu v-if="panel === 'start'" @close="panel = ''" />
    <QuickPanel v-if="panel === 'quick'" mode="quick" @close="panel = ''" />
    <QuickPanel v-if="panel === 'notif'" mode="notif" @close="panel = ''" />
    <ContextMenu v-if="ctx" :menu="ctx" @close="ctx = null" />

    <!-- 首次启动动画 -->
    <transition name="boot">
      <div v-if="boot" class="boot">
        <span class="boot-logo" v-html="logo.windows" />
        <div class="win-spin" />
      </div>
    </transition>
  </div>
</template>

<style scoped>
.screen-cover.dim {
  background: #000;
}
.screen-cover.black {
  background: #000;
}
.power-off-btn {
  border: 1px solid rgba(255, 255, 255, 0.35);
  background: rgba(255, 255, 255, 0.08);
  color: #fff;
  border-radius: 10px;
  padding: 22px 34px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  font-size: 14px;
  cursor: pointer;
}
.power-off-btn:hover {
  background: rgba(255, 255, 255, 0.16);
  border-color: rgba(255, 255, 255, 0.6);
}
.power-off-btn .ic {
  width: 26px;
  height: 26px;
}
.power-off-btn .ic :deep(svg) {
  width: 26px;
  height: 26px;
}
.sm-lock {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 26px;
  padding: 8px 18px;
  border-radius: 20px;
  background: rgba(255, 255, 255, 0.12);
  font-size: 13px;
}
.ic {
  display: inline-flex;
  width: 16px;
  height: 16px;
}
.ic :deep(svg) {
  width: 16px;
  height: 16px;
}
.toast {
  position: absolute;
  left: 50%;
  bottom: 24px;
  transform: translateX(-50%);
  padding: 10px 18px;
  font-size: 13px;
  z-index: 9600;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
  transform: translate(-50%, 10px);
}
.boot {
  position: absolute;
  inset: 0;
  background: #000;
  z-index: 99999;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 34px;
  color: #fff;
}
.boot-logo {
  width: 76px;
  height: 76px;
  color: #4cc3ff;
}
.boot-logo :deep(svg) {
  width: 76px;
  height: 76px;
}
.boot-enter-active,
.boot-leave-active {
  transition: opacity 0.5s ease;
}
.boot-enter-from,
.boot-leave-to {
  opacity: 0;
}
.desk-icons {
  position: absolute;
  inset: 0;
}
.glyph {
  display: inline-flex;
  color: #fff;
}
.desk-icon .glyph,
.desk-icon .glyph :deep(svg) {
  width: 40px;
  height: 40px;
}
</style>
