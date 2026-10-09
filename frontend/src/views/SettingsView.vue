<script setup>
// 设置页（Task 14 充实，Task 16 分页签）：页签「模型库」= 多模型配置 CRUD（列表/新增/编辑/删除确认/设默认），
// 页签「基础配置」= 原分组表单——模型接入（base_url / model / api_key(password) + 测试连接）、
// 执行参数（write_confirm / ask_timeout_seconds / max_tool_rounds / history_window / fs.open_folder_mode）、环境状态（PG 版本）。
// 基础配置字段映射与保存走 config store（loadConfig / save），api_key 掩码哨兵 sk-*** 由后端 PUT 过滤、不会覆盖真实密钥。
// 模型库 api_key 契约（后端 app/api/models.py）：编辑留空 = 不改（提交时填哨兵 sk-***），新建必填；GET 永远掩码回显。
import { createVNode, onMounted, ref } from 'vue'
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
  }
  modelModal.value = true
}

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
      <router-link to="/settings/skills" class="zc-skills-link" style="margin-left: auto">技能管理 →</router-link>
    </div>

    <a-tabs v-model:activeKey="activeTab" class="zc-tabs">
      <a-tab-pane key="models" tab="模型库">
        <a-button type="primary" class="zc-model-add" @click="openCreate">新增模型</a-button>
        <a-table
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
        <a-card title="模型接入" class="zc-card">
          <a-form layout="vertical">
            <a-form-item label="Base URL">
              <a-input v-model:value="store.form.baseUrl" placeholder="https://api.deepseek.com/v1" />
            </a-form-item>
            <a-form-item label="模型">
              <a-input v-model:value="store.form.model" placeholder="deepseek-chat" />
            </a-form-item>
            <a-form-item label="API Key" extra="保存后以掩码 sk-*** 显示；重新输入即覆盖">
              <a-input-password v-model:value="store.form.apiKey" placeholder="sk-***" />
            </a-form-item>
            <a-button type="primary" :loading="testing" @click="runTest">测试连接</a-button>
          </a-form>
        </a-card>

        <a-card title="执行参数" class="zc-card">
          <a-form layout="vertical">
            <a-form-item label="写入前确认">
              <a-switch v-model:checked="store.form.writeConfirm" />
            </a-form-item>
            <a-form-item label="提问等待超时（秒）" extra="0=不限制">
              <a-input-number v-model:value="store.form.askTimeoutSeconds" :min="0" style="width: 180px" />
            </a-form-item>
            <a-form-item label="最大工具轮数" extra="0=不限制">
              <a-input-number v-model:value="store.form.maxToolRounds" :min="0" style="width: 180px" />
            </a-form-item>
            <a-form-item label="历史窗口（条数）">
              <a-input-number v-model:value="store.form.historyWindow" :min="0" style="width: 180px" />
            </a-form-item>
            <a-form-item
              label="打开文件夹方式"
              extra="默认在页面里弹出内置文件管理器（网页版目录）；自动判定=本机访问直开系统资源管理器、远端访问走网页；强制直开留给确定同机的场合"
            >
              <a-select v-model:value="store.form.openFolderMode" :options="openFolderModeOptions" style="width: 260px" />
            </a-form-item>
          </a-form>
        </a-card>

        <a-card title="环境状态" class="zc-card">
          <a-spin v-if="!pg" />
          <template v-else>
            <a-tag :color="pg.ok ? 'green' : 'red'">{{ pg.ok ? 'PostgreSQL 正常' : 'PostgreSQL 异常' }}</a-tag>
            <span class="zc-pg-version">{{ pg.ok ? pg.version : pg.error }}</span>
          </template>
        </a-card>

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
  padding: 24px 0 40px;
}
.zc-page-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 0 16px;
}
.zc-back {
  padding-inline: 6px;
  color: var(--zc-text-sub);
}
.zc-back:hover {
  color: var(--zc-primary);
}
.zc-page-title {
  font-size: 18px;
  font-weight: 600;
  color: var(--zc-text);
  margin: 0;
}
.zc-card {
  margin-bottom: 16px;
  border: 1px solid var(--zc-border);
  border-radius: 6px;
}
.zc-pg-version {
  font-size: 12px;
  color: var(--zc-text-sub);
  margin-left: 8px;
}
.zc-save-bar {
  text-align: right;
}
.zc-model-add {
  margin-bottom: 12px;
}
</style>
