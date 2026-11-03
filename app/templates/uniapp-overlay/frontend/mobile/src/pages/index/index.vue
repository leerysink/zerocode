<template>
  <view class="page">
    <text class="title">{{ appName }}</text>
    <text class="desc">移动端模板页：接口一律写相对 /api，与管理端同源同端口。</text>

    <button class="btn" @click="checkHealth">探测后端健康</button>
    <text class="result">{{ health }}</text>

    <view class="row">
      <text class="who">{{ who }}</text>
    </view>
    <view class="row">
      <button class="btn ghost" @click="go('/pages/login/index')">登录</button>
      <button class="btn ghost" @click="go('/pages/register/index')">注册</button>
      <button v-if="token" class="btn ghost" @click="logout">退出</button>
    </view>
  </view>
</template>

<script setup>
import { ref } from 'vue'
import { request } from '@/utils/request'

const appName = ref("{{APP_NAME}}")
const health = ref('尚未探测')
const token = ref(uni.getStorageSync('token') || '')
const who = ref(token.value ? `已登录（token 前 8 位 ${token.value.slice(0, 8)}…）` : '未登录')

function go(url) {
  uni.navigateTo({ url })
}

async function checkHealth() {
  health.value = '探测中…'
  try {
    // /api/health 是 actuator 探针，permitAll：不带 token 也应当 200，是「同源链路通了」的最小证据
    const data = await request('/health')
    health.value = `通：${JSON.stringify(data)}`
  } catch (e) {
    health.value = `不通：${e.message}`
  }
}

function logout() {
  token.value = ''
  who.value = '未登录'
  uni.removeStorageSync('token')
}
</script>

<style>
.page { padding: 40rpx 32rpx; }
.title { display: block; font-size: 44rpx; font-weight: 600; color: #1f2937; }
.desc { display: block; margin-top: 12rpx; font-size: 26rpx; color: #6b7280; line-height: 1.6; }
.btn { margin-top: 32rpx; background: #2f6fed; color: #fff; font-size: 30rpx; }
.btn.ghost { margin-top: 0; margin-right: 16rpx; background: #fff; color: #2f6fed; border: 1rpx solid #2f6fed; }
.row { display: flex; flex-direction: row; align-items: center; margin-top: 28rpx; }
.who { font-size: 26rpx; color: #374151; }
.result { display: block; margin-top: 16rpx; font-size: 26rpx; color: #6b7280; word-break: break-all; }
</style>
