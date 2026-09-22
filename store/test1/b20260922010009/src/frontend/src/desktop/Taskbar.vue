<script setup>
// 任务栏：开始按钮、搜索、应用按钮（运行指示）、系统托盘、时钟、显示桌面
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useWinStore } from '../stores/windows'
import { useSettings } from '../stores/settings'
import { appOf, pinnedApps } from './registry/apps'
import { ui, logo } from '../assets/icons'
import ContextMenu from './ContextMenu.vue'

const emit = defineEmits(['open-start', 'open-quick', 'open-notif'])
const wm = useWinStore()
const st = useSettings()

const props = defineProps({ startOpen: { type: Boolean, default: false } })

const pinned = pinnedApps()
const trayMenu = ref(null)

const now = ref(new Date())
let timer = null
onMounted(() => {
  timer = setInterval(() => (now.value = new Date()), 10 * 1000)
})
onBeforeUnmount(() => clearInterval(timer))

const timeStr = computed(() =>
  now.value.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false }))
const dateStr = computed(() => now.value.toLocaleDateString('zh-CN', {
  year: 'numeric', month: '2-digit', day: '2-digit',
}))

const items = computed(() => {
  const ids = new Set(pinned.map((a) => a.id))
  for (const w of wm.wins) ids.add(w.appId)
  return [...ids].map((id) => {
    const app = appOf(id)
    const wins = wm.groups.get(id) || []
    return {
      app,
      wins,
      running: wins.length > 0,
      active: wins.some((w) => w.winId === wm.activeId && !w.minimized),
    }
  })
})

function onAppClick(item) {
  if (!item.running) return wm.open(item.app.id)
  wm.taskbarClick(item.app.id)
}

function onAppContext(item, e) {
  const wins = item.wins
  const entries = [
    {
      label: wins.length ? `${item.app.short}（${wins.length} 个窗口）` : '打开',
      icon: ui.grid,
      action: () => (wins.length ? wm.focus(wins[wins.length - 1].winId) : wm.open(item.app.id)),
    },
    { sep: true },
  ]
  if (wins.length) {
    entries.push({ label: '全部关闭', icon: ui.close, action: () => wm.closeApp(item.app.id) })
  } else {
    entries.push({ label: '从任务栏取消固定', icon: ui.pin, disabled: true, action: () => {} })
  }
  trayMenu.value = { x: e.clientX, y: e.clientY, entries }
}

function showDesktop() {
  wm.minimizeAll()
}
</script>

<template>
  <ContextMenu v-if="trayMenu" :menu="trayMenu" @close="trayMenu = null" />

  <div class="taskbar" :class="st.cfg.taskbarAlign === 'left' ? 'left-bar' : 'center'">
    <div class="tb-left">
      <button v-if="st.cfg.taskbarAlign === 'left'" class="tb-btn" :class="{ active: startOpen }"
              data-tip="开始" @click.stop="emit('open-start')">
        <span class="glyph win" v-html="logo.windows" />
        <span class="dot" />
      </button>
    </div>

    <div class="tb-center">
      <button v-if="st.cfg.taskbarAlign !== 'left'" class="tb-btn" :class="{ active: startOpen }"
              data-tip="开始" @click.stop="emit('open-start')">
        <span class="glyph win" v-html="logo.windows" />
        <span class="dot" />
      </button>
      <button class="tb-btn tb-search" data-tip="搜索" @click.stop="emit('open-start')">
        <span class="glyph" v-html="ui.search" /> 搜索
      </button>
      <button v-for="item in items" :key="item.app.id" class="tb-btn"
              :class="{ active: item.active, multi: item.wins.length > 1 }"
              :data-tip="item.app.short" @click="onAppClick(item)" @contextmenu.prevent="onAppContext(item, $event)">
        <span class="glyph" v-html="item.app.icon" />
        <span v-if="item.running" class="dot" />
      </button>
    </div>

    <div class="tb-tray">
      <button class="tb-btn" data-tip="网络连接" @click.stop="emit('open-quick')">
        <span class="glyph" v-html="ui.wifi" />
      </button>
      <button class="tb-btn" data-tip="音量" @click.stop="emit('open-quick')">
        <span class="glyph" v-html="ui.volume" />
      </button>
      <button class="tb-btn" data-tip="电池" @click.stop="emit('open-quick')">
        <span class="glyph" v-html="ui.battery" />
      </button>
      <div class="tb-clock" data-tip="通知" @click.stop="emit('open-notif')">
        <div>{{ timeStr }}</div>
        <div class="date">{{ dateStr }}</div>
      </div>
      <div class="show-desktop" title="显示桌面" @click="showDesktop" />
    </div>
  </div>
</template>

<style scoped>
.taskbar.center .tb-center {
  position: absolute;
  left: 50%;
  transform: translateX(-50%);
}
.taskbar.left-bar {
  justify-content: flex-start;
}
.taskbar.left-bar .tb-center {
  margin-left: 6px;
}
.taskbar.left-bar .tb-tray {
  margin-left: auto;
}
.glyph {
  display: inline-flex;
  color: var(--menu-fg);
}
.tb-btn .glyph,
.tb-btn .glyph :deep(svg) {
  width: 22px;
  height: 22px;
}
.tb-btn.active .glyph.win {
  color: var(--accent);
}
.tb-search .glyph,
.tb-search .glyph :deep(svg) {
  width: 15px;
  height: 15px;
}
.tb-search {
  font-size: 12.5px;
}
.date {
  opacity: 0.8;
  font-size: 11px;
}
</style>
