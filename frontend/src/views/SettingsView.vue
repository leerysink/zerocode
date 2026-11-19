<script setup>
// 设置中心（009 重构）：页内左侧二级导航 + 右侧分组内容，切换不碰路由。
//   分区 = 模型库 / 模型接入 / Agent 行为 / 上下文与记忆 / 联网与工具 / 环境与预览 / 外观 / 账号。
// 刻意不开 /settings/* 子路由：/skills 在 5ec4baf 就是为了避开父路径高亮串台才升为一级入口（ADR-0062 §4）。
//
// 配置行改成 SECTIONS 数据表驱动（34 个键一张表），理由有二：搬迁 8 个分区最容易出的事是漏项与
// 重复绑定，表结构能让测试逐个核；以及每行的 min/max 必须与后端夹取区间一致，抄在模板里没人对得上。
// 老写法是在模板里手写 store.form.xxx，那份守卫改成核这张表（见 SettingsView.test.js 的说明）。
//
// 模型库 api_key 契约（后端 app/api/models.py）：编辑留空 = 不改（提交时填哨兵 sk-***），
// 新建必填；GET 永远掩码回显。基础配置字段映射与保存走 config store（loadConfig / save）。
import { createVNode, computed, onMounted, ref, watch } from 'vue'
import { Modal, message } from 'ant-design-vue'
import {
  DeleteOutlined, ExclamationCircleOutlined, MenuOutlined,
} from '@ant-design/icons-vue'
import { authLogout, deleteModel, createModel, listModels, pgStatus, testLLM, updateModel } from '../api'
import { useAuthStore } from '../stores/auth'
import { useConfigStore } from '../stores/config'
import { useThemeStore } from '../stores/theme'
import { buildModelPayload, emptyModelForm, MODEL_KIND_OPTIONS, modelKindLabel, validateModelForm } from '../utils/modelForm'
import ZcPageHeader from '../components/zc/ZcPageHeader.vue'
import { openFolderModeOptions, SETTINGS_SECTIONS as SECTIONS } from '../utils/settingsSections'

const store = useConfigStore()
const themeStore = useThemeStore()
const auth = useAuthStore()
const pg = ref(null) // {ok, version|error}
const testing = ref(false)
const active = ref('models')

const current = computed(() => SECTIONS.find((s) => s.key === active.value) || SECTIONS[0])
// 只有吃 config store 的分区才需要保存条
const hasForm = computed(() => !!current.value.groups?.length)

// ---- 模型库管理 ----
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

// 删除是这一行唯一的破坏性动作，收进 ☰（同应用页的分层纪律）；编辑留在行上直点
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

async function doLogout() {
  try { await authLogout() } catch { /* 服务端本就无会话：当作登出成功 */ }
  window.location.assign('/login') // token 已轮换，留在原地只会处处 401
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
    <zc-page-header title="设置" subtitle="模型、Agent 行为、工具量闸与外观偏好" />

    <div class="zc-set-body">
      <nav class="zc-set-nav" aria-label="设置分区">
        <button v-for="s in SECTIONS" :key="s.key" type="button"
                :aria-current="active === s.key ? 'true' : undefined"
                :data-test="`set-nav-${s.key}`"
                class="zc-set-nav-item" :class="{ on: active === s.key }"
                @click="active = s.key">
          {{ s.label }}
        </button>
      </nav>

      <main class="zc-set-main">
        <h3 class="zc-sec-title">{{ current.label }}</h3>
        <p class="zc-sec-desc">{{ current.desc }}</p>
        <!-- 原页签版有一条页首 alert 说明「哪些按账户生效」，分区化后不能丢：
             用户看不出某项改了会不会影响别人，就会拿它当个人偏好乱调 -->
        <p v-if="hasForm" class="zc-set-note" data-test="set-note-scope">
          除标注「账户级、只影响自己」的项外，本页其余项都是平台级配置：保存后对所有账号的会话生效。
          数值框的上下限与后端夹取区间一致，超出的值后端会回落。
        </p>

        <template v-if="active === 'models'">
          <div class="zc-toolbar">
            <a-button type="primary" data-test="set-model-new" @click="openCreate">新增模型</a-button>
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
                <a-dropdown :trigger="['click']" placement="bottomRight">
                  <menu-outlined class="zc-set-menu-btn" title="更多操作" @click.stop />
                  <template #overlay>
                    <div class="zc-set-menu">
                      <div class="zc-set-menu-item danger" @click.stop="removeModel(record)">
                        <delete-outlined /> 删除模型
                      </div>
                    </div>
                  </template>
                </a-dropdown>
              </template>
            </template>
          </a-table>
        </template>

        <template v-else-if="active === 'appearance'">
          <section class="zc-group">
            <h3 class="zc-group-title">主题</h3>
            <div class="zc-row">
              <div class="zc-row-label">
                <span class="zc-row-name">当前配色</span>
                <span class="zc-row-desc">深浅色即时生效并记住；与右上角账户栏的开关是同一个偏好。登录页固定深色，不随这项变化（ADR-0058）。</span>
              </div>
              <div class="zc-row-control">
                <a-button data-test="set-theme-toggle" @click="themeStore.toggle()">
                  切到{{ themeStore.theme === 'dark' ? '浅色' : '深色' }}
                </a-button>
                <a-tag class="zc-set-theme-now" :color="themeStore.theme === 'dark' ? 'default' : 'gold'">
                  当前：{{ themeStore.theme === 'dark' ? '深色' : '浅色' }}
                </a-tag>
              </div>
            </div>
          </section>
        </template>

        <template v-else-if="active === 'account'">
          <section class="zc-group">
            <h3 class="zc-group-title">当前身份</h3>
            <div class="zc-row">
              <div class="zc-row-label">
                <span class="zc-row-name">昵称</span>
              </div>
              <div class="zc-row-control">
                <span class="zc-set-value" data-test="set-account-name">{{ auth.user?.nickname || '未登录' }}</span>
                <a-tag v-if="auth.user?.is_founder" color="gold">创始人</a-tag>
              </div>
            </div>
            <div class="zc-row">
              <div class="zc-row-label">
                <span class="zc-row-name">登出</span>
                <span class="zc-row-desc">登出后令牌即失效，会跳到登录页；本机的工作区与数据不受影响。</span>
              </div>
              <div class="zc-row-control">
                <a-button danger data-test="set-logout" @click="doLogout">登出</a-button>
              </div>
            </div>
          </section>
        </template>

        <template v-else>
          <section v-for="g in current.groups" :key="g.title" class="zc-group">
            <h3 class="zc-group-title">{{ g.title }}</h3>
            <div v-for="row in g.rows" :key="row.form" class="zc-row">
              <div class="zc-row-label">
                <span class="zc-row-name">{{ row.label }}</span>
                <span v-if="row.desc" class="zc-row-desc">{{ row.desc }}</span>
              </div>
              <div class="zc-row-control">
                <a-switch v-if="row.type === 'switch'" v-model:checked="store.form[row.form]" />
                <a-input-number v-else-if="row.type === 'number'" v-model:value="store.form[row.form]"
                                :min="row.min" :max="row.max" :step="row.step || 1" style="width: 180px" />
                <a-input v-else-if="row.type === 'text'" v-model:value="store.form[row.form]"
                         :placeholder="row.placeholder" />
                <a-input-password v-else-if="row.type === 'password'" v-model:value="store.form[row.form]"
                                  :placeholder="row.placeholder" />
                <a-select v-else-if="row.type === 'select'" v-model:value="store.form[row.form]"
                          :options="row.options" style="width: 260px" />
              </div>
            </div>

            <div v-if="g.title === '模型接入'" class="zc-group-actions">
              <a-button type="primary" :loading="testing" data-test="set-test" @click="runTest">测试连接</a-button>
            </div>
            <div v-if="g.title === '执行与预览'" class="zc-row">
              <div class="zc-row-label">
                <span class="zc-row-name">PostgreSQL</span>
                <span class="zc-row-desc">平台库读数，只看不改</span>
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
        </template>

        <div v-if="hasForm" class="zc-save-bar">
          <a-button type="primary" :loading="store.saving" data-test="set-save" @click="save">保存</a-button>
        </div>
      </main>
    </div>

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
  </div>
</template>

<style scoped>
/* 页体两栏：左窄导航 + 右内容区。整体宽度比旧版放宽——导航占了 168px，
   内容区再挤在 720px 里会让长说明换行成三段 */
.zc-settings {
  width: 86%;
  max-width: 1080px;
  min-width: 320px;
  margin: 0 auto;
  padding: var(--zc-space-5) 0 var(--zc-space-4);
}
.zc-set-body { display: flex; align-items: flex-start; gap: var(--zc-space-5); }
.zc-set-nav { display: flex; flex-direction: column; gap: var(--zc-space-1);
  flex: 0 0 168px; position: sticky; top: var(--zc-space-4); }
.zc-set-nav-item { text-align: left; border: none; cursor: pointer; padding: 6px 12px;
  border-radius: var(--zc-radius-inner); background: transparent;
  color: var(--zc-text-sub); font-size: var(--zc-font-sm);
  transition: background 0.15s, color 0.15s; }
.zc-set-nav-item:hover { background: var(--zc-bg-inset); color: var(--zc-text); }
.zc-set-nav-item.on { background: rgba(var(--zc-primary-rgb), 0.12);
  color: var(--zc-primary); font-weight: 500; }
.zc-set-main { flex: 1; min-width: 0; }
.zc-sec-title { margin: 0; font-size: var(--zc-font-lg); font-weight: 600; color: var(--zc-text); }
.zc-sec-desc { margin: var(--zc-space-1) 0 var(--zc-space-4); font-size: var(--zc-font-sm);
  color: var(--zc-text-sub); }
.zc-set-note { margin: 0 0 var(--zc-space-4); padding: var(--zc-space-2) var(--zc-space-3);
  font-size: var(--zc-font-xs); color: var(--zc-text-sub); line-height: 1.6;
  background: var(--zc-bg-inset); border-left: 3px solid rgba(var(--zc-primary-rgb), 0.45);
  border-radius: var(--zc-radius-inner); }
/* 分组：标题 + 细线替代卡片外壳，组间大留白 */
.zc-group + .zc-group { margin-top: var(--zc-space-5); }
.zc-group-title {
  font-size: var(--zc-font-md);
  font-weight: 600;
  color: var(--zc-text);
}
.zc-row {
  display: flex;
  align-items: flex-start;
  gap: var(--zc-space-4);
  padding: var(--zc-space-3) 0;
  border-top: 1px solid var(--zc-border-soft);
}
.zc-row-label { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: var(--zc-space-1); }
.zc-row-name { font-size: var(--zc-font-sm); color: var(--zc-text); }
.zc-row-desc { font-size: var(--zc-font-xs); color: var(--zc-text-sub); line-height: 1.6; }
.zc-row-control { flex: 0 0 auto; display: flex; align-items: center; gap: var(--zc-space-2); }
.zc-group-actions { margin-top: var(--zc-space-3); }
.zc-set-value { font-size: var(--zc-font-sm); color: var(--zc-text); }
.zc-pg-version { font-size: var(--zc-font-xs); color: var(--zc-text-sub); }
.zc-toolbar { display: flex; justify-content: flex-end; margin-bottom: var(--zc-space-3); }
/* 保存条吸底：分区内容长短不一，滚到哪都能存 */
.zc-save-bar {
  position: sticky;
  bottom: 0;
  display: flex;
  justify-content: flex-end;
  gap: var(--zc-space-2);
  padding: var(--zc-space-3) 0;
  margin-top: var(--zc-space-4);
  background: var(--zc-bg);
  border-top: 1px solid var(--zc-border-soft);
}
.zc-models { font-size: var(--zc-font-xs); }
.zc-set-menu-btn { font-size: var(--zc-font-md); line-height: 1; padding: 3px 4px; cursor: pointer;
  opacity: 0.7; color: var(--zc-text-sub); }
.zc-set-menu-btn:hover { opacity: 1; color: var(--zc-primary); }
.zc-set-menu { min-width: 148px; padding: 4px 0; background: var(--zc-bg-card);
  border-radius: var(--zc-radius-inner); box-shadow: 0 6px 16px rgba(var(--zc-shadow-rgb), 0.12); }
.zc-set-menu-item { display: flex; align-items: center; gap: var(--zc-space-2);
  padding: 6px 14px; font-size: var(--zc-font-sm); color: var(--zc-text); cursor: pointer; }
.zc-set-menu-item:hover { background: rgba(var(--zc-primary-rgb), 0.08); }
.zc-set-menu-item.danger { color: var(--zc-danger); }
.zc-set-menu-item.danger:hover { background: var(--zc-danger-bg-alt); }
@media (max-width: 900px) {
  .zc-set-body { flex-direction: column; }
  .zc-set-nav { position: static; flex: none; flex-direction: row; flex-wrap: wrap; }
}
</style>
