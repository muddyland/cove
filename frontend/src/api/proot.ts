import { api } from './client'

export const prootApi = {
  // Available LinuxServer proot-app names (for autocomplete in the launcher).
  list: () => api.get<{ apps: string[] }>('/proot-apps'),
}
