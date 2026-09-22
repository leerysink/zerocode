<script setup>
// 开始菜单：搜索、已固定应用、所有应用列表、用户与电源
import { ref, computed } from 'vue'
import { useWinStore } from '../stores/windows'
import { appList, pinnedApps, WALLPAPERS } from './registry/apps'
import { useSettings } from '../stores/settings'
import { ui, logo } from '../assets/icons'

const emit = defineEmits(['close'])
const wm = useWinStore()
const st = useSettings()
const q = ref('')
const all = ref(false)
const powerOpen = ref(false)

const list = computed(() => appList())
const results = computed(() => {
  const k = q.value.trim().toLowerCase()
  if (!k) return []
  return list.value.filter(
    (a) => a.name.toLowerCase().includes(k) || a.short.toLowerCase().includes(k) || a.category.toLowerCase().includes(k),
  )
})
const pinned = computed(() => pinnedApps())
const shown = computed(() => {
  const arr = list.value.slice().sort((a, b) => a.short.localeCompare(b.short, 'zh-CN'))
  return all.value ? arr : results.value
})

function launch(id) {
  wm.open(id)
  emit('close')
}

function shutdown(mode) {
  window.dispatchEvent(new CustomEvent('desktop-power', { detail: mode }))
  emit('close')
}
</script>

<template>
  <div class="start-menu acrylic" :class="{ left: st.cfg.taskbarAlign === 'left' }"
       @click.stop>
    <div class="sm-search">
      <input v-model="q" class="sm-input" placeholder="搜索应用、设置和文档" autofocus />
    </div>

    <div class="sm-scroll">
      <template v-if="q">
        <div class="sm-section"><span>最佳匹配</span></div>
        <div class="sm-list">
          <div v-for="a in results" :key="a.id" class="sm-row" @click="launch(a.id)">
            <span class="g" v-html="a.icon" />
            <div>
              <div>{{ a.short }}</div>
              <div class="sub">{{ a.category }}</div>
            </div>
          </div>
          <div v-if="!results.length" class="sm-row muted">找不到与应用「{{ q }}」匹配的结果</div>
        </div>
      </template>

      <template v-else>
        <div class="sm-section">
          <span>已固定</span>
          <a class="link" @click="all = !all">{{ all ? '收起' : '所有应用' }}</a>
        </div>
        <div v-if="!all" class="sm-grid">
          <div v-for="a in pinned" :key="a.id" class="sm-tile" @click="launch(a.id)">
            <span class="g" v-html="a.icon" />
            <span>{{ a.short }}</span>
          </div>
        </div>
        <div v-else class="sm-list">
          <div v-for="a in shown" :key="a.id" class="sm-row" @click="launch(a.id)">
            <span class="g" v-html="a.icon" />
            <div>
              <div>{{ a.short }}</div>
              <div class="sub">{{ a.name }}</div>
            </div>
          </div>
        </div>
        <div v-if="!all" class="sm-section"><span>推荐的项目</span></div>
        <div v-if="!all" class="sm-list">
          <div v-for="w in Object.entries(WALLPAPERS).slice(0, 2)" :key="w[0]" class="sm-row"
               @click="st.patch({ wallpaper: w[0] })">
            <span class="g" v-html="logo.photos" />
            <div>
              <div>{{ w[1].name }}</div>
              <div class="sub">切换壁纸（写入数据库）</div>
            </div>
          </div>
        </div>
      </template>
    </div>

    <div class="sm-foot">
      <div class="sm-user">
        <span class="avatar">陌</span>
        <span>陌如玉</span>
      </div>
      <div style="position: relative">
        <button class="tb-btn" title="电源" @click.stop="powerOpen = !powerOpen">
          <span class="g" v-html="ui.power" />
        </button>
        <div v-if="powerOpen" class="power-menu acrylic" @click.stop>
          <div class="sm-row" @click="shutdown('sleep')">
            <span class="g" v-html="ui.moon" /> 睡眠
          </div>
          <div class="sm-row" @click="shutdown('restart')">
            <span class="g" v-html="ui.refresh" /> 重新启动
          </div>
          <div class="sm-row" @click="shutdown('shutdown')">
            <span class="g" v-html="ui.power" /> 关机
          </div>
          <div class="ctx-sep" />
          <div class="sm-row" @click="shutdown('lock')">
            <span class="g" v-html="ui.pin" /> 锁定
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.link {
  color: var(--accent);
  font-size: 12px;
  font-weight: 400;
}
.g {
  display: inline-flex;
  color: var(--menu-fg);
}
.sm-tile .g,
.sm-tile .g :deep(svg) {
  width: 32px;
  height: 32px;
}
.sm-row .g,
.sm-row .g :deep(svg) {
  width: 22px;
  height: 22px;
}
.sm-user .g,
.sm-user .g :deep(svg),
.tb-btn .g,
.tb-btn .g :deep(svg) {
  width: 18px;
  height: 18px;
}
.sub {
  font-size: 11.5px;
  opacity: 0.6;
}
.muted {
  opacity: 0.6;
}
</style>
