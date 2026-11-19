<script setup>
// 商店管理（创始人）：上架生命周期 / 审批队列 / 分类总览。门禁双层：视图 + 后端 403。
import { computed, onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import {
  AppstoreOutlined, AuditOutlined, EyeOutlined, PlusOutlined, ReloadOutlined,
} from '@ant-design/icons-vue'
import * as api from '../api'
import { useAuthStore } from '../stores/auth'
import { STORE_CATEGORIES } from '../utils/storeCategories'
import ZcPageHeader from '../components/zc/ZcPageHeader.vue'
import ZcEmptyState from '../components/zc/ZcEmptyState.vue'

const auth = useAuthStore()
const isFounder = computed(() => !!auth.user?.is_founder)

const releases = ref([])
const review = ref([])
const tab = ref('all')
const loading = ref(false)
const reviewOpen = ref(false)
const catOpen = ref(false)
const reasonDraft = ref({})

const TABS = [
  { key: 'all', label: '全部' },
  { key: 'published', label: '已上架' },
  { key: 'withdrawn', label: '已下架' },
]
const counts = computed(() => ({
  all: releases.value.length,
  published: releases.value.filter((r) => r.status === 'published').length,
  withdrawn: releases.value.filter((r) => r.status === 'withdrawn').length,
}))
const shown = computed(() =>
  tab.value === 'all' ? releases.value : releases.value.filter((r) => r.status === tab.value))
const stats = computed(() => [
  { key: 'all', label: '全部版本', value: counts.value.all },
  { key: 'published', label: '已上架', value: counts.value.published },
  { key: 'withdrawn', label: '已下架', value: counts.value.withdrawn },
  { key: 'review', label: '待审批', value: review.value.length },
])
const catStats = computed(() => STORE_CATEGORIES.map((c) => {
  const rows = releases.value.filter((r) => (r.category || '其他') === c)
  return { name: c, total: rows.length, published: rows.filter((r) => r.status === 'published').length }
}))

async function loadAll () {
  if (!isFounder.value) return
  loading.value = true
  try {
    const [rel, rev] = await Promise.all([
      api.storeAdminReleases(),
      api.storeApplications('review'),
    ])
    releases.value = rel.data
    review.value = rev.data
  } catch (e) {
    message.error('商店管理加载失败：' + (e?.message || e))
  } finally {
    loading.value = false
  }
}
onMounted(loadAll)

const fmtDate = (s) => (s ? new Date(s).toLocaleDateString('zh-CN') : '')
function preview (r) { if (r.preview_url) window.open(r.preview_url, '_blank') }

async function withdraw (r) {
  await api.storeWithdraw(r.id)
  message.success('已下架，已装实例不受影响')
  await loadAll()
}
async function republish (r) {
  await api.storeRepublish(r.id)
  message.success('已重新上架')
  await loadAll()
}
async function approve (row) {
  await api.storeApprove(row.id)
  message.success('已通过，正在安装')
  await loadAll()
}
async function reject (row) {
  const reason = (reasonDraft.value[row.id] || '').trim()
  if (!reason) { message.warning('驳回请写原因'); return }
  await api.storeReject(row.id, reason)
  message.success('已驳回')
  await loadAll()
}
</script>

<template>
  <div class="zc-sadm">
    <a-empty v-if="!isFounder" description="商店管理仅创始人可见" />
    <template v-else>
      <zc-page-header data-test="sadm-head" title="商店管理"
                      subtitle="管理上架应用、处理审批与分类" :stats="stats">
        <template #actions>
          <a-badge :count="review.length" :offset="[-2, 2]">
            <a-button data-test="adm-review-open" @click="reviewOpen = true">
              <audit-outlined /> 审批队列
            </a-button>
          </a-badge>
          <a-button data-test="adm-refresh" :loading="loading" @click="loadAll">
            <reload-outlined /> 刷新
          </a-button>
          <a-button type="primary" data-test="adm-cat-open" @click="catOpen = true">
            <plus-outlined /> 分类管理
          </a-button>
        </template>
      </zc-page-header>

      <div class="zc-sadm-tabs" role="tablist">
        <button v-for="t in TABS" :key="t.key" type="button" role="tab"
                :aria-selected="tab === t.key" :data-test="`adm-tab-${t.key}`"
                class="zc-sadm-tab" :class="{ on: tab === t.key }" @click="tab = t.key">
          {{ t.label }} <span class="n">{{ counts[t.key] }}</span>
        </button>
      </div>

      <zc-empty-state v-if="loading" loading />
      <zc-empty-state v-else-if="!shown.length"
                      :description="releases.length ? '该状态下暂无版本' : '还没有应用上架'" />
      <div v-else class="zc-sadm-grid">
          <article v-for="r in shown" :key="r.id" class="zc-sadm-card"
                   :class="{ off: r.status === 'withdrawn' }" data-test="adm-card">
            <div class="zc-sadm-cover" :class="{ click: !!r.preview_url }" @click="preview(r)">
              <img v-if="r.screenshots && r.screenshots.length" :src="r.screenshots[0]" alt="" />
              <div v-else class="zc-sadm-cover-ph"><appstore-outlined /></div>
              <span class="zc-sadm-pill" :class="r.status">
                {{ r.status === 'published' ? '已上架' : '已下架' }}
              </span>
            </div>
            <div class="zc-sadm-body">
              <div class="zc-sadm-titlerow">
                <h3 :title="r.title">{{ r.title }}</h3>
                <span class="zc-sadm-ver">{{ r.version }}</span>
              </div>
              <div class="zc-sadm-tags">
                <span class="zc-sadm-tag">{{ r.stack }}</span>
                <span class="zc-sadm-tag cat">{{ r.category || '其他' }}</span>
              </div>
              <p class="zc-sadm-summary">{{ r.summary || '作者没写简介' }}</p>
              <div class="zc-sadm-meta">
                {{ r.publisher || '未知' }} · {{ r.install_count || 0 }} 次安装 · {{ fmtDate(r.created_at) }}
              </div>
              <div class="zc-sadm-foot">
                <a-button v-if="r.status === 'published'" size="small" danger ghost
                          data-test="adm-withdraw" @click="withdraw(r)">下架</a-button>
                <a-button v-else size="small" type="primary" ghost
                          data-test="adm-republish" @click="republish(r)">重新上架</a-button>
                <a-button size="small" type="primary" ghost :disabled="!r.preview_url"
                          :title="r.preview_url ? '打开在服环境预览' : '来源应用未在服'"
                          data-test="adm-preview" @click="preview(r)">
                  <eye-outlined /> 预览
                </a-button>
              </div>
            </div>
          </article>
      </div>
    </template>

    <a-drawer v-model:open="reviewOpen" title="审批队列" width="520" placement="right">
      <a-empty v-if="!review.length" description="没有待审批的申请" />
      <div v-for="row in review" :key="row.id" class="zc-sadm-review-row">
        <div class="zc-sadm-review-line"><b>{{ row.want_name }}</b></div>
        <div class="zc-sadm-review-sub">
          {{ row.applicant || '未知' }} 申请 · 来自 {{ row.title }} {{ row.version }} · {{ fmtDate(row.created_at) }}
        </div>
        <div class="zc-sadm-review-ops">
          <a-button size="small" type="primary" data-test="adm-approve"
                    @click="approve(row)">通过并安装</a-button>
          <a-input v-model:value="reasonDraft[row.id]" size="small"
                   placeholder="驳回原因" style="width: 180px" data-test="adm-reject-reason" />
          <a-button size="small" danger data-test="adm-reject" @click="reject(row)">驳回</a-button>
        </div>
      </div>
    </a-drawer>

    <a-modal v-model:open="catOpen" title="分类管理" :footer="null" width="480">
      <div v-for="c in catStats" :key="c.name" class="zc-sadm-cat-row">
        <span>{{ c.name }}</span>
        <span class="zc-sadm-cat-n">{{ c.published }} 在架 / {{ c.total }} 版本</span>
      </div>
      <p class="zc-sadm-cat-note">分类为平台固定枚举（规格 §4.8），上架时从中选择；此处为各分类的在架与版本总览。</p>
    </a-modal>
  </div>
</template>

<style scoped>
.zc-sadm { padding: var(--zc-space-5); }
.zc-sadm-tabs { display: flex; gap: var(--zc-space-2); margin-bottom: var(--zc-space-4); }
.zc-sadm-tab { display: flex; align-items: center; gap: 6px; border: none; cursor: pointer;
  padding: 5px 14px; border-radius: var(--zc-radius-pill); background: transparent;
  color: var(--zc-text-sub); font-size: var(--zc-font-sm); }
.zc-sadm-tab:hover { background: var(--zc-bg-inset); color: var(--zc-text); }
.zc-sadm-tab.on { background: rgba(var(--zc-primary-rgb), 0.12); color: var(--zc-primary); font-weight: 500; }
.zc-sadm-tab .n { min-width: 18px; padding: 0 6px; border-radius: var(--zc-radius-pill);
  background: var(--zc-bg-inset); color: var(--zc-text-sub); font-size: var(--zc-font-xs);
  line-height: 18px; text-align: center; }
.zc-sadm-tab.on .n { background: rgba(var(--zc-primary-rgb), 0.16); color: var(--zc-primary); }
/* 一行 3 列等高卡片：窄屏降 2 列 / 1 列 */
.zc-sadm-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--zc-space-4); align-items: stretch; }
@media (max-width: 1100px) { .zc-sadm-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 680px) { .zc-sadm-grid { grid-template-columns: 1fr; } }
.zc-sadm-card { display: flex; flex-direction: column; min-width: 0; overflow: hidden;
  background: var(--zc-bg-card); border: 1px solid var(--zc-border);
  border-radius: var(--zc-radius-card);
  box-shadow: var(--zc-shadow-1) rgba(var(--zc-shadow-rgb), 0.05);
  transition: border-color 0.15s, box-shadow 0.15s; }
.zc-sadm-card:hover { border-color: rgba(var(--zc-primary-rgb), 0.45);
  box-shadow: var(--zc-shadow-1) rgba(var(--zc-shadow-rgb), 0.12); }
.zc-sadm-cover { position: relative; aspect-ratio: 16 / 9; overflow: hidden;
  background: var(--zc-bg-cover); }
.zc-sadm-cover.click { cursor: pointer; }
.zc-sadm-cover img { display: block; width: 100%; height: 100%; object-fit: cover; }
.zc-sadm-card.off .zc-sadm-cover img { filter: grayscale(0.7); opacity: 0.75; }
.zc-sadm-cover-ph { position: absolute; inset: 0; display: flex; align-items: center;
  justify-content: center; font-size: 44px; color: rgba(var(--zc-primary-rgb), 0.55);
  background: linear-gradient(135deg, rgba(var(--zc-primary-rgb), 0.14),
    rgba(var(--zc-success-soft-rgb), 0.22)); }
.zc-sadm-pill { position: absolute; top: var(--zc-space-2); right: var(--zc-space-2);
  padding: 2px 10px; border-radius: var(--zc-radius-pill); font-size: var(--zc-font-xs);
  line-height: 18px; }
.zc-sadm-pill.published { background: var(--zc-success-bg); color: var(--zc-success);
  border: 1px solid rgba(var(--zc-success-rgb), 0.35); }
.zc-sadm-pill.withdrawn { background: var(--zc-bg-inset); color: var(--zc-text-sub);
  border: 1px solid var(--zc-border); }
.zc-sadm-body { display: flex; flex-direction: column; flex: 1;
  padding: var(--zc-space-3) var(--zc-space-4); }
.zc-sadm-titlerow { display: flex; align-items: center; gap: var(--zc-space-2); }
.zc-sadm-titlerow h3 { flex: 1; min-width: 0; margin: 0; font-size: var(--zc-font-md);
  font-weight: 600; color: var(--zc-text); white-space: nowrap; overflow: hidden;
  text-overflow: ellipsis; }
.zc-sadm-ver { flex: none; font-size: var(--zc-font-xs); color: var(--zc-text-weak); }
.zc-sadm-tags { display: flex; gap: var(--zc-space-1); margin-top: var(--zc-space-2); }
.zc-sadm-tag { padding: 1px 8px; border-radius: var(--zc-radius-pill);
  font-size: var(--zc-font-xs); background: var(--zc-bg-inset); color: var(--zc-text-sub);
  border: 1px solid var(--zc-border-soft); }
.zc-sadm-tag.cat { background: rgba(var(--zc-primary-rgb), 0.1); color: var(--zc-primary);
  border-color: transparent; }
.zc-sadm-summary { margin: var(--zc-space-2) 0; font-size: var(--zc-font-sm);
  color: var(--zc-text-sub); line-height: 1.6; display: -webkit-box;
  -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
  min-height: calc(var(--zc-font-sm) * 3.2); }
.zc-sadm-meta { margin-top: auto; padding-top: var(--zc-space-2);
  font-size: var(--zc-font-xs); color: var(--zc-text-weak); }
.zc-sadm-foot { display: flex; align-items: center; justify-content: space-between;
  gap: var(--zc-space-2); margin-top: var(--zc-space-3); padding-top: var(--zc-space-3);
  border-top: 1px solid var(--zc-border-soft); }
.zc-sadm-review-row { display: flex; flex-direction: column; gap: var(--zc-space-2);
  padding: var(--zc-space-3) 0; border-bottom: 1px solid var(--zc-border-soft); }
.zc-sadm-review-line { font-size: var(--zc-font-sm); color: var(--zc-text); }
.zc-sadm-review-sub { font-size: var(--zc-font-xs); color: var(--zc-text-sub); }
.zc-sadm-review-ops { display: flex; align-items: center; gap: var(--zc-space-2); }
.zc-sadm-cat-row { display: flex; justify-content: space-between;
  padding: var(--zc-space-2) 0; border-bottom: 1px solid var(--zc-border-soft);
  font-size: var(--zc-font-sm); color: var(--zc-text); }
.zc-sadm-cat-n { color: var(--zc-text-sub); font-size: var(--zc-font-xs); }
.zc-sadm-cat-note { margin-top: var(--zc-space-3); font-size: var(--zc-font-xs);
  color: var(--zc-text-sub); }
</style>