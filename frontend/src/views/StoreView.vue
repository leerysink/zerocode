<script setup>
// 应用商店（规格 M3-a §9）：列表/申请/审批一页三视图。身份缺失弹昵称框，不进登录页。
import { onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import { AppstoreOutlined, PlusOutlined, SendOutlined } from '@ant-design/icons-vue'
import * as api from '../api'
import ActorNameModal from '../components/ActorNameModal.vue'

const tab = ref('store')
const releases = ref([])
const mine = ref([])
const review = ref([])
const me = ref(null)
const actorOpen = ref(false)
const applyOpen = ref(false)
const applyTarget = ref(null)
const applyName = ref('')
const reasonDraft = ref({})

async function load () {
  releases.value = (await api.storeReleases()).data
  mine.value = (await api.storeApplications('mine')).data
  review.value = (await api.storeApplications('review')).data
}

onMounted(async () => {
  try {
    me.value = (await api.storeMe()).data
    await load()
  } catch (e) {
    if (e?.response?.status === 401) actorOpen.value = true
    else message.error('商店加载失败：' + (e?.message || e))
  }
})

async function onIdentify (nickname) {
  me.value = (await api.storeIdentify(nickname)).data
  await load()
}

function openApply (r) { applyTarget.value = r; applyName.value = r.title; applyOpen.value = true }

async function submitApply () {
  const name = applyName.value.trim()
  if (!name) { message.warning('给应用起个名字'); return }
  await api.storeApply(applyTarget.value.id, name)
  applyOpen.value = false
  message.success('已提交申请，等发布者审批')
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
      <h2><appstore-outlined /> 应用商店</h2>
      <a-space>
        <a-button @click="load">刷新</a-button>
        <a-button type="primary" @click="gotoApps"><plus-outlined /> 新建应用</a-button>
      </a-space>
    </header>

    <a-tabs v-model:activeKey="tab">
      <a-tab-pane key="store" tab="商店">
        <a-empty v-if="!releases.length" description="还没有应用上架" />
        <a-card v-for="r in releases" :key="r.id" class="zc-card">
          <template #title>
            {{ r.title }}
            <a-tag class="zc-card-tag">{{ r.stack }}</a-tag>
            <a-tag v-if="r.installed" class="zc-card-tag">已安装</a-tag>
          </template>
          <div class="zc-store-meta">发布者 {{ r.publisher || '未知' }} · 版本 {{ r.version }}</div>
          <p class="zc-store-summary">{{ r.summary || '作者没写简介' }}</p>
          <a-space wrap>
            <a-button v-if="!r.installed" type="primary" @click="openApply(r)">
              <send-outlined /> 申请使用
            </a-button>
            <a-button v-for="i in r.installed_apps" :key="i.app_id"
                      :href="i.port ? `http://localhost:${i.port}` : undefined">
              打开 {{ i.name }}
            </a-button>
          </a-space>
        </a-card>
      </a-tab-pane>

      <a-tab-pane key="mine" tab="我的申请">
        <a-empty v-if="!mine.length" description="还没有提过申请" />
        <a-table v-else :data-source="mine" :pagination="false" row-key="id"
          :columns="[{ title: '应用', dataIndex: 'title' }, { title: '我起的名字', dataIndex: 'want_name' },
                     { title: '状态', dataIndex: 'status' }, { title: '说明', dataIndex: 'reason' }]" />
      </a-tab-pane>

      <a-tab-pane key="review">
        <template #tab><span data-test="tab-review">待我审批</span></template>
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

    <ActorNameModal v-model:open="actorOpen" @submit="onIdentify" />
  </div>
</template>

<style scoped>
.zc-store { padding: 16px 20px; }
.zc-store-head { display: flex; align-items: center; justify-content: space-between; }
.zc-store-meta { color: rgba(0, 0, 0, 0.55); font-size: 13px; }
.zc-store-summary { margin: 8px 0 12px; }
.zc-store-tip { color: rgba(0, 0, 0, 0.55); font-size: 13px; }
.zc-review-row { display: flex; align-items: center; justify-content: space-between;
  padding: 10px 0; border-bottom: 1px solid rgba(0, 0, 0, 0.06); }
/* 与应用页同纪律：类型标签紧跟应用名，8px 左间距、常规字重、与文本垂直居中 */
.zc-card-tag { margin-inline-start: 8px; font-weight: 400; vertical-align: middle; }
</style>
