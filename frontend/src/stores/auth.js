// 登录态 store（Ticket 002）：user 为 /api/auth/me 的投影，loaded 区分「查过了」与「没查过」
import { defineStore } from 'pinia'
import { authLogin, authLogout, authMe, authRegister } from '../api'

export const useAuthStore = defineStore('auth', {
  state: () => ({
    user: null,
    loaded: false,
  }),
  actions: {
    async fetchMe() {
      try {
        const { data } = await authMe()
        this.user = data
      } catch {
        this.user = null
      } finally {
        this.loaded = true
      }
      return this.user
    },
    async login(payload) {
      const { data } = await authLogin(payload)
      this.user = data
      this.loaded = true
    },
    async register(payload) {
      const { data } = await authRegister(payload)
      this.user = data
      this.loaded = true
    },
    async logout() {
      await authLogout()
      this.user = null
    },
  },
})
