<template>
  <view class="page">
    <text class="title">注册</text>
    <input v-model="form.username" class="input" placeholder="用户名（登录用）" />
    <input v-model="form.nickname" class="input" placeholder="昵称" />
    <input v-model="form.password" class="input" password placeholder="密码" />
    <button class="btn" :loading="busy" @click="submit">注册</button>
    <text class="link" @click="go('/pages/login/index')">已有账号？去登录</text>
  </view>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { request } from '@/utils/request'

const form = reactive({ username: '', nickname: '', password: '' })
const busy = ref(false)

function go(url) {
  uni.redirectTo({ url })
}

async function submit() {
  if (!form.username || !form.password) {
    uni.showToast({ title: '用户名和密码都要填', icon: 'none' })
    return
  }
  busy.value = true
  try {
    // 注册出来的账号只挂「移动端用户」角色，没有任何后台菜单（规格 US-12）
    await request('/auth/register', { method: 'POST', data: { ...form } })
    uni.showToast({ title: '注册成功，请登录', icon: 'success' })
    setTimeout(() => uni.redirectTo({ url: '/pages/login/index' }), 700)
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
