<script setup>
// 快速设置 / 通知中心（任务栏托盘与时钟点击弹出）
import { ref, computed, onMounted } from 'vue'
import axios from 'axios'
import { useSettings } from '../stores/settings'
import { ui } from '../assets/icons'
import { WALLPAPERS } from './registry/apps'

const props = defineProps({ mode: { type: String, default: 'quick' } }) // quick | notif
const emit = defineEmits(['close'])
const st = useSettings()

const net = ref({ wifi: true, bt: false, focus: false })
const qs = computed(() => ({
  wifi: net.value.wifi, bt: net.value.bt, focus: net.value.focus, night: !!st.cfg.nightLight,
}))

const volume = ref(62)

const tiles = computed(() => [
  { key: 'wifi', label: net.value.wifi ? 'WLAN · 已连接' : 'WLAN', icon: ui.wifi },
  { key: 'bt', label: net.value.bt ? '蓝牙 · 开' : '蓝牙', icon: ui.copy },
  { key: 'focus', label: '专注助手', icon: ui.bell },
  { key: 'night', label: '夜间模式', icon: ui.moon },
])

function toggle(key) {
  if (key === 'night') {
    st.patch({ nightLight: !st.cfg.nightLight })
    return
  }
  if (key === 'focus') {
    net.value.focus = !net.value.focus
    return
  }
  net.value[key] = !net.value[key]
}

function setBright(v) {
  st.patch({ brightness: v })
}

const notifs = ref([
  { t: '系统 · 陌如玉', d: '壁纸与主题设置会自动保存到服务器数据库', at: '刚刚' },
  { t: '记事本', d: '提示：新建便签后点击「保存」写入 desktop_memos', at: '10 分钟前' },
])
const health = ref('checking')
onMounted(async () => {
  try {
    const { data } = await axios.get('/api/health', { timeout: 5000 })
    health.value = data.db ? 'ok' : 'nodb'
  } catch {
    health.value = 'down'
  }
})

const cal = computed(() => {
  const now = new Date()
  const y = now.getFullYear()
  const m = now.getMonth()
  const first = new Date(y, m, 1)
  const startIdx = (first.getDay() + 6) % 7 // 周一开头
  const days = new Date(y, m + 1, 0).getDate()
  const cells = []
  const prevDays = new Date(y, m, 0).getDate()
  for (let i = 0; i < startIdx; i += 1) cells.push({ d: prevDays - startIdx + 1 + i, out: true })
  for (let d = 1; d <= days; d += 1) cells.push({ d, today: d === now.getDate() })
  while (cells.length % 7) cells.push({ d: cells.length - (startIdx + days) + 1, out: true })
  return { y, m: m + 1, cells }
})

const wallNow = computed(() => WALLPAPERS[st.cfg.wallpaper]?.name || '')
</script>

<template>
  <div class="flyout acrylic" @click.stop>
    <template v-if="props.mode === 'quick'">
      <h4>快速设置</h4>
      <div class="qs-grid">
        <div v-for="t in tiles" :key="t.key" class="qs-tile" :class="{ on: qs[t.key] }"
             @click="toggle(t.key)">
          <span class="ic" v-html="t.icon" />{{ t.label }}
        </div>
      </div>
      <div class="slider-row">
        <span class="ic" v-html="ui.sun" />
        <input type="range" min="30" max="100" :value="st.cfg.brightness ?? 100"
               @input="setBright(+$event.target.value)" />
        <span class="val">{{ st.cfg.brightness ?? 100 }}%</span>
      </div>
      <div class="slider-row">
        <span class="ic" v-html="ui.volume" />
        <input v-model.number="volume" type="range" min="0" max="100" />
        <span class="val">{{ volume }}%</span>
      </div>
      <div class="ctx-sep" />
      <div class="row-between">
        <span>壁纸：{{ wallNow }}</span>
        <a class="link" @click="st.patch({ wallpaper: st.cfg.wallpaper === 'bloom' ? 'night' : 'bloom' })">切换</a>
      </div>
      <div class="row-between">
        <span>服务状态</span>
        <a-tag :color="health === 'ok' ? 'success' : health === 'nodb' ? 'warning' : 'error'">
          {{ health === 'ok' ? '后端 + 数据库正常' : health === 'nodb' ? '后端在线，数据库未连接' : '后端不可用（本地模式）' }}
        </a-tag>
      </div>
      <div class="foot">
        <span class="avatar sm">陌</span>
        <span style="flex: 1">陌如玉</span>
        <span class="pct">{{ volume }}</span>
      </div>
    </template>

    <template v-else>
      <h4>通知中心</h4>
      <a-empty v-if="!notifs.length" description="没有新通知" />
      <div v-for="(n, i) in notifs" :key="i" class="notif">
        <div class="t">{{ n.t }} <span style="float: right; opacity: .6; font-weight: 400">{{ n.at }}</span></div>
        <div class="d">{{ n.d }}</div>
      </div>
      <a-button block size="small" danger @click="notifs = []">清除所有通知</a-button>
      <div class="cal-mini">
        <div class="cal-head">
          <b>{{ cal.y }} 年 {{ cal.m }} 月</b>
          <span style="opacity: .6">{{ new Date().toLocaleDateString('zh-CN', { weekday: 'long' }) }}</span>
        </div>
        <table>
          <thead><tr><th v-for="w in ['一', '二', '三', '四', '五', '六', '日']" :key="w">{{ w }}</th></tr></thead>
          <tbody>
            <tr v-for="(row, ri) in Math.ceil(cal.cells.length / 7)" :key="ri">
              <td v-for="ci in 7" :key="ci" :class="{ out: cal.cells[ri * 7 + ci - 1]?.out, today: cal.cells[ri * 7 + ci - 1]?.today }">
                <span>{{ cal.cells[ri * 7 + ci - 1]?.d }}</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>

<style scoped>
.flyout {
  width: 360px;
}
.row-between {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 12.5px;
  padding: 6px 0;
}
.link {
  color: var(--accent);
}
.qs-tile .ic {
  display: inline-flex;
  width: 18px;
  height: 18px;
}
.qs-tile .ic :deep(svg) {
  width: 18px;
  height: 18px;
}
.slider-row .ic {
  display: inline-flex;
  width: 16px;
  height: 16px;
}
.slider-row .ic :deep(svg) {
  width: 16px;
  height: 16px;
}
.val {
  width: 40px;
  text-align: right;
  font-size: 12px;
  opacity: 0.8;
}
.foot {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid var(--divider);
  font-size: 12.5px;
}
.avatar.sm {
  width: 26px;
  height: 26px;
  font-size: 11px;
}
.notif .t {
  font-size: 12px;
}
.cal-head {
  display: flex;
  justify-content: space-between;
  font-size: 12.5px;
  margin-bottom: 6px;
}
td.out {
  opacity: 0.35;
}
</style>
