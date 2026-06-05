import { api } from './client'
import type { AuthConfig, User } from '@/types'

export const authApi = {
  config: () => api.get<AuthConfig>('/auth/config'),
  setup: (username: string, password: string) =>
    api.post<{ access_token: string }>('/auth/setup', { username, password }),
  login: (username: string, password: string) =>
    api.post<{ access_token: string }>('/auth/login', { username, password }),
  refresh: () => api.post<{ access_token: string }>('/auth/refresh'),
  me: () => api.get<User>('/auth/me'),
  logout: () => api.post('/auth/logout'),
  changePassword: (current_password: string, new_password: string) =>
    api.post('/auth/change-password', { current_password, new_password }),
}
