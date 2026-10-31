import axios from 'axios'

const LOCAL_API_URL = 'http://localhost:8000/api'

export function normalizeApiBaseUrl(value: string): string {
  const trimmed = (value ?? '').trim().replace(/\/+$/, '')
  if (!trimmed) {
    return ''
  }

  return trimmed.endsWith('/api') ? trimmed : `${trimmed}/api`
}

export function getApiBaseUrl(): string {
  const configured = normalizeApiBaseUrl(import.meta.env.VITE_API_URL ?? '')

  if (configured) {
    return configured
  }

  if (import.meta.env.PROD) {
    throw new Error('VITE_API_URL is required in production. Set it to the deployed backend URL.')
  }

  return LOCAL_API_URL
}

const API_URL = getApiBaseUrl()

export const api = axios.create({
  baseURL: API_URL,
  headers: {
    'Content-Type': 'application/json',
  },
})

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('gymai_token')
  if (token && config.headers) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error?.response?.status === 401) {
      localStorage.removeItem('gymai_token')
      localStorage.removeItem('gymai_user')
      // AuthContext owns session state and navigation; do not hard-reload here.
    }
    return Promise.reject(error)
  }
)

export default api
