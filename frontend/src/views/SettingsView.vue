<script setup>
// 设置页（Task 14 充实，Task 16 分页签）：页签「模型库」= 多模型配置 CRUD（列表/新增/编辑/删除确认/设默认），
// 页签「基础配置」= 原分组表单——模型接入（base_url / model / api_key(password) + 测试连接）、
// 执行参数（write_confirm / clarify.enabled / clarify.ask_remind_at / ask_timeout_seconds /
// max_tool_rounds / agent.preview_max_restarts / history_window / fs.open_folder_mode）、环境状态（PG 版本）。
// 页签「运行参数」= 其余 agent.*/memory.* 可调项：上下文压缩、执行与预览、浏览器检查、应用数据库工具、
// 记忆召回。除记忆（账户级）外全是平台级键，页首用 alert 明说这件事。
// 基础配置字段映射与保存走 config store（loadConfig / save），api_key 掩码哨兵 sk-*** 由后端 PUT 过滤、不会覆盖真实密钥。
// 模型库 api_key 契约（后端 app/api/models.py）：编辑留空 = 不改（提交时填哨兵 sk-***），新建必填；GET 永远掩码回显。
import { createVNode, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Modal, message } from 'ant-design-vue'
import { ArrowLeftOutlined, ExclamationCircleOutlined } from '@ant-design/icons-vue'
import { deleteModel, createModel, listModels, pgStatus, testLLM, updateModel } from '../api'
import { useConfigStore } from '../stores/config'
import { buildModelPayload, emptyModelForm, MODEL_KIND_OPTIONS, modelKindLabel, validateModelForm } from '../utils/modelForm'

const store = useConfigStore()
// 设置页是无侧栏的独立页，回退优先走浏览器历史；直接输 URL 进来时历史为空 → 兜底回对话首页。
const router = useRouter()
function goBack() {
  if (window.history.state?.back) router.back()
  else router.push('/')
}
const pg = ref(null) // {ok, version|error}
const testing = ref(false)

// 「打开文件夹」三态，取值与 frontend/src/utils/openFolder.js 的常量一一对应
// 默认是 web（ADR-0009）：不依赖“浏览器与后端同机”这个前提，对所有人的一致行为
const openFolderModeOptions = [
  { value: 'web', label: '内置文件管理器（默认，推荐）' },
  { value: 'auto', label: '自动判定（本机访问直开）' },
  { value: 'system', label: '强制系统资源管理器' },
]

// ---- 模型库管理 ----
const activeTab = ref('models')
const models = ref([])
const modelsLoading = ref(false)
const modelModal = ref(false)
const modelSaving = ref(false)
const editingId = ref(null)
const modelForm = ref(emptyModelForm())
// 表格约定：columns + bodyCell 模板渲染（默认标记与操作列）
// 类型列（Ticket 005）：chat 与 embedding 两类档案同居一个列表，不分栏就必须看得出来哪条是向量档案，
// 否则用户设了「默认」却不知道默认的是哪一类。
const modelColumns = [
  { title: '名称', dataIndex: 'name' },
  { title: '模型', dataIndex: 'model' },
  { title: '类型', dataIndex: 'kind', width: 110 },
  { title: '能力', dataIndex: 'supports_vision', width: 90 },
  { title: 'Base URL', dataIndex: 'base_url' },
  { title: '默认', dataIndex: 'is_default', width: 80 },
  { title: '操作', key: 'action', width: 130 },
]

async function loadModels() {
  modelsLoading.value = true
  try {
    models.value = (await listModels()).data
  } catch (e) {
    message.error(e?.response?.data?.detail || '模型列表加载失败')
  } finally {
    modelsLoading.value = false
  }
}

function openCreate() {
  editingId.value = null
  modelForm.value = emptyModelForm()
  modelModal.value = true
}

function openEdit(record) {
  editingId.value = record.id
  // api_key 掩码不入表单：留空提交 = 保留原密钥（saveModel 里填哨兵）
  // kind 必须从行里带回来：后端「不传 = 不改类型」，但表单是个完整载荷，缺值会被归一成 chat
  modelForm.value = {
    name: record.name,
    base_url: record.base_url,
    api_key: '',
    model: record.model,
    kind: record.kind,
    is_default: record.is_default,
    supports_vision: !!record.supports_vision,
  }
  modelModal.value = true
}

// 类型切到向量模型时同步关掉视觉开关（开关同时置灰，载荷层再强制 false）
watch(
  () => modelForm.value.kind,
  (k) => { if (k === 'embedding') modelForm.value.supports_vision = false }
)

async function saveModel() {
  const isEdit = editingId.value != null
  const err = validateModelForm(modelForm.value, isEdit)
  if (err) {
    message.warning(err)
    return
  }
  modelSaving.value = true
  try {
    const payload = buildModelPayload(modelForm.value, isEdit)
    if (isEdit) await updateModel(editingId.value, payload)
    else await createModel(payload)
    modelModal.value = false
    message.success('已保存')
    await loadModels()
  } catch (e) {
    message.error(e?.response?.data?.detail || '保存失败')
  } finally {
    modelSaving.value = false
  }
}

function removeModel(record) {
  Modal.confirm({
    title: '删除模型',
    icon: createVNode(ExclamationCircleOutlined),
    content: `确认删除「${record.name}」？引用它的会话将回退到默认模型。`,
    async onOk() {
      try {
        await deleteModel(record.id)
        message.success('已删除')
        await loadModels()
      } catch (e) {
        message.error(e?.response?.data?.detail || '删除失败')
      }
    },
  })
}

onMounted(async () => {
  try {
    await store.loadConfig()
  } catch (e) {
    message.error('配置加载失败')
  }
  try {
    pg.value = (await pgStatus()).data
  } catch (e) {
    pg.value = { ok: false, error: '无法获取状态' }
  }
  await loadModels()
})

// 测试连接：用表单当前值发一条最小消息（后端构造 OpenAICompatLLM，10s 超时收集流式回复）
async function runTest() {
  // 掩码哨兵并非真实密钥，原样发出必然鉴权失败；提示重新输入后再测（自采决策，见 task-14-report）
  if (store.form.apiKey === 'sk-***') {
    message.warning('api_key 为掩码占位，请重新输入真实密钥后再测试')
    return
  }
  testing.value = true
  try {
    const { data } = await testLLM({
      base_url: store.form.baseUrl,
      api_key: store.form.apiKey,
      model: store.form.model,
    })
    if (data.ok) message.success(`连接成功：${data.reply}`)
    else message.error(`连接失败：${data.error}`)
  } catch (e) {
    message.error(e?.response?.data?.detail || '连接失败')
  } finally {
    testing.value = false
  }
}

async function save() {
  try {
    await store.save()
    message.success('已保存')
  } catch (e) {
    message.error(e?.response?.data?.detail || '保存失败')
  }
}
</script>

<template>
  <div class="zc-settings">
    <div class="zc-page-head">
      <a-button type="text" class="zc-back" @click="goBack"><arrow-left-outlined /> 返回</a-button>
      <h2 class="zc-page-title">设置</h2>
    </div>

    <a-tabs v-model:activeKey="activeTab" class="zc-tabs">
      <a-tab-pane key="models" tab="模型库">
        <div class="zc-toolbar">
          <a-button type="primary" @click="openCreate">新增模型</a-button>
        </div>
        <a-table
          class="zc-models"
          :data-source="models"
          :loading="modelsLoading"
          :columns="modelColumns"
          row-key="id"
          :pagination="false"
          size="small"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.dataIndex === 'kind'">
              <a-tag :color="record.kind === 'embedding' ? 'purple' : 'default'">{{ modelKindLabel(record.kind) }}</a-tag>
            </template>
            <template v-else-if="column.dataIndex === 'supports_vision'">
              <a-tag v-if="record.kind === 'chat' && record.supports_vision" color="cyan">图片</a-tag>
              <span v-else>—</span>
            </template>
            <template v-else-if="column.dataIndex === 'is_default'">
              <a-tag v-if="record.is_default" color="blue">默认</a-tag>
            </template>
            <template v-else-if="column.key === 'action'">
              <a-button type="link" size="small" @click="openEdit(record)">编辑</a-button>
              <a-button type="link" size="small" danger @click="removeModel(record)">删除</a-button>
            </template>
          </template>
        </a-table>

        <a-modal
          v-model:open="modelModal"
          :title="editingId == null ? '新增模型' : '编辑模型'"
          :confirm-loading="modelSaving"
          @ok="saveModel"
        >
          <a-form layout="vertical">
            <a-form-item label="名称" required>
              <a-input v-model:value="modelForm.name" placeholder="如 DeepSeek 官方" />
            </a-form-item>
            <a-form-item label="Base URL" required>
              <a-input v-model:value="modelForm.base_url" placeholder="https://api.deepseek.com/v1" />
            </a-form-item>
            <a-form-item label="模型名" required>
              <a-input v-model:value="modelForm.model" placeholder="deepseek-chat" />
            </a-form-item>
            <a-form-item label="类型" required extra="向量模型只参与工作区记忆的语义召回，不会出现在对话模型下拉里">
              <a-select v-model:value="modelForm.kind" :options="MODEL_KIND_OPTIONS" style="width: 220px" />
            </a-form-item>
            <a-form-item label="支持图片输入" extra="开启后该模型可用于聊天图片识别">
              <a-switch
                v-model:checked="modelForm.supports_vision"
                :disabled="modelForm.kind === 'embedding'"
              />
            </a-form-item>
            <a-form-item label="API Key" :required="editingId == null" extra="新建必填；编辑留空表示保留原密钥">
              <a-input-password v-model:value="modelForm.api_key" placeholder="sk-..." />
            </a-form-item>
            <a-form-item label="设为默认">
              <a-switch v-model:checked="modelForm.is_default" />
            </a-form-item>
          </a-form>
        </a-modal>
      </a-tab-pane>

      <a-tab-pane key="base" tab="基础配置">
        <section class="zc-group">
          <h3 class="zc-group-title">模型接入</h3>
          <div class="zc-row">
            <div class="zc-row-label">
              <span class="zc-row-name">Base URL</span>
            </div>
            <div class="zc-row-control">
              <a-input v-model:value="store.form.baseUrl" placeholder="https://api.deepseek.com/v1" />
            </div>
          </div>
          <div class="zc-row">
            <div class="zc-row-label">
              <span class="zc-row-name">模型</span>
            </div>
            <div class="zc-row-control">
              <a-input v-model:value="store.form.model" placeholder="deepseek-chat" />
            </div>
          </div>
          <div class="zc-row">
            <div class="zc-row-label">
              <span class="zc-row-name">API Key</span>
              <span class="zc-row-desc">保存后以掩码 sk-*** 显示；重新输入即覆盖</span>
            </div>
            <div class="zc-row-control">
              <a-input-password v-model:value="store.form.apiKey" placeholder="sk-***" />
            </div>
          </div>
          <div class="zc-row">
            <div class="zc-row-label">
              <span class="zc-row-name">支持图片输入</span>
              <span class="zc-row-desc">仅在未配置默认对话模型时生效；有默认模型时以模型库设置为准</span>
            </div>
            <div class="zc-row-control">
              <a-switch v-model:checked="store.form.supportsVision" />
            </div>
          </div>
          <div class="zc-group-actions">
            <a-button type="primary" :loading="testing" @click="runTest">测试连接</a-button>
          </div>
        </section>

        <section class="zc-group">
          <h3 class="zc-group-title">确认策略</h3>
          <div class="zc-row">
            <div class="zc-row-label">
              <span class="zc-row-name">写入前确认</span>
              <span class="zc-row-desc">AI 落盘前先给你看改动再动手，覆盖 write/edit/delete/move/revert 五个工具。数据库写入不可回滚，恒确认，不受这一项影响。</span>
            </div>
            <div class="zc-row-control">
              <a-switch v-model:checked="store.form.writeConfirm" />
            </div>
          </div>
          <div class="zc-row">
            <div class="zc-row-label">
              <span class="zc-row-name">重启预览需确认</span>
              <span class="zc-row-desc">只在预览正在运行时拦一道：重启会中断你正在用的调试。预览停着时模型直接启动、不问；关掉则运行中的也直接重启。这两项都是平台级配置，保存后对所有账号生效。</span>
            </div>
            <div class="zc-row-control">
              <a-switch v-model:checked="store.form.previewConfirmRunning" />
            </div>
          </div>
        </section>

        <section class="zc-group">
          <h3 class="zc-group-title">执行参数</h3>
          <div class="zc-row">
            <div class="zc-row-label">
              <span class="zc-row-name">需求澄清模式</span>
              <span class="zc-row-desc">开启后 AI 改文件前会先跟你对齐需求（先给解读、再逐题确认）；这一项按账户生效，关掉只影响你自己</span>
            </div>
            <div class="zc-row-control">
              <a-switch v-model:checked="store.form.clarifyEnabled" />
            </div>
          </div>
          <div class="zc-row">
            <div class="zc-row-label">
              <span class="zc-row-name">提问提醒题数</span>
              <span class="zc-row-desc">本轮问满 N 题时提醒模型停止追问（软提醒，不禁止提问）；0=不提醒</span>
            </div>
            <div class="zc-row-control">
              <a-input-number v-model:value="store.form.askRemindAt" :min="0" style="width: 180px" />
            </div>
          </div>
          <div class="zc-row">
            <div class="zc-row-label">
              <span class="zc-row-name">提问等待超时（秒）</span>
              <span class="zc-row-desc">0=不限制</span>
            </div>
            <div class="zc-row-control">
              <a-input-number v-model:value="store.form.askTimeoutSeconds" :min="0" style="width: 180px" />
            </div>
          </div>
          <div class="zc-row">
            <div class="zc-row-label">
              <span class="zc-row-name">最大工具轮数</span>
              <span class="zc-row-desc">0=不限制</span>
            </div>
            <div class="zc-row-control">
              <a-input-number v-model:value="store.form.maxToolRounds" :min="0" style="width: 180px" />
            </div>
          </div>
          <div class="zc-row">
            <div class="zc-row-label">
              <span class="zc-row-name">预览重启次数上限</span>
              <span class="zc-row-desc">单次任务内启动/重启预览的次数上限；0=不限制</span>
            </div>
            <div class="zc-row-control">
              <a-input-number v-model:value="store.form.previewMaxRestarts" :min="0" style="width: 180px" />
            </div>
          </div>
          <div class="zc-row">
            <div class="zc-row-label">
              <span class="zc-row-name">历史窗口（条数）</span>
            </div>
            <div class="zc-row-control">
              <a-input-number v-model:value="store.form.historyWindow" :min="0" style="width: 180px" />
            </div>
          </div>
          <div class="zc-row">
            <div class="zc-row-label">
              <span class="zc-row-name">打开文件夹方式</span>
              <span class="zc-row-desc">默认在页面里弹出内置文件管理器（网页版目录）；自动判定=本机访问直开系统资源管理器、远端访问走网页；强制直开留给确定同机的场合</span>
            </div>
            <div class="zc-row-control">
              <a-select v-model:value="store.form.openFolderMode" :options="openFolderModeOptions" style="width: 260px" />
            </div>
          </div>
        </section>

        <section class="zc-group">
          <h3 class="zc-group-title">环境状态</h3>
          <div class="zc-row">
            <div class="zc-row-label">
              <span class="zc-row-name">PostgreSQL</span>
            </div>
            <div class="zc-row-control">
              <a-spin v-if="!pg" />
              <template v-else>
                <a-tag :color="pg.ok ? 'green' : 'red'">{{ pg.ok ? '正常' : '异常' }}</a-tag>
                <span class="zc-pg-version">{{ pg.ok ? pg.version : pg.error }}</span>
              </template>
            </div>
          </div>
        </section>

        <div class="zc-save-bar">
          <a-button type="primary" :loading="store.saving" @click="save">保存</a-button>
        </div>
      </a-tab-pane>

      <a-tab-pane key="agent" tab="运行参数">
        <a-alert
          class="zc-agent-note"
          type="info"
          show-icon
          message="除「记忆召回」与「子任务委派上限」按账户生效外，本页其余项都是平台级配置：保存后对所有账号的会话生效。数值框的上下限与后端夹取区间一致，超出的值后端会回落。"
        />
        <section class="zc-group">
          <h3 class="zc-group-title">上下文压缩</h3>
          <div class="zc-row">
            <div class="zc-row-label">
              <span class="zc-row-name">自动压缩阈值（字符）</span>
              <span class="zc-row-desc">水位之后的消息估算超过这个字符数就自动摘要，0=关闭自动压缩</span>
            </div>
            <div class="zc-row-control">
              <a-input-number v-model:value="store.form.autoCompactChars" :min="0" :step="1000" style="width: 180px" />
            </div>
          </div>
          <div class="zc-row">
            <div class="zc-row-label">
              <span class="zc-row-name">压缩最少新增消息数</span>
              <span class="zc-row-desc">距上次压缩至少新增这么多条才允许再压，0=不设下限</span>
            </div>
            <div class="zc-row-control">
              <a-input-number v-model:value="store.form.compactMinNew" :min="0" style="width: 180px" />
            </div>
          </div>
          <div class="zc-row">
            <div class="zc-row-label">
              <span class="zc-row-name">压缩保留原文条数</span>
              <span class="zc-row-desc">最近 N 条消息不进摘要，保持原文</span>
            </div>
            <div class="zc-row-control">
              <a-input-number v-model:value="store.form.compactKeepRecent" :min="1" style="width: 180px" />
            </div>
          </div>
        </section>

        <section class="zc-group">
          <h3 class="zc-group-title">执行与预览</h3>
          <div class="zc-row">
            <div class="zc-row-label">
              <span class="zc-row-name">插话队列上限（条）</span>
              <span class="zc-row-desc">执行中最多排几条插话，0=禁用插话</span>
            </div>
            <div class="zc-row-control">
              <a-input-number v-model:value="store.form.steeringQueueMax" :min="0" style="width: 180px" />
            </div>
          </div>
          <div class="zc-row">
            <div class="zc-row-label">
              <span class="zc-row-name">预览启动等待（秒）</span>
              <span class="zc-row-desc">preview_restart 单次最长等待，0..900</span>
            </div>
            <div class="zc-row-control">
              <a-input-number v-model:value="store.form.previewWaitSeconds" :min="0" :max="900" style="width: 180px" />
            </div>
          </div>
          <div class="zc-row">
            <div class="zc-row-label">
              <span class="zc-row-name">预览日志行数</span>
              <span class="zc-row-desc">返回给模型的最近日志行数，10..300</span>
            </div>
            <div class="zc-row-control">
              <a-input-number v-model:value="store.form.previewLogLines" :min="10" :max="300" style="width: 180px" />
            </div>
          </div>
          <div class="zc-row">
            <div class="zc-row-label">
              <span class="zc-row-name">单批委派子任务上限（个）</span>
              <span class="zc-row-desc">账户级、只影响自己；一次 delegate 最多并行几个只读子 agent，1..8</span>
            </div>
            <div class="zc-row-control">
              <a-input-number v-model:value="store.form.subrunMaxTasks" :min="1" :max="8" style="width: 180px" />
            </div>
          </div>
          <div class="zc-row">
            <div class="zc-row-label">
              <span class="zc-row-name">子任务轮数上限（轮）</span>
              <span class="zc-row-desc">账户级、只影响自己；每个子 agent 最多跑几轮，0 表示不限制（有限值最大 50）</span>
            </div>
            <div class="zc-row-control">
              <a-input-number v-model:value="store.form.subrunMaxRounds" :min="0" :max="50" style="width: 180px" />
            </div>
          </div>
        </section>

        <section class="zc-group">
          <h3 class="zc-group-title">浏览器检查</h3>
          <div class="zc-row">
            <div class="zc-row-label">
              <span class="zc-row-name">只读检查次数上限（次/任务）</span>
              <span class="zc-row-desc">0=不限，最大 100</span>
            </div>
            <div class="zc-row-control">
              <a-input-number v-model:value="store.form.browserMaxPerRun" :min="0" :max="100" style="width: 180px" />
            </div>
          </div>
          <div class="zc-row">
            <div class="zc-row-label">
              <span class="zc-row-name">交互步数上限（步/任务）</span>
              <span class="zc-row-desc">0=不限，最大 200</span>
            </div>
            <div class="zc-row-control">
              <a-input-number v-model:value="store.form.browserMaxStepsPerRun" :min="0" :max="200" style="width: 180px" />
            </div>
          </div>
          <div class="zc-row">
            <div class="zc-row-label">
              <span class="zc-row-name">单步超时（秒）</span>
              <span class="zc-row-desc">打开页面与检查的 HTTP 超时，10..120</span>
            </div>
            <div class="zc-row-control">
              <a-input-number v-model:value="store.form.browserTimeoutSeconds" :min="10" :max="120" style="width: 180px" />
            </div>
          </div>
          <div class="zc-row">
            <div class="zc-row-label">
              <span class="zc-row-name">断言等待超时（毫秒）</span>
              <span class="zc-row-desc">expect 轮询上限，500..8000</span>
            </div>
            <div class="zc-row-control">
              <a-input-number v-model:value="store.form.browserExpectTimeoutMs" :min="500" :max="8000" :step="100" style="width: 180px" />
            </div>
          </div>
          <div class="zc-row">
            <div class="zc-row-label">
              <span class="zc-row-name">页面静置等待（毫秒）</span>
              <span class="zc-row-desc">导航后留给动画的时间，0..5000</span>
            </div>
            <div class="zc-row-control">
              <a-input-number v-model:value="store.form.browserSettleMs" :min="0" :max="5000" :step="100" style="width: 180px" />
            </div>
          </div>
          <div class="zc-row">
            <div class="zc-row-label">
              <span class="zc-row-name">交互元素清单条数</span>
              <span class="zc-row-desc">喂给模型的可点元素清单长度，1..200，调大即调高每轮 token</span>
            </div>
            <div class="zc-row-control">
              <a-input-number v-model:value="store.form.browserInteractiveMax" :min="1" :max="200" style="width: 180px" />
            </div>
          </div>
        </section>

        <section class="zc-group">
          <h3 class="zc-group-title">联网工具</h3>
          <div class="zc-row">
            <div class="zc-row-label">
              <span class="zc-row-name">网页读取次数上限（次/任务）</span>
              <span class="zc-row-desc">web_fetch 每次任务最多抓几个页面，0=不限，最大 200；公网走查的步数沿用上面的交互步数上限</span>
            </div>
            <div class="zc-row-control">
              <a-input-number v-model:value="store.form.webMaxFetchesPerRun" :min="0" :max="200" style="width: 180px" />
            </div>
          </div>
        </section>

        <section class="zc-group">
          <h3 class="zc-group-title">应用数据库工具</h3>
          <div class="zc-row">
            <div class="zc-row-label">
              <span class="zc-row-name">查询结果行数上限</span>
              <span class="zc-row-desc">返回给模型的行数，10..1000</span>
            </div>
            <div class="zc-row-control">
              <a-input-number v-model:value="store.form.dbRowsLimit" :min="10" :max="1000" style="width: 180px" />
            </div>
          </div>
          <div class="zc-row">
            <div class="zc-row-label">
              <span class="zc-row-name">断言比对行数上限</span>
              <span class="zc-row-desc">rows 断言两侧各比对的行数，1..1000</span>
            </div>
            <div class="zc-row-control">
              <a-input-number v-model:value="store.form.dbRowsAssertMax" :min="1" :max="1000" style="width: 180px" />
            </div>
          </div>
        </section>

        <section class="zc-group">
          <h3 class="zc-group-title">记忆召回</h3>
          <div class="zc-row">
            <div class="zc-row-label">
              <span class="zc-row-name">注入召回记忆</span>
              <span class="zc-row-desc">关掉只是不再往提示词里塞记忆，AI 仍可写入记忆</span>
            </div>
            <div class="zc-row-control">
              <a-switch v-model:checked="store.form.memoryEnabled" />
            </div>
          </div>
          <div class="zc-row">
            <div class="zc-row-label">
              <span class="zc-row-name">召回条数</span>
              <span class="zc-row-desc">每次最多带几条记忆，1..100</span>
            </div>
            <div class="zc-row-control">
              <a-input-number v-model:value="store.form.memoryTopK" :min="1" :max="100" style="width: 180px" />
            </div>
          </div>
          <div class="zc-row">
            <div class="zc-row-label">
              <span class="zc-row-name">相似度阈值</span>
              <span class="zc-row-desc">-1..1；-1 表示一条都不因阈值被滤掉</span>
            </div>
            <div class="zc-row-control">
              <a-input-number v-model:value="store.form.memoryMinSimilarity" :min="-1" :max="1" :step="0.05" style="width: 180px" />
            </div>
          </div>
          <div class="zc-row">
            <div class="zc-row-label">
              <span class="zc-row-name">记忆注入预算（token）</span>
              <span class="zc-row-desc">1..20000；召回按分数排序逐条累计，触顶的整条丢弃</span>
            </div>
            <div class="zc-row-control">
              <a-input-number v-model:value="store.form.memoryMaxTokens" :min="1" :max="20000" :step="100" style="width: 180px" />
            </div>
          </div>
        </section>

        <div class="zc-save-bar">
          <a-button type="primary" :loading="store.saving" @click="save">保存</a-button>
        </div>
      </a-tab-pane>
    </a-tabs>
  </div>
</template>

<style scoped>
/* 面板宽度百分比：宽屏下表单区居中，窄屏自适应占满 */
.zc-settings {
  width: 62%;
  max-width: 720px;
  min-width: 320px;
  margin: 0 auto;
  padding: var(--zc-space-5) 0 var(--zc-space-4);
}
.zc-page-head {
  display: flex;
  align-items: center;
  gap: var(--zc-space-2);
  margin: 0 0 var(--zc-space-4);
}
.zc-back {
  padding-inline: var(--zc-space-1);
  color: var(--zc-text-sub);
}
.zc-back:hover {
  color: var(--zc-primary);
}
.zc-page-title {
  font-size: var(--zc-font-lg);
  font-weight: 600;
  color: var(--zc-text);
  margin: 0;
}
.zc-agent-note {
  margin-bottom: var(--zc-space-4);
}
/* 分组：标题 + 细线替代卡片外壳，组间大留白 */
.zc-group + .zc-group {
  margin-top: var(--zc-space-5);
}
.zc-group-title {
  font-size: var(--zc-font-md);
  font-weight: 600;
  color: var(--zc-text);
  margin: 0;
  padding-bottom: var(--zc-space-2);
  border-bottom: 1px solid var(--zc-border);
}
/* 设置行：左标签 + 说明，右控件；组内行用浅分隔线 */
.zc-row {
  display: flex;
  align-items: center;
  gap: var(--zc-space-4);
  padding: var(--zc-space-3) 0;
}
.zc-row + .zc-row {
  border-top: 1px solid var(--zc-border-soft);
}
.zc-row-label {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--zc-space-hair);
}
.zc-row-name {
  font-size: var(--zc-font-sm);
  color: var(--zc-text);
}
.zc-row-desc {
  font-size: var(--zc-font-xs);
  color: var(--zc-text-sub);
  line-height: 1.6;
}
.zc-row-control {
  flex: 0 0 320px;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--zc-space-2);
}
.zc-group-actions {
  display: flex;
  justify-content: flex-end;
  padding-top: var(--zc-space-3);
}
.zc-pg-version {
  font-size: var(--zc-font-xs);
  color: var(--zc-text-sub);
}
/* 吸底保存条：长页面滚动时随时可保存 */
.zc-save-bar {
  position: sticky;
  bottom: 0;
  display: flex;
  justify-content: flex-end;
  margin-top: var(--zc-space-4);
  padding: var(--zc-space-3) 0;
  background: var(--zc-bg-card);
  border-top: 1px solid var(--zc-border);
}
.zc-toolbar {
  margin-bottom: var(--zc-space-3);
}
/* 模型库表格：表头弱化、字号收进字阶 */
.zc-models :deep(.ant-table) {
  font-size: var(--zc-font-sm);
}
.zc-models :deep(.ant-table-thead > tr > th) {
  background: var(--zc-bg-card-soft);
  color: var(--zc-text-sub);
  font-size: var(--zc-font-xs);
  font-weight: 500;
}
/* 窄屏：设置行改竖排，控件占满 */
@media (max-width: 640px) {
  .zc-row {
    flex-direction: column;
    align-items: stretch;
    gap: var(--zc-space-2);
  }
  .zc-row-control {
    flex-basis: auto;
    justify-content: flex-start;
  }
}
</style>