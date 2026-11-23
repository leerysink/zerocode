<script setup>
// 商店管理（运营台，仅创始人）：上架生命周期 / 运营编辑 / 版本详情 / 审批队列 / 分类总览。
// 门禁双层：视图 + 后端 403。卡片骨架、页头与空态来自 components/zc 薄件。
import { computed, createVNode, onMounted, ref } from 'vue'
import { Modal, message } from 'ant-design-vue'
import {
  AppstoreOutlined, AuditOutlined, DeleteOutlined, EditOutlined, ExclamationCircleOutlined,
  EyeOutlined, MenuOutlined,
  PlusOutlined, ReloadOutlined, RiseOutlined, StopOutlined,
} from '@ant-design/icons-vue'
import * as api from '../api'
import { useAuthStore } from '../stores/auth'
import { useCategoryStore } from '../stores/categories'
import ZcPageHeader from '../components/zc/ZcPageHeader.vue'
import ZcEmptyState from '../components/zc/ZcEmptyState.vue'
import ZcEntityCard from '../components/zc/ZcEntityCard.vue'
import ZcPagination from '../components/zc/ZcPagination.vue'
import { usePagedList } from '../composables/usePagedList'

const auth = useAuthStore()
const cats = useCategoryStore()
const isFounder = computed(() => !!auth.user?.is_founder)

const releases = ref([])
const review = ref([])
const tab = ref('all')
const loading = ref(false)
const reviewOpen = ref(false)
const catOpen = ref(false)
const reasonDraft = ref({})
const editOpen = ref(false)
const editRow = ref(null)
const editForm = ref({ title: '', summary: '', category: '' })
const saving = ref(false)
const detailOpen = ref(false)
const detail = ref(null)

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
// 每页 6 张卡（ADR-0071）：分页叠在状态筛选之后，counts/stats/badge 一律按全量算。
const shownPager = usePagedList(shown)
const reviewPager = usePagedList(review)
// 换 tab 就是换筛选口径，旧页码在新口径下没有对应位置，回到第 1 页最不容易看漏。
function pickTab (key) { tab.value = key; shownPager.resetPage() }
const stats = computed(() => [
  { key: 'all', label: '全部版本', value: counts.value.all },
  { key: 'published', label: '已上架', value: counts.value.published },
  { key: 'withdrawn', label: '已下架', value: counts.value.withdrawn },
  { key: 'review', label: '待审批', value: review.value.length },
])
// 分类字典（ADR-0061 §4）：条目数与在架数由后端 GET /store/categories 一起算好带回来，
// 不再在前端按当前页的 releases 拼一份假统计——那是分页与筛选之前的数字，口径不对。
const catNew = ref({ key: '', label: '', sort: 0 })
// 编辑弹框的可选项 = 启用项 ∪ 该条目当前已持有的分类：
// 停用某个分类后，挂着它的条目仍要能被保存（否则停用等于把那些条目的编辑锁死）
const catOptions = computed(() => {
  const keys = [...cats.enabledKeys]
  const cur = editForm.value.category
  if (cur && !keys.includes(cur)) keys.push(cur)
  return keys
})

async function addCategory () {
  const key = catNew.value.key.trim()
  if (!key) { message.warning('给分类一个标识'); return }
  try {
    await api.storeCreateCategory({ key, label: catNew.value.label.trim(), sort: Number(catNew.value.sort) || 0 })
    message.success('已新增分类')
    catNew.value = { key: '', label: '', sort: 0 }
    await cats.load(true)
  } catch (e) {
    message.error(e?.response?.data?.detail || '新增失败')
  }
}

async function saveCategory (c) {
  try {
    await api.storePatchCategory(c.key, { label: c.label, sort: Number(c.sort) || 0 })
    message.success(`「${c.key}」已更新`)
    await cats.load(true)
  } catch (e) {
    message.error(e?.response?.data?.detail || '保存失败')
    await cats.load(true)
  }
}

async function toggleCategory (c, enabled) {
  try {
    await api.storePatchCategory(c.key, { enabled })
    message.success(enabled ? `「${c.key}」已启用` : `「${c.key}」已停用：不再出现在上架与编辑选项里`)
  } catch (e) {
    message.error(e?.response?.data?.detail || '启停失败')
  }
  await cats.load(true)
}

async function removeCategory (c) {
  try {
    await api.storeDeleteCategory(c.key)
    message.success(`「${c.key}」已删除`)
    await cats.load(true)
  } catch (e) {
    // 有条目引用时后端回 409 并带条数：这句提示是给运营看的，别只说"失败"
    message.error(e?.response?.data?.detail || '删除失败')
  }
}

async function loadAll () {
  if (!isFounder.value) return
  loading.value = true
  try {
    const [rel, rev] = await Promise.all([
      api.storeAdminReleases(),
      api.storeApplications('review'),
    ])
    cats.load()
    releases.value = rel.data
    review.value = rev.data
    // 详情抽屉开着时刷新：按 id 从新列表里取，避免下架/编辑后抽屉里还是旧值
    if (detailOpen.value && detail.value) {
      detail.value = releases.value.find((r) => r.id === detail.value.id) || null
    }
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

// 被引用面由服务端算好（deletable + 四个计数）：让前端自己加总迟早加漏一个，
// 而加漏的那次就是真删掉别人正在用的版本。
function refuseReason (r) {
  const bits = []
  if (r.install_count) bits.push(`${r.install_count} 次安装`)
  if (r.application_count) bits.push(`${r.application_count} 条申请`)
  if (r.instance_link_count) bits.push(`${r.instance_link_count} 个已装实例链接`)
  if (r.child_release_count) bits.push(`${r.child_release_count} 个后继版本`)
  return bits.length ? `这条版本被引用着（${bits.join('、')}），不能硬删；请先下架` : ''
}

function purge (r) {
  Modal.confirm({
    title: '硬删这个版本',
    icon: createVNode(ExclamationCircleOutlined),
    content: `删除后这条版本记录不再存在，且不可恢复：${r.slug} ${r.version}（${r.title}）`,
    okText: '确认删除',
    okType: 'danger',
    async onOk () {
      try {
        await api.storePurgeRelease(r.id)
        message.success('已删除该版本')
      } catch (e) {
        // 409 的文案里带着引用计数，原样给运营看；无论如何都重取一次让列表回到真值
        message.error(e?.response?.data?.detail || '删除失败')
      }
      await loadAll()
    },
  })
}

// 运营编辑（ADR-0061 §1）：只改描述性元数据，不新增版本行、不重走审批。
// 截图增删不在这个弹框里——它属于条目内容，跟详情层（005）一起做更顺。
function openEdit (r) {
  editRow.value = r
  editForm.value = { title: r.title, summary: r.summary || '', category: r.category || '其他' }
  editOpen.value = true
}

async function saveEdit () {
  const title = (editForm.value.title || '').trim()
  if (!title) { message.warning('标题不能为空'); return }
  saving.value = true
  try {
    await api.storeUpdateRelease(editRow.value.id, {
      title, summary: editForm.value.summary || '', category: editForm.value.category,
    })
    message.success('已保存，改动立刻对所有人可见')
    editOpen.value = false
    await loadAll()
  } catch (e) {
    message.error(e?.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}
function openDetail (r) {
  detail.value = r
  detailOpen.value = true
}

function openCatManager () {
  catOpen.value = true
  cats.load(true) // 每次打开都重取：别的窗口可能刚停用或新增了分类
}

// 截图属于条目内容（ADR-0057 的发现层素材），编辑弹框只管文案三字段
async function addShot (r, ev) {
  const file = ev.target.files && ev.target.files[0]
  if (!file) return
  try {
    await api.storeAddShot(r.id, file)
    message.success('截图已上传')
    await loadAll()
  } catch (e) {
    message.error(e?.response?.data?.detail || '截图上传失败')
  }
  ev.target.value = ''
}

async function delShot (r, index) {
  try {
    await api.storeDelShot(r.id, index)
    message.success('截图已删除')
    await loadAll()
  } catch (e) {
    message.error(e?.response?.data?.detail || '截图删除失败')
  }
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
          <a-button type="primary" data-test="adm-cat-open" @click="openCatManager">
            <plus-outlined /> 分类管理
          </a-button>
        </template>
      </zc-page-header>

      <div class="zc-sadm-tabs zc-panel" role="tablist" data-test="sadm-tabs">
        <button v-for="t in TABS" :key="t.key" type="button" role="tab"
                :aria-selected="tab === t.key" :data-test="`adm-tab-${t.key}`"
                class="zc-sadm-tab" :class="{ on: tab === t.key }" @click="pickTab(t.key)">
          {{ t.label }} <span class="n">{{ counts[t.key] }}</span>
        </button>
      </div>

      <zc-empty-state v-if="loading" loading />
      <zc-empty-state v-else-if="!shown.length"
                      :description="releases.length ? '该状态下暂无版本' : '还没有应用上架'" />
      <div v-else class="zc-sadm-grid">
        <zc-entity-card v-for="r in shownPager.paged" :key="r.id" data-test="adm-card"
                        :muted="r.status === 'withdrawn'"
                        :rail="r.status === 'published' ? 'var(--zc-success)' : 'var(--zc-text-weak)'"
                        cover-clickable @cover-click="openDetail(r)">
          <template #cover>
            <img v-if="r.screenshots && r.screenshots.length" :src="r.screenshots[0]" alt="" />
            <div v-else class="zc-sadm-cover-ph"><appstore-outlined /></div>
          </template>
          <template #badge>
            <span class="zc-sadm-pill" :class="r.status">
              {{ r.status === 'published' ? '已上架' : '已下架' }}
            </span>
          </template>
          <template #title>
            <span class="zc-sadm-name" :title="r.title" data-test="adm-detail-open"
                  @click="openDetail(r)">{{ r.title }}</span>
          </template>
          <template #tags>
            <span class="zc-sadm-tag">{{ r.stack }}</span>
            <span class="zc-sadm-tag cat">{{ cats.labelOf(r.category) }}</span>
          </template>
          <template #menu>
            <!-- 菜单入口用三横（☰）而不是 ⋯ 的圆点：圆点在卡头太小、几乎看不见（同应用页语言）。
                 下架/重新上架收在这里：它们是有后果的状态变更，不常驻在卡面。 -->
            <a-dropdown :trigger="['click']" placement="bottomRight">
              <menu-outlined class="zc-sadm-menu-btn" title="更多操作" @click.stop />
              <template #overlay>
                <div class="zc-sadm-menu">
                  <div class="zc-sadm-menu-item" data-test="adm-edit" @click.stop="openEdit(r)">
                    <edit-outlined /> 编辑
                  </div>
                  <div class="zc-sadm-menu-item" data-test="adm-detail" @click.stop="openDetail(r)">
                    <eye-outlined /> 查看详情
                  </div>
                  <div v-if="r.status === 'published'" class="zc-sadm-menu-item"
                       data-test="adm-withdraw" @click.stop="withdraw(r)">
                    <stop-outlined /> 下架
                  </div>
                  <div v-else class="zc-sadm-menu-item" data-test="adm-republish"
                       @click.stop="republish(r)">
                    <rise-outlined /> 重新上架
                  </div>
                  <!-- 删除只对无引用的行开：置灰不是拦样子，点它会给出口拒绝的原因 -->
                  <div class="zc-sadm-menu-item danger" :class="{ 'is-off': !r.deletable }"
                       :title="refuseReason(r)" data-test="adm-purge"
                       @click.stop="r.deletable ? purge(r) : message.warning(refuseReason(r))">
                    <delete-outlined /> 删除
                  </div>
                </div>
              </template>
            </a-dropdown>
          </template>
          <template #summary>{{ r.summary || '作者没写简介' }}</template>
          <template #meta>
            <div class="zc-sadm-metastack">
              <div>{{ r.publisher || '未知' }} · {{ r.install_count || 0 }} 次安装 ·
                {{ fmtDate(r.created_at) }}</div>
              <div v-if="r.updated_at" class="zc-sadm-edited" data-test="adm-edited">
                已由 {{ r.editor || '创始人' }} 编辑 · {{ fmtDate(r.updated_at) }}
              </div>
            </div>
          </template>
          <template #footer>
            <a-button size="small" type="primary" ghost :disabled="!r.preview_url"
                      :title="r.preview_url ? '打开在服环境预览' : '来源应用未在服'"
                      data-test="adm-preview" @click="preview(r)">
              <eye-outlined /> 预览
            </a-button>
            <span class="zc-sadm-ver">{{ r.version }}</span>
          </template>
        </zc-entity-card>
      </div>
      <zc-pagination :pager="shownPager" label="版本列表" />
    </template>

    <a-drawer v-model:open="reviewOpen" title="审批队列" width="520" placement="right">
      <a-empty v-if="!review.length" description="没有待审批的申请" />
      <div v-for="row in reviewPager.paged" :key="row.id" class="zc-sadm-review-row">
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
      <zc-pagination :pager="reviewPager" label="审批队列" />
    </a-drawer>

    <a-drawer v-model:open="detailOpen" title="版本详情" width="560" placement="right"
              destroy-on-close data-test="adm-detail-drawer">
      <template v-if="detail">
        <div class="zc-sadm-detail-cover">
          <img v-if="detail.screenshots && detail.screenshots.length"
               :src="detail.screenshots[0]" alt="" />
          <div v-else class="zc-sadm-cover-ph"><appstore-outlined /></div>
        </div>
        <p class="zc-sadm-detail-summary">{{ detail.summary || '作者没写简介' }}</p>

        <a-descriptions :column="1" size="small" bordered>
          <a-descriptions-item label="状态">
            {{ detail.status === 'published' ? '已上架' : '已下架' }}
          </a-descriptions-item>
          <a-descriptions-item label="标识 slug">{{ detail.slug }}</a-descriptions-item>
          <a-descriptions-item label="版本">{{ detail.version }}</a-descriptions-item>
          <a-descriptions-item label="技术栈">{{ detail.stack }}</a-descriptions-item>
          <a-descriptions-item label="分类">{{ cats.labelOf(detail.category) }}</a-descriptions-item>
          <a-descriptions-item label="发布者">{{ detail.publisher || '未知' }}</a-descriptions-item>
          <a-descriptions-item label="安装 / 申请">
            {{ detail.install_count || 0 }} 次安装 · {{ detail.application_count || 0 }} 条申请
          </a-descriptions-item>
          <a-descriptions-item label="发布时间">{{ fmtDate(detail.created_at) }}</a-descriptions-item>
          <a-descriptions-item label="运营编辑">
            <span v-if="detail.updated_at" data-test="adm-detail-edited">
              已由 {{ detail.editor || '创始人' }} 编辑 · {{ fmtDate(detail.updated_at) }}
            </span>
            <span v-else>未被编辑</span>
          </a-descriptions-item>
        </a-descriptions>

        <h4 class="zc-sadm-detail-h">截图（{{ (detail.screenshots || []).length }}）</h4>
        <div class="zc-sadm-shots">
          <div v-for="(u, i) in detail.screenshots" :key="i" class="zc-sadm-shot">
            <img :src="u" alt="" />
            <a-button size="small" danger ghost data-test="adm-shot-del"
                      @click="delShot(detail, i)">删掉</a-button>
          </div>
          <a-empty v-if="!detail.screenshots || !detail.screenshots.length"
                   description="这条上架没配截图" :image-style="{ height: '48px' }" />
        </div>
        <label class="zc-sadm-shot-add">
          <plus-outlined /> 上传截图
          <input type="file" accept="image/*" data-test="adm-shot-add"
                 @change="addShot(detail, $event)" />
        </label>

        <div class="zc-sadm-detail-ops">
          <a-button type="primary" ghost :disabled="!detail.preview_url"
                    :title="detail.preview_url ? '打开在服环境预览' : '来源应用未在服'"
                    data-test="adm-detail-preview" @click="preview(detail)">
            <eye-outlined /> 打开预览
          </a-button>
          <a-button data-test="adm-detail-edit" @click="openEdit(detail)">编辑文案</a-button>
        </div>
        <p class="zc-sadm-cat-note">
          slug / 版本 / 技术栈是身份字段，运营台刻意不给改：它们牵着包目录与已装实例（ADR-0061 §1）。
        </p>
      </template>
    </a-drawer>

    <a-modal v-model:open="catOpen" title="分类管理" :footer="null" width="640">
      <div class="zc-sadm-cat-head">
        <span class="k">标识 key</span><span class="l">展示名</span><span class="s">排序</span>
        <span class="e">启用</span><span class="n">在架 / 版本</span><span class="o" />
      </div>
      <div v-for="c in cats.items" :key="c.key" class="zc-sadm-cat-row2"
           :data-test="`cat-row-${c.key}`">
        <span class="k" :title="c.key">{{ c.key }}</span>
        <a-input v-model:value="c.label" size="small" :disabled="c.key === '其他'"
                 data-test="cat-label" />
        <a-input-number v-model:value="c.sort" size="small" :min="0" class="zc-sadm-cat-sort"
                        data-test="cat-sort" />
        <a-switch :checked="c.enabled" size="small" :disabled="c.key === '其他'"
                  data-test="cat-enabled" @change="(v) => toggleCategory(c, v)" />
        <span class="n">{{ c.published_count || 0 }} / {{ c.release_count || 0 }}</span>
        <span class="o">
          <a-button size="small" type="link" data-test="cat-save" @click="saveCategory(c)">保存</a-button>
          <a-button size="small" type="link" danger :disabled="c.key === '其他'"
                    data-test="cat-del" @click="removeCategory(c)">删除</a-button>
        </span>
      </div>
      <div class="zc-sadm-cat-new">
        <a-input v-model:value="catNew.key" size="small" placeholder="新分类标识（≤20 字）"
                 data-test="cat-new-key" />
        <a-input v-model:value="catNew.label" size="small" placeholder="展示名（留空同标识）"
                 data-test="cat-new-label" />
        <a-button size="small" type="primary" data-test="cat-add" @click="addCategory">新增</a-button>
      </div>
      <p class="zc-sadm-cat-note">
        条目上存的是 key，改展示名与排序立刻影响全站、不动数据。停用只是把它从上架与编辑的可选项里
        拿掉，已挂在它上面的条目仍照常显示与编辑；还有条目引用的分类不能删除，
        「其他」是兜底分类，既不能停用也不能删除（ADR-0061 §4）。
      </p>
    </a-modal>

    <a-modal v-model:open="editOpen" :title="`编辑「${editRow?.title || ''}」`"
             ok-text="保存" cancel-text="取消" :confirm-loading="saving" @ok="saveEdit">
      <a-form layout="vertical">
        <a-form-item label="标题">
          <a-input v-model:value="editForm.title" :maxlength="100" data-test="adm-edit-title" />
        </a-form-item>
        <a-form-item label="摘要">
          <a-textarea v-model:value="editForm.summary" :rows="3" data-test="adm-edit-summary" />
        </a-form-item>
        <a-form-item label="分类">
          <a-select v-model:value="editForm.category" data-test="adm-edit-category">
            <a-select-option v-for="c in catOptions" :key="c" :value="c">{{ cats.labelOf(c) }}</a-select-option>
          </a-select>
        </a-form-item>
      </a-form>
      <p class="zc-sadm-cat-note">
        编辑只改文案与分类：不新增版本、不重走审批，保存后立刻对所有人可见，并留「谁改的」痕。
      </p>
    </a-modal>
  </div>
</template>

<style scoped>
.zc-sadm { padding: var(--zc-space-5); }
/* 状态页签条是一张面卡（外壳由全局 .zc-panel 给），卡内是胶囊页签 */
.zc-sadm-tabs { display: flex; gap: var(--zc-space-2); margin-bottom: var(--zc-space-4);
  padding: var(--zc-space-2) var(--zc-space-3); }
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
/* 卡片外壳、封面容器、等高与摘要两行截断都在 ZcEntityCard 里；这里只留运营台自己的内容样式 */
.zc-sadm-cover-ph { position: absolute; inset: 0; display: flex; align-items: center;
  justify-content: center; font-size: 44px; color: rgba(var(--zc-primary-rgb), 0.55);
  background: linear-gradient(135deg, rgba(var(--zc-primary-rgb), 0.14),
    rgba(var(--zc-success-soft-rgb), 0.22)); }
.zc-sadm-pill { padding: 2px 10px; border-radius: var(--zc-radius-pill);
  font-size: var(--zc-font-xs); line-height: 18px; }
.zc-sadm-pill.published { background: var(--zc-success-bg); color: var(--zc-success);
  border: 1px solid rgba(var(--zc-success-rgb), 0.35); }
.zc-sadm-pill.withdrawn { background: var(--zc-bg-inset); color: var(--zc-text-sub);
  border: 1px solid var(--zc-border); }
.zc-sadm-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  cursor: pointer; }
.zc-sadm-name:hover { color: var(--zc-primary); }
.zc-sadm-ver { flex: none; font-size: var(--zc-font-xs); color: var(--zc-text-weak); }
.zc-sadm-menu-btn { flex: none; font-size: 16px; line-height: 1; padding: 3px 4px; cursor: pointer;
  opacity: 0.7; transition: opacity 0.15s; color: var(--zc-text-sub); }
.zc-sadm-menu-btn:hover { opacity: 1; color: var(--zc-primary); }
.zc-sadm-menu { min-width: 148px; padding: 4px 0; background: var(--zc-bg-card);
  border-radius: var(--zc-radius-inner); box-shadow: 0 6px 16px rgba(var(--zc-shadow-rgb), 0.12); }
.zc-sadm-menu-item { display: flex; align-items: center; gap: var(--zc-space-2);
  padding: 6px 14px; font-size: var(--zc-font-sm); color: var(--zc-text);
  cursor: pointer; white-space: nowrap; }
.zc-sadm-menu-item:hover { background: rgba(var(--zc-primary-rgb), 0.08); }
.zc-sadm-menu-item.danger { color: var(--zc-danger); }
.zc-sadm-menu-item.danger:hover { background: var(--zc-danger-bg-alt); }
.zc-sadm-menu-item.is-off { color: var(--zc-text-weak); }
.zc-sadm-menu-item.is-off:hover { background: var(--zc-bg-inset); }
.zc-sadm-edited { margin-top: var(--zc-space-1); font-size: var(--zc-font-xs);
  color: var(--zc-warning-text); }
.zc-sadm-tag { padding: 1px 8px; border-radius: var(--zc-radius-pill);
  font-size: var(--zc-font-xs); background: var(--zc-bg-inset); color: var(--zc-text-sub);
  border: 1px solid var(--zc-border-soft); }
.zc-sadm-tag.cat { background: rgba(var(--zc-primary-rgb), 0.1); color: var(--zc-primary);
  border-color: transparent; }
.zc-sadm-metastack { display: flex; flex-direction: column; gap: var(--zc-space-hair); }
.zc-sadm-review-row { display: flex; flex-direction: column; gap: var(--zc-space-2);
  padding: var(--zc-space-3) 0; border-bottom: 1px solid var(--zc-border-soft); }
.zc-sadm-review-line { font-size: var(--zc-font-sm); color: var(--zc-text); }
.zc-sadm-review-sub { font-size: var(--zc-font-xs); color: var(--zc-text-sub); }
.zc-sadm-review-ops { display: flex; align-items: center; gap: var(--zc-space-2); }
.zc-sadm-cat-head, .zc-sadm-cat-row2 { display: grid; align-items: center;
  grid-template-columns: 96px minmax(0, 1fr) 76px 52px 92px 108px; gap: var(--zc-space-2); }
.zc-sadm-cat-head { padding: var(--zc-space-1) 0; font-size: var(--zc-font-xs);
  color: var(--zc-text-sub); border-bottom: 1px solid var(--zc-border-soft); }
.zc-sadm-cat-row2 { padding: var(--zc-space-2) 0; font-size: var(--zc-font-sm);
  color: var(--zc-text); border-bottom: 1px solid var(--zc-border-soft); }
.zc-sadm-cat-row2 .k, .zc-sadm-cat-head .k { overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; }
.zc-sadm-cat-row2 .n, .zc-sadm-cat-row2 .o { color: var(--zc-text-sub);
  font-size: var(--zc-font-xs); }
.zc-sadm-cat-row2 .o { display: flex; justify-content: flex-end; gap: var(--zc-space-1); }
.zc-sadm-cat-sort { width: 76px; }
.zc-sadm-cat-new { display: flex; gap: var(--zc-space-2); margin-top: var(--zc-space-3); }
.zc-sadm-cat-new :deep(.ant-input) { max-width: 200px; }
.zc-sadm-cat-n { color: var(--zc-text-sub); font-size: var(--zc-font-xs); }
.zc-sadm-cat-note { margin-top: var(--zc-space-3); font-size: var(--zc-font-xs);
  color: var(--zc-text-sub); }
/* 详情抽屉：封面 + 摘要 + 身份字段只读区 + 截图墙 + 动作 */
.zc-sadm-detail-cover { position: relative; aspect-ratio: 16 / 9; overflow: hidden;
  border-radius: var(--zc-radius-inner); background: var(--zc-bg-cover); }
.zc-sadm-detail-cover img { display: block; width: 100%; height: 100%; object-fit: cover; }
.zc-sadm-detail-summary { margin: var(--zc-space-3) 0; font-size: var(--zc-font-sm);
  color: var(--zc-text-sub); line-height: 1.6; white-space: pre-wrap; }
.zc-sadm-detail-h { margin: var(--zc-space-4) 0 var(--zc-space-2); font-size: var(--zc-font-sm);
  font-weight: 600; color: var(--zc-text); }
.zc-sadm-shots { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--zc-space-2); }
.zc-sadm-shot { display: flex; flex-direction: column; gap: var(--zc-space-1); }
.zc-sadm-shot img { width: 100%; aspect-ratio: 16 / 9; object-fit: cover;
  border: 1px solid var(--zc-border); border-radius: var(--zc-radius-inner); }
.zc-sadm-shot-add { display: inline-flex; align-items: center; gap: var(--zc-space-1);
  margin-top: var(--zc-space-3); padding: 4px 12px; cursor: pointer;
  border: 1px dashed var(--zc-border); border-radius: var(--zc-radius-pill);
  font-size: var(--zc-font-xs); color: var(--zc-text-sub); }
.zc-sadm-shot-add:hover { color: var(--zc-primary); border-color: var(--zc-primary); }
.zc-sadm-shot-add input { display: none; }
.zc-sadm-detail-ops { display: flex; gap: var(--zc-space-2); margin-top: var(--zc-space-4); }
</style>