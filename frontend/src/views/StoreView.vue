<script setup>
// 应用商店（规格 M3-a §9）：列表/申请/审批一页三视图。
// 身份（Ticket 008 合流 / ADR-0003）：actor 即登录用户，昵称弹层已退役——401 统一去登录页。
import { computed, onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import { AppstoreOutlined, PlusOutlined, SendOutlined } from '@ant-design/icons-vue'
import * as api from '../api'
import BackButton from '../components/BackButton.vue'
import { STORE_CATEGORIES } from '../utils/storeCategories'

const tab = ref('store')
const releases = ref([])
const catFilter = ref('')
const q = ref('')
const sort = ref('latest')
const page = ref(1)
const PAGE_SIZE = 20
const categories = ['全部', ...STORE_CATEGORIES]
const pagedReleases = computed(() =>
  releases.value.slice((page.value - 1) * PAGE_SIZE, page.value * PAGE_SIZE))
const setCategory = (c) => { catFilter.value = c === '全部' ? '' : c; page.value = 1; loadReleases() }
const onSearch = () => { page.value = 1; loadReleases() }
const onSort = () => { page.value = 1; loadReleases() }
const mine = ref([])
const review = ref([])
// 审批权收口创始人（平台管理员）：非创始人不展示审批收件箱；
// 收件箱读接口对非创始人返回空列表、决策接口 403 兜底（后端 applications.py）。
const isFounder = ref(false)
const myId = ref(null)
const applyOpen = ref(false)
const applyTarget = ref(null)
const applyName = ref('')
const reasonDraft = ref({})
const drawerOpen = ref(false)
const detail = ref(null)
const shotIdx = ref(0)
const fileInput = ref(null)

function openDetail (r) { detail.value = r; shotIdx.value = 0; drawerOpen.value = true }
const isMine = (r) => !!r && myId.value != null && r.created_by === myId.value
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
  releases.value = (await api.storeReleases({
    q: q.value.trim(), category: catFilter.value, sort: sort.value,
  })).data
}

async function load () {
  await loadReleases()
  mine.value = (await api.storeApplications('mine')).data
  review.value = (await api.storeApplications('review')).data
}

onMounted(async () => {
  try {
    const res = await api.authMe()   // 登录门禁 + 创始人角色（昵称回显已上全局顶栏 AccountBar）
    isFounder.value = !!res.data?.is_founder
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

async function approve (row) {
  await api.storeApprove(row.id)
  message.success('已通过，正在安装（进度看来源应用的日志）')
  await load()
}

// 驳回双按钮同走 reject：没写原因只提醒、不调接口（后端也强制，前端先把空提交挡住）
async function reject (row) {
  const reason = (reasonDraft.value[row.id] || '').trim()
  if (!reason) { message.warning('驳回请写原因'); return }
  await api.storeReject(row.id, reason)
  await load()
}

function gotoApps () { window.location.href = '/apps' }
</script>

<template>
  <div class="zc-store">
    <header class="zc-store-head">
      <div class="zc-store-head-left">
        <back-button />
        <h2><appstore-outlined /> 应用商店</h2>
      </div>
      <a-space>
        <a-button @click="load">刷新</a-button>
        <a-button type="primary" @click="gotoApps"><plus-outlined /> 新建应用</a-button>
      </a-space>
    </header>

    <a-tabs v-model:activeKey="tab">
      <a-tab-pane key="store" tab="商店">
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
                 class="zc-cat-tag" @click="setCategory(c)">{{ c }}</a-tag>
        </div>
        <a-empty v-if="!pagedReleases.length"
                 :description="releases.length ? '该分类下暂无应用' : '还没有应用上架'" />
        <div class="zc-store-grid">
        <a-card v-for="r in pagedReleases" :key="r.id" class="zc-card">
          <template #cover>
            <div class="zc-cover" @click="openDetail(r)">
              <img v-if="r.screenshots && r.screenshots.length" :src="r.screenshots[0]"
                   class="zc-cover-img" alt="" />
              <div v-else class="zc-cover-placeholder"><appstore-outlined /></div>
            </div>
          </template>
          <template #title>
            {{ r.title }}
            <a-tag class="zc-card-tag">{{ r.stack }}</a-tag>
            <a-tag class="zc-card-tag" color="geekblue">{{ r.category || '其他' }}</a-tag>
            <a-tag v-if="r.installed" class="zc-card-tag">已安装</a-tag>
          </template>
          <div class="zc-store-meta">发布者 {{ r.publisher || '未知' }} · 版本 {{ r.version }} ·
            安装 {{ r.install_count || 0 }} 次</div>
          <p class="zc-store-summary">{{ r.summary || '作者没写简介' }}</p>
          <a-space wrap>
            <a-button data-test="trial-btn" :disabled="!r.trial_available"
                      :title="r.trial_available ? '打开作者的测试环境试用' : '作者的测试环境未启动'"
                      @click="openTrial(r)">在线试用</a-button>
            <a-button v-if="!r.installed" type="primary" @click="openApply(r)">
              <send-outlined /> 申请使用
            </a-button>
            <a-button v-for="i in r.installed_apps" :key="i.app_id"
                      :disabled="i.status_prod !== 'running' || !i.prod_url"
                      @click="openInstalled(i)">
              打开 {{ i.name }}
            </a-button>
          </a-space>
        </a-card>
        </div>
        <a-pagination v-if="releases.length > PAGE_SIZE" v-model:current="page"
                      :total="releases.length" :page-size="PAGE_SIZE" :show-size-changer="false"
                      class="zc-store-pager" />
      </a-tab-pane>

      <a-tab-pane key="mine" tab="我的申请">
        <a-empty v-if="!mine.length" description="还没有提过申请" />
        <a-table v-else :data-source="mine" :pagination="false" row-key="id"
          :columns="[{ title: '应用', dataIndex: 'title' }, { title: '我起的名字', dataIndex: 'want_name' },
                     { title: '状态', dataIndex: 'status' }, { title: '说明', dataIndex: 'reason' },
                     { title: '操作', key: 'op' }]">
          <template #bodyCell="{ column, record }">
            <a-button v-if="column.key === 'op' && record.status === 'pending'" size="small"
                      data-test="cancel-apply" @click="cancelApply(record)">撤回</a-button>
          </template>
        </a-table>
      </a-tab-pane>

      <a-tab-pane v-if="isFounder" key="review">
        <template #tab><span data-test="tab-review">待审批</span></template>
        <a-empty v-if="!review.length" description="没有待审批的申请" />
        <div v-for="row in review" :key="row.id" class="zc-review-row">
          <div><b>{{ row.want_name }}</b> · 来自 {{ row.title }} {{ row.version }}</div>
          <a-space>
            <a-button data-test="approve-btn" type="primary" @click="approve(row)">通过并安装</a-button>
            <a-input data-test="reject-reason" v-model:value="reasonDraft[row.id]"
                     placeholder="驳回原因" style="width: 200px" />
            <a-button data-test="reject-btn" @click="reject(row)">驳回</a-button>
            <a-button data-test="reject-confirm" danger type="primary" ghost
                      @click="reject(row)">确认驳回</a-button>
          </a-space>
        </div>
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
          分类 {{ detail.category || '其他' }} · {{ detail.stack }} ·
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
.zc-store { padding: 16px 20px; }
.zc-store-head { display: flex; align-items: center; justify-content: space-between; }
.zc-store-head-left { display: flex; align-items: center; gap: 10px; }
.zc-store-meta { color: rgba(0, 0, 0, 0.55); font-size: 13px; }
.zc-store-filters { margin-bottom: 12px; display: flex; flex-wrap: wrap; gap: 6px; }
.zc-cat-tag { cursor: pointer; }
.zc-store-toolbar { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.zc-store-pager { margin-top: 12px; text-align: right; }
/* 一行 3 列卡片网格：窄屏降为 2 列 / 1 列；卡片等高，按钮沉底 */
.zc-store-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px; align-items: stretch; }
.zc-store-grid .zc-card { display: flex; flex-direction: column; min-width: 0; }
.zc-store-grid .zc-card :deep(.ant-card-head-title) { white-space: normal; line-height: 1.6; }
.zc-store-grid .zc-card :deep(.ant-card-body) { flex: 1; display: flex; flex-direction: column; }
.zc-store-grid .zc-card :deep(.ant-card-body) > .ant-space { margin-top: auto; padding-top: 8px; }
@media (max-width: 1100px) { .zc-store-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 680px) { .zc-store-grid { grid-template-columns: 1fr; } }
.zc-cover { cursor: pointer; height: 140px; overflow: hidden; background: var(--zc-bg-cover);
  display: flex; align-items: center; justify-content: center; }
.zc-cover-img { width: 100%; height: 100%; object-fit: cover; }
.zc-cover-placeholder { font-size: 40px; color: var(--zc-cover-placeholder); }
.zc-shot-viewer { text-align: center; }
.zc-shot-img { max-width: 100%; max-height: 280px; border-radius: 6px; }
.zc-shot-nav { display: flex; align-items: center; justify-content: center; gap: 10px; margin-top: 8px; }
.zc-shot-empty { height: 160px; display: flex; align-items: center; justify-content: center;
  background: var(--zc-bg-cover); border-radius: 6px; }
.zc-shot-admin { margin-top: 10px; display: flex; flex-wrap: wrap; gap: 6px; }
.zc-store-summary { margin: 8px 0 12px; display: -webkit-box; -webkit-line-clamp: 2;
  -webkit-box-orient: vertical; overflow: hidden; min-height: 40px; }
.zc-store-tip { color: rgba(0, 0, 0, 0.55); font-size: 13px; }
.zc-review-row { display: flex; align-items: center; justify-content: space-between;
  padding: 10px 0; border-bottom: 1px solid rgba(0, 0, 0, 0.06); }
/* 与应用页同纪律：类型标签紧跟应用名，8px 左间距、常规字重、与文本垂直居中 */
.zc-card-tag { margin-inline-start: 8px; font-weight: 400; vertical-align: middle; }
</style>
