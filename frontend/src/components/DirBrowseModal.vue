<script setup>
// 目录浏览弹窗：空 path 列盘符 → 逐级进入 → 「选择当前目录」返回 path；也可手输路径跳转。
// 数据源 POST /api/fs/browse（api.browseDir）：{ path, parent, entries: [{name, path, is_dir}] }，
// 后端只返回子目录（is_dir 恒 true），400「目录不存在」时提示并保留原位置。
import { ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { FolderOutlined } from '@ant-design/icons-vue'
import { browseDir } from '../api'

const props = defineProps({ open: Boolean })
const emit = defineEmits(['update:open', 'select'])

const path = ref('') // 当前所在目录（'' = 此电脑/盘符层）
const parent = ref(null) // 上级目录，盘符层为 null
const entries = ref([])
const loading = ref(false)
const manual = ref('') // 手输路径输入框

async function load(p) {
  loading.value = true
  try {
    const { data } = await browseDir(p || '')
    path.value = data.path
    parent.value = data.parent
    entries.value = data.entries
    manual.value = data.path // 同步回输入框，可直接微调后再次跳转
  } catch (e) {
    message.error(e?.response?.data?.detail || '目录不存在')
  } finally {
    loading.value = false
  }
}

// 每次打开都从盘符层开始浏览
watch(
  () => props.open,
  (v) => {
    if (v) load('')
  }
)

function close() {
  emit('update:open', false)
}

// 选中当前目录回传（盘符层 path 为空串，不可选）
function choose() {
  if (!path.value) return
  emit('select', path.value)
  close()
}
</script>

<template>
  <a-modal
    :open="open"
    title="浏览目录"
    width="50%"
    ok-text="选择当前目录"
    :ok-button-props="{ disabled: !path }"
    @ok="choose"
    @cancel="close"
  >
    <div class="zc-browse-bar">
      <a-input
        v-model:value="manual"
        placeholder="输入路径后回车跳转，如 D:\work"
        allow-clear
        @press-enter="load(manual)"
      />
      <a-button @click="load(manual)">跳转</a-button>
      <a-button :disabled="!parent" @click="load(parent)">上级</a-button>
    </div>
    <div class="zc-browse-current">当前：{{ path || '此电脑（盘符）' }}</div>
    <a-spin :spinning="loading">
      <div class="zc-browse-body">
        <div v-for="it in entries" :key="it.path" class="zc-browse-item" @click="load(it.path)">
          <folder-outlined class="zc-browse-icon" />
          <span>{{ it.name }}</span>
        </div>
        <a-empty v-if="!loading && !entries.length" description="空目录" />
      </div>
    </a-spin>
  </a-modal>
</template>

<style scoped>
.zc-browse-bar {
  display: flex;
  gap: 8px;
}
.zc-browse-current {
  margin: 8px 0;
  color: var(--zc-text-sub);
  font-size: 12px;
  word-break: break-all;
}
.zc-browse-body {
  max-height: 45vh;
  overflow-y: auto;
  border: 1px solid var(--zc-border);
  border-radius: 6px;
}
.zc-browse-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  cursor: pointer;
  color: var(--zc-text);
  font-size: 13px;
}
.zc-browse-item + .zc-browse-item {
  border-top: 1px solid var(--zc-border);
}
.zc-browse-item:hover {
  background: var(--zc-bg);
}
.zc-browse-icon {
  color: var(--zc-primary);
  flex-shrink: 0;
}
</style>
