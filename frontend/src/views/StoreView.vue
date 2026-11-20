<script setup>
// 应用商店（规格 M3-a §9）：发现 + 申请，一页两视图（商店 / 我的申请）。
// 审批不在这里——入口已收口到运营台（ADR-0062 §7），本页刻意不再拉 review 收件箱，
// 免得两个队列各刷各的、出现完成时间差。
// 身份（Ticket 008 合流 / ADR-0003）：actor 即登录用户，昵称弹层已退役——401 统一去登录页。
import { computed, onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import {
  AppstoreOutlined, EyeOutlined, MenuOutlined, PlusOutlined, SendOutlined,
} from '@ant-design/icons-vue'
import * as api from '../api'
import { useCategoryStore } from '../stores/categories'
import ZcPageHeader from '../components/zc/ZcPageHeader.vue'
import ZcEmptyState from '../components/zc/ZcEmptyState.vue'
import ZcEntityCard from '../components/zc/ZcEntityCard.vue'

const cats = useCategoryStore()

const tab = ref('store')
const releases = ref([])
const loading = ref(false)
const catFilter = ref('')
const q = ref('')
const sort = ref('latest')
const page = ref(1)
const PAGE_SIZE = 20
const categories = computed(() => ['全部', ...cats.enabledKeys])
const pagedReleases = computed(() =>
  releases.value.slice((page.value - 1) * PAGE_SIZE, page.value * PAGE_SIZE))
const setCategory = (c) => { catFilter.value = c === '全部' ? '' : c; page.value = 1; loadReleases() }
const onSearch = () => { page.value = 1; loadReleases() }
const onSort = () => { page.value = 1; loadReleases() }
const mine = ref([])
const myId = ref(null)
const applyOpen = ref(false)
const applyTarget = ref(null)
const applyName = ref('')
const drawerOpen = ref(false)
const detail = ref(null)
const shotIdx = ref(0)
const fileInput = ref(null)

const stats = computed(() => [
  { key: 'on', label: '在架版本', value: releases.value.length },
  { key: 'cats', label: '商店分类', value: cats.enabled.length },
  { key: 'mine', label: '我已安装', value: releases.value.filter((r) => r.installed).length },
])

function openDetail (r) { detail.value = r; shotIdx.value = 0; drawerOpen.value = true }
const isMine = (r) => !!r && myId.value != null && r.created_by === myId.value
// 装了多个实例时，卡面只出「打开」这一个动作（指向第一个在跑的），其余收进 ☰
const runningInstall = (r) => (r.installed_apps || []).find(
  (i) => i.status_prod === 'running' && i.prod_url)
const prevShot = () => {
  const n = detail.value.screenshots.length
  shotIdx.value = (shotIdx.value - 1 + n) % n
}
const nextShot = () => {
  const n = detail.value.screenshots.length
  shotIdx.value = (shotIdx.value + 1) % n
}
async function refreshDetail () {
  await loadReleases()
  const fresh = releases.value.find((x) => x.id === detail.value.id)
  if (fresh) detail.value = fresh
  const n = detail.value.screenshots?.length || 0
  shotIdx.value = Math.min(shotIdx.value, Math.max(0, n - 1))
}
async function onPickShots (e) {
  for (const f of Array.from(e.target.files || [])) await api.storeAddShot(detail.value.id, f)
  e.target.value = ''
  await refreshDetail()
}
async function delShot (i) {
  await api.storeDelShot(detail.value.id, i)
  await refreshDetail()
}

async function loadReleases () {
  loading.value = true
  try {
    releases.value = (await api.storeReleases({
      q: q.value.trim(), category: catFilter.value, sort: sort.value,
    })).data
  } finally {
    loading.value = false
  }
}

async function load () {
  await Promise.all([loadReleases(), cats.load()])
  mine.value = (await api.storeApplications('mine')).data
}

onMounted(async () => {
  try {
    const res = await api.authMe()   // 登录门禁 + 我的应用归属判定（昵称回显已上全局顶栏 AccountBar）
    myId.value = res.data?.id ?? null
    await load()
  } catch (e) {
    // 非 auth 接口的 401 由 axios 拦截器统一跳转；authMe 在豁免名单里，这里自己引导
    if (e?.response?.status === 401) window.location.assign('/login')
    else message.error('商店加载失败：' + (e?.message || e))
  }
})

function openApply (r) { applyTarget.value = r; applyName.value = r.title; applyOpen.value = true }

function openTrial (r) {
  if (r.trial_available && r.trial_url) window.open(r.trial_url, '_blank')
}

function openInstalled (i) {
  if (i.status_prod === 'running' && i.prod_url) window.open(i.prod_url, '_blank')
}

async function submitApply () {
  const name = applyName.value.trim()
  if (!name) { message.warning('给应用起个名字'); return }
  await api.storeApply(applyTarget.value.id, name)
  applyOpen.value = false
  message.success('已提交申请，等管理员审批')
  await load()
}

async function cancelApply (row) {
  await api.storeCancel(row.id)
  message.success('已撤回申请')
  await load()
}

function gotoApps () { window.location.href = '/apps' }
</script>

<template>
  <div class="zc-store">
    <zc-page-header title="应用商店" subtitle="挑一个装成你自己的应用，或先试用作者的测试环境"
                    :stats="stats">
      <template #actions>
        <a-button data-test="store-refresh" @click="load">刷新</a-button>
        <a-button type="primary" data-test="store-new-app" @click="gotoApps">
          <plus-outlined /> 新建应用
        </a-button>
      </template>
    </zc-page-header>

    <a-tabs v-model:activeKey="tab">
      <a-tab-pane key="store" tab="商店">
        <!-- 搜索/排序与分类胶囊同住一张筛选面卡：裸控件直接铺在页面底色上会和下方卡流脱节 -->
        <div class="zc-store-filter zc-panel" data-test="store-filter-card">
          <div class="zc-store-toolbar">
            <a-input v-model:value="q" data-test="store-search" placeholder="搜索标题或简介"
                     style="width: 220px" @press-enter="onSearch" />
            <a-button data-test="store-search-btn" @click="onSearch">搜索</a-button>
            <a-radio-group v-model:value="sort" button-style="solid" size="small" @change="onSort">
              <a-radio-button value="latest" data-test="sort-latest">最新</a-radio-button>
              <a-radio-button value="installs" data-test="sort-installs">安装量</a-radio-button>
            </a-radio-group>
          </div>
          <div class="zc-store-filters">
            <a-tag v-for="c in categories" :key="c" :data-test="`cat-${c}`"
                   :color="(catFilter === '' && c === '全部') || catFilter === c ? 'blue' : undefined"
                   class="zc-cat-tag" @click="setCategory(c)">{{ cats.labelOf(c) }}</a-tag>
          </div>
        </div>
        <zc-empty-state v-if="loading" loading />
        <zc-empty-state v-else-if="!pagedReleases.length"
                        :description="releases.length ? '该分类下暂无应用' : '还没有应用上架'" />
        <div v-else class="zc-store-grid">
          <zc-entity-card v-for="r in pagedReleases" :key="r.id" class="zc-card"
                          data-test="store-card" cover-clickable @cover-click="openDetail(r)">
            <template #cover>
              <img v-if="r.screenshots && r.screenshots.length" :src="r.screenshots[0]"
                   class="zc-cover-img" alt="" />
              <div v-else class="zc-cover-placeholder"><appstore-outlined /></div>
            </template>
            <template v-if="r.installed" #badge>
              <a-tag class="zc-inst-tag">已安装</a-tag>
            </template>
            <template #title>
              <span class="zc-store-name" data-test="store-title" @click="openDetail(r)">
                {{ r.title }}
              </span>
            </template>
            <template #tags>
              <a-tag class="zc-card-tag">{{ r.stack }}</a-tag>
              <a-tag class="zc-card-tag" color="geekblue">{{ cats.labelOf(r.category) }}</a-tag>
            </template>
            <template #menu>
              <a-dropdown :trigger="['click']" placement="bottomRight">
                <menu-outlined class="zc-card-menu-btn" title="更多操作" @click.stop />
                <template #overlay>
                  <div class="zc-app-menu">
                    <div class="zc-app-menu-item" data-test="detail-btn" @click.stop="openDetail(r)">
                      <eye-outlined /> 查看详情
                    </div>
                    <div v-for="i in r.installed_apps" :key="i.app_id" class="zc-app-menu-item"
                         :class="{ 'is-off': !i.prod_url || i.status_prod !== 'running' }"
                         :data-test="`open-installed-${i.app_id}`" @click.stop="openInstalled(i)">
                      打开 {{ i.name }}
                    </div>
                    <div v-if="!r.installed_apps || !r.installed_apps.length"
                         class="zc-app-menu-item is-off">还没装过这个应用</div>
                  </div>
                </template>
              </a-dropdown>
            </template>
            <template #summary>{{ r.summary || '作者没写简介' }}</template>
            <template #meta>
              发布者 {{ r.publisher || '未知' }} · 版本 {{ r.version }} ·
              安装 {{ r.install_count || 0 }} 次
            </template>
            <template #actions>
              <!-- 卡面只留两枚胶囊：试用（读作者 dev）与 申请/打开。装了多个实例时「打开」
                   指向第一个在跑的，其余进 ☰——按钮随实例数增长会把卡面挤到换行。 -->
              <a-button size="small" shape="round" data-test="trial-btn"
                        :disabled="!r.trial_available"
                        :title="r.trial_available ? '打开作者的测试环境试用' : '作者的测试环境未启动'"
                        @click="openTrial(r)">在线试用</a-button>
              <a-button v-if="!r.installed" size="small" shape="round" type="primary"
                        data-test="apply-btn" @click="openApply(r)">
                <send-outlined /> 申请使用
              </a-button>
              <a-button v-else size="small" shape="round" data-test="open-btn"
                        :disabled="!runningInstall(r)"
                        :title="runningInstall(r) ? '打开你装的实例' : '实例的生产环境未运行'"
                        @click="runningInstall(r) && openInstalled(runningInstall(r))">
                打开
              </a-button>
            </template>
          </zc-entity-card>
        </div>
        <a-pagination v-if="releases.length > PAGE_SIZE" v-model:current="page"
                      :total="releases.length" :page-size="PAGE_SIZE" :show-size-changer="false"
                      class="zc-store-pager" />
      </a-tab-pane>

      <a-tab-pane key="mine" tab="我的申请">
        <a-empty v-if="!mine.length" description="还没有提过申请" />
        <a-card v-else class="zc-store-apply" data-test="store-apply-card" title="我的申请">
          <a-table :data-source="mine" :pagination="false" row-key="id"
            :columns="[{ title: '应用', dataIndex: 'title' }, { title: '我起的名字', dataIndex: 'want_name' },
                       { title: '状态', dataIndex: 'status' }, { title: '说明', dataIndex: 'reason' },
                       { title: '操作', key: 'op' }]">
            <template #bodyCell="{ column, record }">
              <a-button v-if="column.key === 'op' && record.status === 'pending'" size="small"
                        data-test="cancel-apply" @click="cancelApply(record)">撤回</a-button>
            </template>
          </a-table>
        </a-card>
      </a-tab-pane>
    </a-tabs>

    <a-modal v-model:open="applyOpen" title="申请使用" :footer="null">
      <a-input v-model:value="applyName" placeholder="给你的实例起个名字" />
      <p class="zc-store-tip">获批后会为你单独建一套代码目录与数据库，数据与发布者无关。</p>
      <a-button type="primary" @click="submitApply">提交申请</a-button>
    </a-modal>

    <a-drawer v-model:open="drawerOpen" :title="detail?.title" width="480" placement="right">
      <template v-if="detail">
        <div v-if="detail.screenshots && detail.screenshots.length" class="zc-shot-viewer">
          <img :src="detail.screenshots[shotIdx]" class="zc-shot-img" alt="" />
          <div v-if="detail.screenshots.length > 1" class="zc-shot-nav">
            <a-button size="small" data-test="shot-prev" @click="prevShot">上一张</a-button>
            <span>{{ shotIdx + 1 }} / {{ detail.screenshots.length }}</span>
            <a-button size="small" data-test="shot-next" @click="nextShot">下一张</a-button>
          </div>
        </div>
        <div v-else class="zc-cover-placeholder zc-shot-empty"><appstore-outlined /></div>
        <p class="zc-store-summary">{{ detail.summary || '作者没写简介' }}</p>
        <div class="zc-store-meta">
          分类 {{ cats.labelOf(detail.category) }} · {{ detail.stack }} ·
          发布者 {{ detail.publisher || '未知' }} · 版本 {{ detail.version }} ·
          安装 {{ detail.install_count || 0 }} 次
        </div>
        <div v-if="isMine(detail)" class="zc-shot-admin">
          <a-button size="small" data-test="shot-add"
                    @click="fileInput && fileInput.click()">补传截图</a-button>
          <input ref="fileInput" data-test="shot-file" type="file" accept="image/png,image/jpeg"
                 multiple style="display: none" @change="onPickShots" />
          <a-button v-for="(s, i) in detail.screenshots" :key="s" size="small" danger
                    data-test="shot-del" @click="delShot(i)">删第{{ i + 1 }}张</a-button>
        </div>
        <a-space wrap style="margin-top: 12px">
          <a-button data-test="trial-btn" :disabled="!detail.trial_available"
                    :title="detail.trial_available ? '打开作者的测试环境试用' : '作者的测试环境未启动'"
                    @click="openTrial(detail)">在线试用</a-button>
          <a-button v-if="!detail.installed" type="primary" @click="openApply(detail)">
            <send-outlined /> 申请使用
          </a-button>
          <a-button v-for="i in detail.installed_apps" :key="i.app_id"
                    :disabled="i.status_prod !== 'running' || !i.prod_url"
                    @click="openInstalled(i)">
            打开 {{ i.name }}
          </a-button>
        </a-space>
        <p class="zc-store-tip">试用环境是作者的测试环境，试用操作会写入作者的测试数据库。</p>
      </template>
    </a-drawer>
  </div>
</template>

<style scoped>
/* 间距与字号统一到 token（ADR-0059 决策 8）：这一页原先散着 13px/16px/rgba(0,0,0,.55)
   这类硬编码值，换肤时只能靠深色块覆写颜色补，几何也各自为政 */
.zc-store { padding: var(--zc-space-5); }
.zc-store-meta { color: var(--zc-text-sub); font-size: var(--zc-font-sm); }
/* 筛选面卡：外壳（描边/圆角/卡底）由全局 .zc-panel 给，这里只管卡内节奏 */
.zc-store-filter { margin-bottom: var(--zc-space-4); padding: var(--zc-space-3) var(--zc-space-4); }
.zc-store-filters { display: flex; flex-wrap: wrap; gap: var(--zc-space-2); }
.zc-cat-tag { cursor: pointer; }
.zc-store-toolbar { display: flex; align-items: center; gap: var(--zc-space-2);
  margin-bottom: var(--zc-space-2); }
.zc-store-pager { margin-top: var(--zc-space-3); text-align: right; }
/* 网格列宽与等高由这里给，卡内节奏在 ZcEntityCard 里 */
.zc-store-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--zc-space-4); align-items: stretch; }
@media (max-width: 1100px) { .zc-store-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 680px) { .zc-store-grid { grid-template-columns: 1fr; } }
.zc-cover-img { display: block; width: 100%; height: 100%; object-fit: cover; }
.zc-cover-placeholder { position: absolute; inset: 0; display: flex; align-items: center;
  justify-content: center; font-size: 40px; color: var(--zc-cover-placeholder); }
.zc-store-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  cursor: pointer; }
.zc-store-name:hover { color: var(--zc-primary); }
.zc-inst-tag { background: var(--zc-success-bg); color: var(--zc-success);
  border: 1px solid rgba(var(--zc-success-rgb), 0.35); }
/* 菜单入口用三横（☰）而不是 ⋯ 的圆点：圆点在卡头太小、几乎看不见（同应用页语言） */
.zc-card-menu-btn { font-size: var(--zc-font-md); line-height: 1; padding: 3px 4px;
  cursor: pointer; opacity: 0.7; color: var(--zc-text-sub); }
.zc-card-menu-btn:hover { opacity: 1; color: var(--zc-primary); }
.zc-app-menu { min-width: 148px; padding: 4px 0; background: var(--zc-bg-card);
  border-radius: var(--zc-radius-inner); box-shadow: 0 6px 16px rgba(var(--zc-shadow-rgb), 0.12); }
.zc-app-menu-item { display: flex; align-items: center; gap: var(--zc-space-2);
  padding: 6px 14px; font-size: var(--zc-font-sm); color: var(--zc-text); cursor: pointer;
  white-space: nowrap; }
.zc-app-menu-item:hover { background: rgba(var(--zc-primary-rgb), 0.08); }
.zc-app-menu-item.is-off { color: var(--zc-text-weak); cursor: default; }
.zc-app-menu-item.is-off:hover { background: transparent; }
.zc-shot-viewer { text-align: center; }
.zc-shot-img { max-width: 100%; max-height: 280px; border-radius: var(--zc-radius-inner); }
.zc-shot-nav { display: flex; align-items: center; justify-content: center;
  gap: var(--zc-space-3); margin-top: var(--zc-space-2); }
.zc-shot-empty { position: static; height: 160px; background: var(--zc-bg-cover);
  border-radius: var(--zc-radius-inner); }
.zc-shot-admin { margin-top: var(--zc-space-3); display: flex; flex-wrap: wrap;
  gap: var(--zc-space-2); }
.zc-store-summary { margin: var(--zc-space-2) 0 var(--zc-space-3);
  font-size: var(--zc-font-sm); color: var(--zc-text-sub);
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
  overflow: hidden; min-height: 40px; }
.zc-store-tip { color: var(--zc-text-sub); font-size: var(--zc-font-sm); }
/* 与应用页同纪律：类型标签紧跟应用名，8px 左间距、常规字重、与文本垂直居中 */
.zc-card-tag { margin-inline-start: var(--zc-space-2); font-weight: 400;
  vertical-align: middle; }
</style>
