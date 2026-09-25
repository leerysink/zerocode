<script setup>
// 技能管理页（Ticket 001 / ADR-0004）：本票只做「直链安装 + 列表」。
// 启用开关与删除（PATCH/DELETE）在 003 补，GitHub 仓库安装在 002 补——届时 install 返回
// candidates 会走候选选择。当前从设置页头部进入，回退优先浏览器历史、兜底回设置页。
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { ArrowLeftOutlined } from '@ant-design/icons-vue'
import { installSkill, listSkills } from '../api'

const router = useRouter()
function goBack() {
  if (window.history.state?.back) router.back()
  else router.push('/settings')
}

const skills = ref([])
const loading = ref(false)
const installing = ref(false)
const url = ref('')

const columns = [
  { title: '名称', dataIndex: 'name' },
  { title: '描述', dataIndex: 'description' },
  { title: '来源', dataIndex: 'source_type', width: 90 },
  { title: '状态', dataIndex: 'enabled', key: 'enabled', width: 80 },
]

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

async function doInstall() {
  const u = url.value.trim()
  if (!u) {
    message.warning('请粘贴 SKILL.md 链接')
    return
  }
  installing.value = true
  try {
    const { data } = await installSkill({ url: u })
    message.success(`已安装技能：${data.name}`)
    url.value = ''
    await load()
  } catch (e) {
    message.error(e?.response?.data?.detail || '安装失败')
  } finally {
    installing.value = false
  }
}
</script>

<template>
  <div class="zc-skills">
    <div class="zc-page-head">
      <a-button type="text" class="zc-back" @click="goBack"><arrow-left-outlined /> 返回</a-button>
      <h2 class="zc-page-title">技能</h2>
    </div>

    <a-card class="zc-card" title="从链接安装">
      <div class="zc-install-row">
        <a-input
          v-model:value="url"
          placeholder="粘贴指向 SKILL.md 的直链，如 https://raw.githubusercontent.com/owner/repo/main/skills/pdf/SKILL.md"
          allow-clear
          @press-enter="doInstall"
        />
        <a-button type="primary" :loading="installing" @click="doInstall">安装</a-button>
      </div>
      <p class="zc-tip">技能遵循 Anthropic Agent Skills 标准；安装后归属当前账户，工作区不会出现技能文件。</p>
    </a-card>

    <a-table
      class="zc-card"
      :data-source="skills"
      :loading="loading"
      :columns="columns"
      row-key="id"
      :pagination="false"
      size="small"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'enabled'">
          <a-tag :color="record.enabled ? 'green' : 'default'">
            {{ record.enabled ? '已启用' : '已停用' }}
          </a-tag>
        </template>
      </template>
      <template #emptyText>
        <a-empty description="还没有技能" />
      </template>
    </a-table>
  </div>
</template>

<style scoped>
.zc-skills {
  max-width: 960px;
  margin: 0 auto;
  padding: 16px 24px 40px;
}
.zc-page-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}
.zc-back {
  color: var(--zc-text-sub);
}
.zc-page-title {
  margin: 0;
  font-size: 18px;
}
.zc-card {
  margin-bottom: 16px;
}
.zc-install-row {
  display: flex;
  gap: 8px;
}
.zc-tip {
  margin: 8px 0 0;
  color: var(--zc-text-sub);
  font-size: 12px;
}
</style>
