<template>
  <view class="page">
    <text class="title">登录</text>
    <input v-model="form.username" class="input" placeholder="用户名" />
    <input v-model="form.password" class="input" password placeholder="密码" />
    <button class="btn" :loading="busy" @click="submit">登录</button>
    <text class="link" @click="go('/pages/register/index')">还没有账号？去注册</text>
  </view>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { request } from '@/utils/request'

const form = reactive({ username: '', password: '' })
const busy = ref(false)

function go(url) {
  uni.navigateTo({ url })
}

async function submit() {
  if (!form.username || !form.password) {
    uni.showToast({ title: '用户名和密码都要填', icon: 'none' })
    return
  }
  busy.value = true
  try {
    // 与后台管理系统共用一个 /auth/login：同一个 JwtAuthenticationFilter、同一份 Redis 登录态（规格 US-14）。
    // 不带验证码：/auth/captcha 是恒 enabled:false 的占位，移动端不必先取图。
    const data = await request('/auth/login', { method: 'POST', data: { ...form } })
    uni.setStorageSync('token', data.token)
    uni.showToast({ title: '登录成功', icon: 'success' })
    setTimeout(() => uni.reLaunch({ url: '/pages/index/index' }), 600)
  } catch (e) {
    uni.showToast({ title: e.message, icon: 'none' })
  } finally {
    busy.value = false
  }
}
</script>

<style>
.page { padding: 60rpx 40rpx; }
.title { display: block; font-size: 40rpx; font-weight: 600; margin-bottom: 40rpx; }
.input { border-bottom: 1rpx solid #e5e7eb; padding: 20rpx 0; font-size: 30rpx; margin-bottom: 24rpx; }
.btn { margin-top: 40rpx; background: #2f6fed; color: #fff; }
.link { display: block; margin-top: 32rpx; font-size: 26rpx; color: #2f6fed; }
</style>
