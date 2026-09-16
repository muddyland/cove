import { api } from './client'
import type { AppImageApp, ProotAppMeta, ProotApps, ProotTask, ProotTaskOp, WorkspaceProotTasks } from '@/types'

export const prootApi = {
  // Available LinuxServer proot-app names (for autocomplete in the launcher).
  list: () => api.get<{ apps: string[]; meta?: Record<string, ProotAppMeta> }>('/proot-apps'),
  // Apps installed in a running workspace; `check` compares them with the registry.
  installed: (wsId: number, check = true) =>
    api.get<ProotApps>(`/workspaces/${wsId}/proot-apps?check=${check}`),
  startTask: (wsId: number, op: ProotTaskOp, apps: string[]) =>
    api.post<ProotTask>(`/workspaces/${wsId}/proot-apps/tasks`, { op, apps }),
  // AppImages installed in a workspace, and the tasks that manage them. Cove
  // never fetches these URLs itself — the workspace downloads them.
  appImages: (wsId: number) => api.get<{ apps: AppImageApp[] }>(`/workspaces/${wsId}/appimages`),
  installAppImages: (wsId: number, urls: string[]) =>
    api.post<ProotTask>(`/workspaces/${wsId}/appimages/tasks`, { op: 'install', urls }),
  updateAppImage: (wsId: number, slug: string, url: string) =>
    api.post<ProotTask>(`/workspaces/${wsId}/appimages/tasks`, { op: 'update', slug, url }),
  removeAppImages: (wsId: number, slugs: string[]) =>
    api.post<ProotTask>(`/workspaces/${wsId}/appimages/tasks`, { op: 'remove', slugs }),
  tasks: (wsId: number) => api.get<ProotTask[]>(`/workspaces/${wsId}/proot-apps/tasks`),
  taskLog: (wsId: number, taskId: string) =>
    api.get<{ output: string }>(`/workspaces/${wsId}/proot-apps/tasks/${encodeURIComponent(taskId)}/log`),
  clearTasks: (wsId: number) => api.post<void>(`/workspaces/${wsId}/proot-apps/tasks/clear`),
  // Background tasks across the current user's live desktop workspaces.
  allTasks: () => api.get<WorkspaceProotTasks[]>('/proot-tasks'),
}
