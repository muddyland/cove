import { api } from './client'
import type { IntegrationScope, IntegrationToken, IntegrationTokenCreated } from '@/types'

const base = '/admin/integration-tokens'

export const integrationsApi = {
  list: () => api.get<IntegrationToken[]>(base),
  create: (name: string, scope: IntegrationScope) => api.post<IntegrationTokenCreated>(base, { name, scope }),
  revoke: (id: number) => api.delete(`${base}/${id}`),
  // The Home Assistant custom component, as custom_components/cove/… in a zip.
  homeAssistantZip: () => api.getBlob(`${base}/home-assistant.zip`),
}
