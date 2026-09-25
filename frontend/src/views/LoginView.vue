<template>
  <div class="login-page">
    <a-card class="login-card" :bordered="false">
      <div class="login-brand">zerocode</div>
      <div class="login-subtitle">{{ mode === 'login' ? '登录你的工作台' : '创建一个账号，开始构建你的应用' }}</div>
      <a-form layout="vertical" @finish="submit">
        <a-form-item name="nickname" label="昵称">
          <a-input v-model:value="form.nickname" placeholder="怎么称呼你" size="large" autocomplete="username" />
        </a-form-item>
        <a-form-item name="password" label="口令">
          <a-input-password v-model:value="form.password" placeholder="登录口令" size="large"
                            :autocomplete="mode === 'login' ? 'current-password' : 'new-password'" />
        </a-form-item>
        <a-alert v-if="error" type="error" show-icon :message="error" class="login-error" />
        <a-button type="primary" html-type="submit" block size="large" :loading="loading">
          {{ mode === 'login' ? '登录' : '注册并进入' }}
        </a-button>
      </a-form>
      <a class="login-switch" @click="toggle">
        {{ mode === 'login' ? '还没有账号？创建一个' : '已有账号？去登录' }}
      </a>
    </a-card>
  </div>
</template>

<script setup>
// 登录/注册页（Ticket 002 / spec 故事 1/2/4/14）：只有昵称+口令两个字段，零经验友好。
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()
const mode = ref('login')
const loading = ref(false)
const error = ref('')
const form = reactive({ nickname: '', password: '' })

function toggle() {
  mode.value = mode.value === 'login' ? 'register' : 'login'
  error.value = ''
}

async function submit() {
  loading.value = true
  error.value = ''
  try {
    if (mode.value === 'login') {
      await auth.login({ nickname: form.nickname, password: form.password })
    } else {
      await auth.register({ nickname: form.nickname, password: form.password })
    }
    router.replace(typeof route.query.next === 'string' ? route.query.next : '/')
  } catch (e) {
    error.value = e.response?.data?.detail || '操作失败，请稍后再试'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page {
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--zc-bg, #f5f6f8);
}
.login-card {
  width: 360px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.08);
  border-radius: 12px;
}
.login-brand {
  font-size: 22px;
  font-weight: 700;
  text-align: center;
}
.login-subtitle {
  margin: 4px 0 20px;
  text-align: center;
  color: rgba(0, 0, 0, 0.45);
  font-size: 13px;
}
.login-error {
  margin-bottom: 12px;
}
.login-switch {
  display: block;
  margin-top: 14px;
  text-align: center;
  font-size: 13px;
}
</style>
