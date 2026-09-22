<template>
  <div class="profile-page page-scroll">
    <!-- 顶部信息卡 -->
    <a-card class="profile-hero" :bordered="false">
      <div class="hero-bg"></div>
      <div class="hero-content">
        <a-avatar class="hero-avatar" :size="96">{{ userInitial }}</a-avatar>
        <div class="hero-info">
          <h2>{{ userStore.userInfo?.nickname || userStore.userInfo?.username }}</h2>
          <p class="hero-username">@{{ userStore.userInfo?.username }}</p>
          <div class="hero-roles">
            <a-tag v-for="r in userStore.userInfo?.roles" :key="r" color="blue" style="margin-top: 8px">
              <SafetyCertificateOutlined style="margin-right: 4px" /> {{ r }}
            </a-tag>
          </div>
        </div>
      </div>
    </a-card>

    <!-- 内容卡片 -->
    <a-card :bordered="false" class="content-card">
      <a-tabs v-model:active-key="activeTab" class="profile-tabs">
        <!-- 基本信息 -->
        <a-tab-pane key="profile" tab="基本信息">
          <a-form :model="profileForm" layout="vertical" class="profile-form">
            <a-row :gutter="24">
              <a-col :span="12">
                <a-form-item label="用户ID">
                  <a-input :value="userStore.userInfo?.userId?.toString()" disabled size="large">
                    <template #prefix><IdcardOutlined /></template>
                  </a-input>
                </a-form-item>
              </a-col>
              <a-col :span="12">
                <a-form-item label="用户名">
                  <a-input :value="userStore.userInfo?.username" disabled size="large">
                    <template #prefix><UserOutlined /></template>
                  </a-input>
                </a-form-item>
              </a-col>
              <a-col :span="12">
                <a-form-item label="昵称">
                  <a-input v-model:value="profileForm.nickname" placeholder="请输入昵称" size="large" allow-clear />
                </a-form-item>
              </a-col>
              <a-col :span="12">
                <a-form-item label="手机号">
                  <a-input v-model:value="profileForm.phone" placeholder="请输入手机号" size="large" allow-clear>
                    <template #prefix><PhoneOutlined /></template>
                  </a-input>
                </a-form-item>
              </a-col>
              <a-col :span="24">
                <a-form-item label="邮箱">
                  <a-input v-model:value="profileForm.email" placeholder="请输入邮箱" size="large" allow-clear>
                    <template #prefix><MailOutlined /></template>
                  </a-input>
                </a-form-item>
              </a-col>
            </a-row>
            <div class="form-footer">
              <a-button @click="loadProfile">重置</a-button>
              <a-button type="primary" :loading="savingProfile" @click="saveProfile" size="large">
                <SaveOutlined /> 保存修改
              </a-button>
            </div>
          </a-form>
        </a-tab-pane>

        <!-- 修改密码 -->
        <a-tab-pane key="password" tab="修改密码">
          <div class="password-tip">
            <SafetyCertificateOutlined />
            <div>
              <div class="tip-title">安全提醒</div>
              <div class="tip-desc">修改密码后需要重新登录，密码长度 6-32 位，建议使用字母+数字组合</div>
            </div>
          </div>
          <a-form :model="pwdForm" layout="vertical" :rules="pwdRules" class="profile-form">
            <a-row :gutter="24">
              <a-col :span="12">
                <a-form-item label="旧密码" name="oldPassword">
                  <a-input-password v-model:value="pwdForm.oldPassword" placeholder="请输入旧密码" size="large">
                    <template #prefix><LockOutlined /></template>
                  </a-input-password>
                </a-form-item>
              </a-col>
              <a-col :span="12" />
              <a-col :span="12">
                <a-form-item label="新密码" name="newPassword">
                  <a-input-password v-model:value="pwdForm.newPassword" placeholder="6-32 位" size="large">
                    <template #prefix><KeyOutlined /></template>
                  </a-input-password>
                </a-form-item>
              </a-col>
              <a-col :span="12">
                <a-form-item label="确认新密码" name="confirmPassword">
                  <a-input-password v-model:value="pwdForm.confirmPassword" placeholder="再次输入" size="large">
                    <template #prefix><CheckOutlined /></template>
                  </a-input-password>
                </a-form-item>
              </a-col>
            </a-row>
            <div class="form-footer">
              <a-button @click="Object.assign(pwdForm, { oldPassword: '', newPassword: '', confirmPassword: '' })">重置</a-button>
              <a-button type="primary" :loading="savingPwd" @click="savePassword" size="large">
                <LockOutlined /> 修改密码
              </a-button>
            </div>
          </a-form>
        </a-tab-pane>
      </a-tabs>
    </a-card>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { useRouter } from 'vue-router'
import { useUserStore } from '@/store/user'
import { updatePassword, updateProfile } from '@/api/profile'

const router = useRouter()
const userStore = useUserStore()
const activeTab = ref('profile')

const savingProfile = ref(false)
const savingPwd = ref(false)

const profileForm = reactive({ nickname: '', phone: '', email: '' })
const pwdForm = reactive({ oldPassword: '', newPassword: '', confirmPassword: '' })

const pwdRules = {
  oldPassword: [{ required: true, message: '请输入旧密码' }],
  newPassword: [
    { required: true, message: '请输入新密码' },
    { min: 6, max: 32, message: '密码长度应在 6-32 位之间' },
  ],
  confirmPassword: [{ required: true, message: '请再次输入新密码' }],
}

const userInitial = computed(() => {
  const n = userStore.userInfo?.nickname || userStore.userInfo?.username || 'U'
  return n.charAt(0).toUpperCase()
})

async function loadProfile() {
  profileForm.nickname = (userStore.userInfo as any)?.nickname || ''
  try {
    const u = (await userStore.fetchUserInfo()) as any
    profileForm.nickname = u?.nickname || ''
  } catch { /* ignore */ }
}

async function saveProfile() {
  savingProfile.value = true
  try {
    await updateProfile({ nickname: profileForm.nickname, phone: profileForm.phone, email: profileForm.email })
    message.success('个人信息已保存')
    await userStore.fetchUserInfo()
  } catch {
    // 失败提示交给 request.ts 拦截器统一弹出（它 reject 的是 new Error(res.msg)，
    // 这里读 e.msg 永远是 undefined，只会再叠一条语义不对的兜底文案）
  } finally { savingProfile.value = false }
}

async function savePassword() {
  if (pwdForm.newPassword !== pwdForm.confirmPassword) {
    message.error('两次密码输入不一致')
    return
  }
  savingPwd.value = true
  try {
    await updatePassword({ oldPassword: pwdForm.oldPassword, newPassword: pwdForm.newPassword })
    Modal.success({
      title: '密码修改成功',
      content: '下次访问请使用新密码重新登录',
      onOk: async () => {
        await userStore.logout()
        router.push('/login')
      },
    })
    pwdForm.oldPassword = ''
    pwdForm.newPassword = ''
    pwdForm.confirmPassword = ''
  } catch {
    // 同上：服务端的具体原因（如「旧密码错误」）已由拦截器提示过
  } finally { savingPwd.value = false }
}

onMounted(loadProfile)
</script>

<style scoped>
.profile-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* 顶部信息卡 */
.profile-hero {
  position: relative;
  overflow: hidden;
  background: #fff !important;
  border-radius: var(--radius-md) !important;
  padding: 0;
}
.hero-bg {
  height: 100px;
  background: linear-gradient(135deg, #1d4eff 0%, #4caefe 50%, #7dd3fc 100%);
  position: relative;
}
.hero-bg::after {
  content: '';
  position: absolute;
  inset: 0;
  background: radial-gradient(circle at top right, rgba(255,255,255,0.18), transparent 60%);
}
.hero-content {
  display: flex;
  align-items: flex-end;
  padding: 0 32px 24px;
  margin-top: -48px;
  position: relative;
  z-index: 1;
}
.hero-avatar {
  background: var(--primary-gradient) !important;
  color: #fff !important;
  font-weight: 700;
  font-size: 36px;
  border: 4px solid #fff;
  box-shadow: 0 8px 24px rgba(29, 78, 255, 0.3);
}
.hero-info {
  margin-left: 20px;
  flex: 1;
}
.hero-info h2 {
  margin: 0 0 4px;
  font-size: 22px;
  font-weight: 600;
  color: var(--text-primary);
}
.hero-username {
  margin: 0;
  color: var(--text-muted);
  font-size: 13px;
}
.hero-roles :deep(.ant-tag) {
  border-radius: 6px;
  padding: 2px 10px;
  background: var(--primary-gradient-soft);
  border: 1px solid rgba(29, 78, 255, 0.2);
  color: var(--primary-color);
  font-weight: 500;
}

/* 内容卡 */
.content-card {
  background: #fff !important;
  border-radius: var(--radius-md) !important;
}
.profile-tabs :deep(.ant-tabs-nav) {
  margin-bottom: 24px;
}
.profile-tabs :deep(.ant-tabs-tab) {
  padding: 8px 16px;
  font-weight: 500;
}
.profile-tabs :deep(.ant-tabs-tab-active) {
  color: var(--primary-color) !important;
}
.profile-tabs :deep(.ant-tabs-ink-bar) {
  background: var(--primary-gradient);
}

.profile-form {
  max-width: 720px;
}
.form-footer {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid var(--border-color);
}
.form-footer .ant-btn-primary {
  background: var(--primary-gradient) !important;
  border-color: transparent !important;
  box-shadow: 0 4px 12px rgba(29, 78, 255, 0.25);
  min-width: 140px;
}

.password-tip {
  background: linear-gradient(135deg, #fff7ed 0%, #fef3c7 100%);
  border-left: 4px solid #f59e0b;
  border-radius: var(--radius-md);
  padding: 16px 20px;
  display: flex;
  gap: 12px;
  margin-bottom: 24px;
}
.password-tip :deep(.anticon) {
  font-size: 22px;
  color: #f59e0b;
}
.tip-title {
  font-weight: 600;
  color: #92400e;
  font-size: 14px;
}
.tip-desc {
  color: #a16207;
  font-size: 13px;
  margin-top: 2px;
}
</style>
