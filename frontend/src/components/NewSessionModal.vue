<script setup>
// 新会话弹窗（2026-09-20 改版）：以后只有应用——手动挂目录建工作区的入口整体下线，
// 只能从已有应用里选一个来开会话。应用列表由父级 SideBar 传入（同一次 /api/apps 的结果，
// 不再自己发第二个请求），选项值就是应用关联的 workspace_id：会话表的外键仍是 workspace，
// 「应用 = 一个带工程目录的工作区」这层关系由 app_service 建应用时一并插入。
// 2026-09-21 自动命名：会话名称输入框下线（跟 DSH 一样无须自己起名），
// 标题由后端在首条用户消息落库时按内容派生（session_title_service）。
import { computed, reactive, watch } from 'vue'
import { message } from 'ant-design-vue'
import { createSession } from '../api'
import { useChatStore } from '../stores/chat'

const props = defineProps({ open: Boolean, apps: { type: Array, default: () => [] } })
const emit = defineEmits(['update:open', 'created'])

const store = useChatStore()
const form = reactive({ wsId: null })

const options = computed(() => props.apps.map((a) => ({ label: a.name, value: a.workspace_id })))
const canSubmit = computed(() => !!form.wsId)

// 每次打开重置表单；只有一个应用时直接选中，省掉一次点击
watch(
  () => props.open,
  (v) => {
    if (!v) return
    form.wsId = props.apps.length === 1 ? props.apps[0].workspace_id : null
  },
  { immediate: true }
)

function close() {
  emit('update:open', false)
}

async function submit() {
  if (!canSubmit.value) {
    message.warning('请先选择一个应用')
    return
  }
  try {
    const { data: session } = await createSession({ workspace_id: form.wsId })
    await store.openSession(session.id, form.wsId) // 同步记录 currentWorkspaceId（渲染门，Task 13/14）
    message.success('会话已创建')
    emit('created', session)
    close()
  } catch (e) {
    message.error(e?.response?.data?.detail || '创建失败')
  }
}
</script>

<template>
  <a-modal
    :open="open"
    title="新会话"
    width="40%"
    ok-text="创建"
    cancel-text="取消"
    :ok-button-props="{ disabled: !canSubmit }"
    @ok="submit"
    @cancel="close"
  >
    <div v-if="!apps.length" class="zc-ns-empty">
      还没有应用，请先去 <router-link to="/apps">「应用」页</router-link> 新建一个应用，再回来开会话。
    </div>
    <template v-else>
      <div class="zc-ns-field">
        <div class="zc-ns-label">选择应用</div>
        <a-select
          v-model:value="form.wsId"
          :options="options"
          placeholder="选择要在哪个应用里聊"
          show-search
          option-filter-prop="label"
          class="zc-ns-select"
        />
      </div>
      <p class="zc-ns-hint">会话将在你发出第一句话时按内容自动命名。</p>
    </template>
  </a-modal>
</template>

<style scoped>
.zc-ns-field {
  margin-bottom: 8px;
}
.zc-ns-label {
  margin-bottom: 4px;
  color: var(--zc-text-sub);
  font-size: 12px;
}
.zc-ns-select {
  width: 100%;
}
.zc-ns-hint {
  margin: 4px 0 0;
  color: var(--zc-text-sub);
  font-size: 12px;
}
.zc-ns-empty {
  color: var(--zc-text-sub);
  font-size: 13px;
  line-height: 1.7;
}
.zc-ns-empty a {
  color: var(--zc-primary);
}
</style>
