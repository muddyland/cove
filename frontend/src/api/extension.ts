import { api } from './client'
import type { ExtensionInfo } from '@/types'

export const extensionApi = {
  /** What (if anything) this deployment ships. */
  info: () => api.get<ExtensionInfo>('/extension'),

  /**
   * Save the extension zip.
   *
   * Fetched through the API client rather than pointed at with a plain link: the
   * download needs the bearer token (and its refresh-retry), which a bare
   * <a href> can't carry.
   */
  async download(filename: string) {
    const blob = await api.getBlob('/extension/download')
    if (!blob) return
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename || 'open-in-cove.zip'
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
  },
}
