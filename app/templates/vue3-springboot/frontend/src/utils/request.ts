import axios, { type AxiosInstance, type AxiosRequestConfig } from 'axios'
import { message } from 'ant-design-vue'

const request: AxiosInstance = axios.create({
  baseURL: '/api',
  timeout: 30000,
})

// 请求拦截器
request.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem('token')
    if (token) {
      config.headers['Authorization'] = 'Bearer ' + token
    }
    return config
  },
  (error) => Promise.reject(error)
)

// 响应拦截器
request.interceptors.response.use(
  (response) => {
    const res = response.data
    if (res.code !== 200) {
      message.error(res.msg || '请求失败')
      if (res.code === 401) {
        localStorage.removeItem('token')
        window.location.href = '/login'
      }
      return Promise.reject(new Error(res.msg || '请求失败'))
    }
    return res
  },
  (error) => {
    const status = error.response?.status
    if (status === 401) {
      message.error('登录已过期，请重新登录')
      localStorage.removeItem('token')
      window.location.href = '/login'
    } else if (status === 403) {
      message.error('权限不足')
    } else {
      message.error(error.response?.data?.msg || error.message || '网络异常')
    }
    return Promise.reject(error)
  }
)

export interface ApiResult<T = any> {
  code: number
  msg: string
  data: T
}

export interface PageResult<T = any> {
  records: T[]
  total: number
  current: number
  size: number
}

export function get<T = any>(url: string, params?: any, config?: AxiosRequestConfig): Promise<ApiResult<T>> {
  return request.get(url, { params, ...config })
}

export function post<T = any>(url: string, data?: any, config?: AxiosRequestConfig): Promise<ApiResult<T>> {
  return request.post(url, data, config)
}

export function put<T = any>(url: string, data?: any, config?: AxiosRequestConfig): Promise<ApiResult<T>> {
  return request.put(url, data, config)
}

export function del<T = any>(url: string, config?: AxiosRequestConfig): Promise<ApiResult<T>> {
  return request.delete(url, config)
}

export default request
