<template>
  <div class="login-container">
    <!-- 左侧品牌区 -->
    <div class="brand-panel">
      <div class="brand-bg">
        <div class="brand-blob blob-1"></div>
        <div class="brand-blob blob-2"></div>
        <div class="brand-blob blob-3"></div>
      </div>
      <div class="brand-content">
        <div class="brand-logo">
          <SettingOutlined />
        </div>
        <h1 class="brand-title">{{ appTitle }}</h1>
        <p class="brand-subtitle">{{ appSubtitle }}</p>
        <ul class="brand-features">
          <li><CheckCircleOutlined /> 部门级数据权限</li>
          <li><CheckCircleOutlined /> 用户 / 角色 / 菜单 / 字典</li>
          <li><CheckCircleOutlined /> 操作与登录日志</li>
          <li><CheckCircleOutlined /> 角色精细化配置</li>
        </ul>
      </div>
    </div>

    <!-- 右侧登录区 -->
    <div class="form-panel">
      <div class="form-wrapper">
        <h2 class="form-title">欢迎登录</h2>
        <p class="form-tip">输入您的账号信息开始使用</p>

        <a-form :model="form" layout="vertical" @finish="handleLogin" autocomplete="off">
          <a-form-item label="用户名" name="username" :rules="[{ required: true, message: '请输入用户名' }]">
            <a-input
              v-model:value="form.username"
              placeholder="请输入用户名"
              size="large"
              autocomplete="username"
            >
              <template #prefix><UserOutlined class="input-icon" /></template>
            </a-input>
          </a-form-item>

          <a-form-item label="密码" name="password" :rules="[{ required: true, message: '请输入密码' }]">
            <a-input-password
              v-model:value="form.password"
              placeholder="请输入密码"
              size="large"
              autocomplete="current-password"
              @pressEnter="handleLogin"
            >
              <template #prefix><LockOutlined class="input-icon" /></template>
            </a-input-password>
          </a-form-item>

          <div class="form-options">
            <a-checkbox v-model:checked="rememberMe">记住账号</a-checkbox>
            <a class="form-link">忘记密码?</a>
          </div>

          <a-button
            type="primary"
            html-type="submit"
            size="large"
            :loading="loading"
            block
            class="login-btn"
          >
            <span v-if="!loading">登 录</span>
            <span v-else>登录中...</span>
          </a-button>
        </a-form>

        <!-- 基座种子的初始账号：不提示则首次预览不知道能进（登录后即可改，见「个人中心」） -->
        <p class="login-tip">初始账号 admin / 初始密码 admin123，登录后请到「个人中心」修改</p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { useUserStore } from '@/store/user'
import { appTitle, appSubtitle } from '@/config'

const router = useRouter()
const userStore = useUserStore()
const loading = ref(false)

const form = reactive({
  username: '',
  password: '',
})
const rememberMe = ref(true)

async function handleLogin() {
  loading.value = true
  try {
    await userStore.login(form)
    message.success('登录成功')
    router.push('/')
  } catch {
    // ignore
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-container {
  display: flex;
  height: 100vh;
  overflow: hidden;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', sans-serif;
}

/* ============ 左侧品牌区 ============ */
.brand-panel {
  flex: 1.1;
  position: relative;
  background: linear-gradient(135deg, #1e3cff 0%, #4287ff 50%, #67c8ff 100%);
  overflow: hidden;
  color: #fff;
}
.brand-bg {
  position: absolute;
  inset: 0;
  overflow: hidden;
}
.brand-blob {
  position: absolute;
  border-radius: 50%;
  filter: blur(80px);
  opacity: 0.4;
  animation: float 15s ease-in-out infinite;
}
.blob-1 {
  width: 460px;
  height: 460px;
  background: #06b6d4;
  top: -100px;
  left: -120px;
}
.blob-2 {
  width: 380px;
  height: 380px;
  background: #8b5cf6;
  bottom: -80px;
  right: -100px;
  animation-delay: -5s;
}
.blob-3 {
  width: 320px;
  height: 320px;
  background: #06b6d4;
  top: 50%;
  left: 40%;
  animation-delay: -10s;
}
@keyframes float {
  0%, 100% { transform: translate(0, 0) scale(1); }
  33% { transform: translate(30px, -30px) scale(1.05); }
  66% { transform: translate(-20px, 20px) scale(0.95); }
}
.brand-content {
  position: relative;
  height: 100%;
  display: flex;
  flex-direction: column;
  justify-content: center;
  padding: 64px;
  z-index: 1;
}
.brand-logo {
  width: 72px;
  height: 72px;
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.2);
  backdrop-filter: blur(20px);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 36px;
  margin-bottom: 28px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.18);
}
.brand-title {
  font-size: 36px;
  margin: 0 0 14px;
  font-weight: 700;
  letter-spacing: -0.5px;
  line-height: 1.2;
}
.brand-subtitle {
  font-size: 16px;
  opacity: 0.85;
  margin: 0 0 36px;
  line-height: 1.6;
}
.brand-features {
  list-style: none;
  padding: 0;
  margin: 0;
}
.brand-features li {
  padding: 12px 0;
  font-size: 15px;
  display: flex;
  align-items: center;
  gap: 10px;
  opacity: 0.92;
}
.brand-features :deep(.anticon) {
  font-size: 18px;
  color: #5eead4;
}

/* ============ 右侧表单区 ============ */
.form-panel {
  flex: 0.9;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #fafbfc;
  padding: 32px;
}
.form-wrapper {
  width: 100%;
  max-width: 380px;
}
.form-title {
  font-size: 28px;
  font-weight: 700;
  color: var(--text-primary);
  margin: 0 0 8px;
  letter-spacing: -0.5px;
}
.form-tip {
  color: var(--text-muted);
  margin: 0 0 32px;
  font-size: 14px;
}

:deep(.ant-input-affix-wrapper) {
  border-radius: var(--radius-sm) !important;
  padding: 8px 12px !important;
}
:deep(.ant-input-affix-wrapper-lg) {
  padding: 10px 14px !important;
  font-size: 14px;
}
.input-icon {
  color: var(--text-muted);
}

.form-options {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin: -8px 0 20px;
}
.form-link {
  color: var(--primary-color);
  font-size: 13px;
  cursor: pointer;
}

.login-btn {
  height: 44px;
  font-size: 15px;
  font-weight: 500;
  background: var(--primary-gradient) !important;
  border-color: transparent !important;
  box-shadow: 0 6px 18px rgba(29, 78, 255, 0.35);
}
.login-btn:hover {
  filter: brightness(1.05);
  transform: translateY(-1px);
}

.login-tip {
  color: var(--text-muted);
  font-size: 12px;
  margin-top: 8px;
  text-align: center;
}

/* 响应式 */
@media (max-width: 900px) {
  .brand-panel { display: none; }
  .form-panel { flex: 1; }
}
</style>
