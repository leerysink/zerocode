<script setup>
// 技能管理页（Ticket 001~003 / ADR-0004）：直链 + GitHub 仓库安装 + AI 生成 + 列表 + 启用开关 + 删除。
// GitHub monorepo 多命中时后端返回 candidates，这里展示可选清单、带 subpath 二次提交。
// AI 生成是两段式：generate 出草稿（可编辑）→ adopt 落盘落库；人在环，不静默安装。
import { computed, onMounted, ref } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { DeleteOutlined, EyeOutlined, RobotOutlined } from '@ant-design/icons-vue'
import {
  adoptSkill, deleteSkill, generateSkill, installSkill, listSkills, patchSkill,
  previewSkill, skillFiles, skillFileContent, skillFileRawUrl,
  previewFileContent, previewFileRawUrl,
} from '../api'
import { skillSourceLabel } from '../utils/skillSource'
import SkillDetailDrawer from '../components/SkillDetailDrawer.vue'
import ZcPageHeader from '../components/zc/ZcPageHeader.vue'

const skills = ref([])
const loading = ref(false)
const installing = ref(false)
const url = ref('')
const candidates = ref(null) // GitHub 仓库多命中候选（{path} 清单）
const candidateUrl = ref('')
const pendingPath = ref(null) // 候选清单中正在安装的那一行（避免逐行按钮 loading 串扰）

// ---- AI 生成通道（两段式：草稿 → 确认安装）----
const prompt = ref('')
const draft = ref(null)          // generate 返回体 {name, description, content, warnings}
const draftContent = ref('')     // 用户可编辑的正文副本
const generating = ref(false)
const adopting = ref(false)
// 改过正文才需要「重新生成会覆盖」确认；没改就直接再生成，不打扰
const draftDirty = computed(() => !!draft.value && draftContent.value !== draft.value.content)

function resetDraft() {
  draft.value = null
  draftContent.value = ''
}

const columns = [
  { title: '名称', dataIndex: 'name', key: 'name' },
  { title: '描述', dataIndex: 'description' },
  { title: '来源', dataIndex: 'source_type', key: 'source', width: 90 },
  { title: '状态', dataIndex: 'enabled', key: 'enabled', width: 110 },
  { title: '操作', key: 'ops', width: 120 },
]

function fmtBytes(n) {
  if (typeof n !== 'number') return ''
  return n < 1024 ? `${n} B` : `${(n / 1024).toFixed(1)} KB`
}
function subLine(record) {
  const parts = [`${record.file_count ?? 0} 文件`, fmtBytes(record.total_bytes ?? 0)]
  if (record.has_scripts) parts.push('含脚本')
  return parts.join(' · ')
}

// ---- 详情抽屉（001/002）：只读浏览母本 ----
const detailOpen = ref(false)
const detailId = ref(null)
const detailTitle = ref('技能详情')
const detailFiles = ref([])
async function openDetail(record) {
  detailId.value = record.id
  detailTitle.value = record.name
  detailFiles.value = []
  detailOpen.value = true
  try {
    detailFiles.value = (await skillFiles(record.id)).data.files
  } catch (e) {
    message.error(e?.response?.data?.detail || '文件清单加载失败')
  }
}
const detailLoadContent = (path) => skillFileContent(detailId.value, path).then((r) => r.data.content)
const detailRawUrl = (path) => skillFileRawUrl(detailId.value, path)

// ---- 安装预览（004）：两段式快照，preview==install ----
const previewOpen = ref(false)
const previewData = ref(null)
const previewConfirming = ref(false)
const previewLoadContent = (path) => previewFileContent(previewData.value.token, path).then((r) => r.data.content)
const previewRawUrl = (path) => previewFileRawUrl(previewData.value.token, path)
async function confirmPreview() {
  if (previewConfirming.value || !previewData.value) return
  previewConfirming.value = true
  try {
    const { data } = await installSkill({ token: previewData.value.token })
    previewOpen.value = false
    previewData.value = null
    finishInstall(data)
  } catch (e) {
    message.error(e?.response?.data?.detail || '安装失败')
  } finally {
    previewConfirming.value = false
  }
}

async function load() {
  loading.value = true
  try {
    skills.value = (await listSkills()).data
  } catch (e) {
    message.error(e?.response?.data?.detail || '技能列表加载失败')
  } finally {
    loading.value = false
  }
}
onMounted(load)

async function doPreview() {
  if (installing.value) return  // 点击与 Enter 两条入口统一短路，防在飞时二次提交
  const u = url.value.trim()
  if (!u) {
    message.warning('请粘贴 GitHub 仓库或 SKILL.md 链接')
    return
  }
  // 换 URL 重新预览时清掉旧候选卡：残留候选可能带错误仓库二次提交
  candidates.value = null
  candidateUrl.value = ''
  installing.value = true
  try {
    const { data } = await previewSkill({ url: u })
    if (data.candidates) {
      candidates.value = data.candidates
      candidateUrl.value = u
      message.info('检测到多个技能，请选择要预览的一个')
      return
    }
    previewData.value = data
    previewOpen.value = true
  } catch (e) {
    message.error(e?.response?.data?.detail || '预览失败')
  } finally {
    installing.value = false
  }
}

async function pickCandidate(path) {
  installing.value = true
  pendingPath.value = path
  try {
    const { data } = await previewSkill({ url: candidateUrl.value, subpath: path })
    // 二次提交仍可能返回候选（候选目录嵌套多技能）：不守卫会假成功 toast「undefined」
    if (data.candidates) {
      candidates.value = data.candidates
      message.info('该路径下仍有多个技能，请再选一次')
      return
    }
    previewData.value = data
    previewOpen.value = true
  } catch (e) {
    message.error(e?.response?.data?.detail || '预览失败')
  } finally {
    installing.value = false
    pendingPath.value = null
  }
}

function finishInstall(data) {
  candidates.value = null
  candidateUrl.value = ''
  message.success(`已安装技能：${data.name}`)
  url.value = ''
  load()
}

// ---- AI 生成：只产草稿，不直接落库（人在环）----
async function doGenerate() {
  if (generating.value) return // 在飞时短接，避免双击发出两个模型请求
  const p = prompt.value.trim()
  if (!p) {
    message.warning('请描述你想要的技能')
    return
  }
  generating.value = true
  try {
    const { data } = await generateSkill({ prompt: p })
    draft.value = data
    draftContent.value = data.content
  } catch (e) {
    resetDraft() // 失败不留半截草稿
    message.error(e?.response?.data?.detail || '生成失败，请重试')
  } finally {
    generating.value = false
  }
}

function regen() {
  if (draftDirty.value) {
    Modal.confirm({
      title: '重新生成会覆盖当前草稿',
      okText: '重新生成',
      cancelText: '继续编辑',
      onOk: doGenerate,
    })
  } else {
    doGenerate()
  }
}

async function doAdopt() {
  if (adopting.value) return
  adopting.value = true
  try {
    // name/description 不传：服务端从正文重新解析，前端传了也不信
    const { data } = await adoptSkill({ content: draftContent.value, prompt: prompt.value })
    resetDraft()
    finishInstall(data)
  } catch (e) {
    if (e?.response?.status === 409) {
      // 拖到 409 基本是同名 slug 撞车，草稿必须留着让用户改完重试
      message.error('技能标识冲突，请修改正文里的 name 后重试')
    } else {
      message.error(e?.response?.data?.detail || '安装失败')
    }
  } finally {
    adopting.value = false
  }
}

function cancelDraft() {
  resetDraft()
  // 保留 prompt 文本，方便用户微调描述后重新生成
}

// 启用开关（003）：整列互斥锁防在飞并发翻转，PATCH 失败回滚到 prev（非取反），
// 越权/已删返回 404 时按已删就地移除（复审 I-1/M-4）。
const toggling = ref(null)
async function toggleEnabled(record, checked) {
  if (toggling.value !== null) return // 在飞时忽略其他行的开关，避免多请求乱序覆盖
  const prev = record.enabled
  toggling.value = record.id
  record.enabled = checked
  try {
    await patchSkill(record.id, { enabled: checked })
  } catch (e) {
    if (e?.response?.status === 404) {
      skills.value = skills.value.filter((s) => s.id !== record.id)
      message.info('该技能已不存在，已从列表移除')
    } else {
      record.enabled = prev
      message.error(e?.response?.data?.detail || '状态更新失败')
    }
  } finally {
    toggling.value = null
  }
}

// 删除（003）：二次确认；后端先删磁盘母本再删行，前端成功后就地移除
function removeSkill(record) {
  Modal.confirm({
    title: '删除技能',
    content: `确定删除「${record.name}」吗？磁盘母本一并删除，不可恢复。`,
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      try {
        await deleteSkill(record.id)
        skills.value = skills.value.filter((s) => s.id !== record.id)
        message.success('技能已删除')
      } catch (e) {
        // 404 视为服务端已删（如其他标签页删过）：静默就地移除，别报「删除失败」（复审 M-4）
        if (e?.response?.status === 404) {
          skills.value = skills.value.filter((s) => s.id !== record.id)
        } else {
          message.error(e?.response?.data?.detail || '删除失败')
        }
      }
    },
  })
}
</script>

<template>
  <div class="zc-skills">
    <zc-page-header title="技能" subtitle="从链接安装、用 AI 生成，或管理已装的技能包" />

    <a-card class="zc-card" title="从链接安装">
      <div class="zc-install-row">
        <a-input
          v-model:value="url"
          placeholder="粘贴 GitHub 仓库链接（如 https://github.com/owner/repo）或指向 SKILL.md 的直链"
          allow-clear
          @press-enter="doPreview()"
        />
        <a-button type="primary" :disabled="installing" :loading="installing && !pendingPath"
                  data-test="skill-preview-btn"
                  @click="doPreview()">预览</a-button>
      </div>
      <p class="zc-tip">先预览整包文件再确认安装（装的就是预览的那一份）；技能遵循 Anthropic Agent Skills 标准，安装后归属当前账户，工作区不会出现技能文件。</p>
    </a-card>

    <a-card class="zc-card zc-ai-card" title="用 AI 生成">
      <!-- 输入态：一句话需求描述。生成只出草稿，不落库 -->
      <template v-if="!draft">
        <a-textarea
          v-model:value="prompt"
          class="zc-ai-prompt"
          placeholder="描述你想要的技能，例如：帮我把会议记录整理成结构化周报"
          :auto-size="{ minRows: 2, maxRows: 4 }"
        />
        <div class="zc-ai-actions">
          <a-button type="primary" :disabled="generating" :loading="generating"
                    @click="doGenerate()">
            <robot-outlined /> 生成
          </a-button>
        </div>
      </template>
      <!-- 草稿态：警告只提示不拦截（拦截在安装端），正文可编辑后再确认安装 -->
      <template v-else>
        <a-alert v-if="draft.warnings && draft.warnings.length" type="warning" show-icon>
          <template #message>
            <ul class="zc-ai-warns">
              <li v-for="(w, i) in draft.warnings" :key="i">{{ w }}</li>
            </ul>
          </template>
        </a-alert>
        <p class="zc-ai-summary">
          <strong>{{ draft.name }}</strong> — {{ draft.description }}
        </p>
        <a-textarea
          v-model:value="draftContent"
          class="zc-ai-draft"
          :auto-size="{ minRows: 6, maxRows: 20 }"
        />
        <div class="zc-ai-actions">
          <a-button type="primary" :disabled="adopting" :loading="adopting"
                    @click="doAdopt()">安装</a-button>
          <a-button :disabled="generating" :loading="generating"
                    @click="regen()">重新生成</a-button>
          <a-button @click="cancelDraft()">取消</a-button>
        </div>
      </template>
    </a-card>

    <a-card v-if="candidates" class="zc-card" title="选择要预览的技能">
      <div v-for="c in candidates" :key="c.path" class="zc-cand-row">
        <span class="zc-cand-path">{{ c.path || '（仓库根目录）' }}</span>
        <a-button size="small" type="primary" :disabled="installing"
                  :loading="pendingPath === c.path"
                  @click="pickCandidate(c.path)">预览</a-button>
      </div>
    </a-card>

    <a-card class="zc-card" data-test="skill-list-card" title="技能清单">
      <template #extra>
        <span class="zc-count-badge" data-test="skill-count">{{ skills.length }}</span>
      </template>
      <a-table
        :data-source="skills"
        :loading="loading"
        :columns="columns"
        row-key="id"
        :pagination="false"
        size="small"
      >
        <template #bodyCell="{ column, record }">
          <!-- 名称列：副信息「N 文件 · X KB · 含脚本」（ADR-0061 / ticket 003） -->
          <template v-if="column.key === 'name'">
            <div class="zc-skill-name">{{ record.name }}</div>
            <div class="zc-skill-sub" data-test="skill-subline">{{ subLine(record) }}</div>
          </template>
          <!-- 来源列：未知 source_type 原样显示，未来新增类型不必改前端 -->
          <template v-if="column.key === 'source'">
            {{ skillSourceLabel(record.source_type) }}
          </template>
          <template v-if="column.key === 'enabled'">
            <a-switch :checked="record.enabled" :loading="toggling === record.id"
                      :disabled="toggling !== null && toggling !== record.id"
                      checked-children="启用" un-checked-children="停用"
                      @change="(v) => toggleEnabled(record, v)" />
          </template>
          <template v-if="column.key === 'ops'">
            <a-button size="small" type="text" data-test="skill-detail-btn" @click="openDetail(record)">
              <eye-outlined /> 详情
            </a-button>
            <a-button size="small" danger type="text" @click="removeSkill(record)">
              <delete-outlined /> 删除
            </a-button>
          </template>
        </template>
        <template #emptyText>
          <a-empty description="还没有技能" />
        </template>
      </a-table>
    </a-card>

    <!-- 详情抽屉（只读浏览母本，001/002） -->
    <SkillDetailDrawer
      :open="detailOpen" :title="detailTitle" :files="detailFiles"
      :load-content="detailLoadContent" :raw-url="detailRawUrl"
      @close="detailOpen = false"
    />
    <!-- 安装预览抽屉（快照审视→确认安装，004） -->
    <SkillDetailDrawer
      :open="previewOpen" :title="`预览：${previewData?.name || ''}`"
      :files="previewData?.files || []" :skill-md="previewData?.skill_md ?? null"
      :load-content="previewLoadContent" :raw-url="previewRawUrl"
      confirmable :confirming="previewConfirming"
      @close="previewOpen = false" @confirm="confirmPreview"
    />
  </div>
</template>

<style scoped>
.zc-skills {
  max-width: 960px;
  margin: 0 auto;
  padding: 16px 24px 40px;
}
.zc-card {
  margin-bottom: 16px;
}
/* 清单计数徽标：贴在卡片标题行的右侧，读数用等宽数字避免跳动 */
.zc-count-badge {
  display: inline-block;
  min-width: 20px;
  padding: 0 var(--zc-space-2);
  border-radius: var(--zc-radius-pill);
  background: rgba(var(--zc-primary-rgb), 0.12);
  color: var(--zc-primary);
  font-size: var(--zc-font-xs);
  font-weight: 500;
  text-align: center;
  font-variant-numeric: tabular-nums;
}
.zc-install-row {
  display: flex;
  gap: 8px;
}
.zc-cand-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 6px 0;
}
.zc-cand-path {
  font-family: ui-monospace, Consolas, Menlo, monospace;
  font-size: 13px;
}
.zc-skill-name { font-size: 13px; }
.zc-skill-sub { font-size: 11px; color: var(--zc-text-sub); margin-top: 2px; }
.zc-tip {
  margin: 8px 0 0;
  color: var(--zc-text-sub);
  font-size: 12px;
}
.zc-ai-prompt,
.zc-ai-draft {
  width: 100%;
  font-family: ui-monospace, Consolas, Menlo, monospace;
  font-size: 13px;
}
.zc-ai-actions {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}
.zc-ai-summary {
  margin: 8px 0 4px;
  font-size: 13px;
}
.zc-ai-warns {
  margin: 0;
  padding-left: 16px;
}
</style>
