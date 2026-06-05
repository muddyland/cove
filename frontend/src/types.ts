export interface User {
  id: number
  username: string
  is_admin: boolean
  auth_provider: string
  created_at: string
  last_login_at: string | null
}

export interface AuthConfig {
  oidc_enabled: boolean
  oidc_provider_name: string
  needs_setup: boolean
}

export type ImageType = 'desktop' | 'browser' | 'link'

export interface WorkspaceImage {
  id: number
  name: string
  docker_image: string
  image_type: ImageType
  description: string | null
  internal_port: number
  url_env: string | null
  enabled: boolean
  created_at: string
}

export type WorkspaceStatus = 'creating' | 'running' | 'stopping' | 'stopped' | 'error'

export interface Workspace {
  id: number
  public_id: string
  user_id: number
  name: string
  status: WorkspaceStatus
  workspace_type: ImageType
  container_id: string | null
  container_name: string | null
  image_id: number
  image_name: string
  target_url: string | null
  stream_url: string | null
  created_at: string
  started_at: string | null
  stopped_at: string | null
  error_message: string | null
}

export interface AuditEntry {
  id: number
  ts: string
  user_id: number | null
  username: string | null
  action: string
  detail: string | null
  ip: string | null
}
