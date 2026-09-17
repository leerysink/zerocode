<script setup>
// 设置页（Task 14 充实）：分组表单——模型接入（base_url / model / api_key(password，placeholder sk-***) + 测试连接）、
// 执行参数（write_confirm 开关 / ask_timeout_seconds(min 0) / max_tool_rounds / history_window）、环境状态（PG 版本）。
// 字段映射与保存走 config store（loadConfig / save），api_key 掩码哨兵 sk-*** 由后端 PUT 过滤、不会覆盖真实密钥。
import { onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import { pgStatus, testLLM } from '../api'
import { useConfigStore } from '../stores/config'

const store = useConfigStore()
const pg = ref(null) // {ok, version|error}
const testing = ref(false)

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
    <h2 class="zc-page-title">设置</h2>

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
        <a-form-item label="最大工具轮数">
          <a-input-number v-model:value="store.form.maxToolRounds" :min="0" style="width: 180px" />
        </a-form-item>
        <a-form-item label="历史窗口（条数）">
          <a-input-number v-model:value="store.form.historyWindow" :min="0" style="width: 180px" />
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
.zc-page-title {
  font-size: 18px;
  font-weight: 600;
  color: var(--zc-text);
  margin: 0 0 16px;
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
</style>
