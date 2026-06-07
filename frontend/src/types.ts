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
  oidc_only: boolean
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
  logo_url: string | null
  created_at: string
}

export type WorkspaceStatus = 'creating' | 'running' | 'stopping' | 'stopped' | 'error'

export interface WorkspaceStats {
  cpu_pct: number
  mem_used: number
  mem_limit: number
  mem_pct: number
  tailscale_ip?: string | null
}

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
  image_logo: string | null
  target_url: string | null
  kiosk: boolean
  kiosk_dark: boolean
  kiosk_menu: boolean
  stream_url: string | null
  created_at: string
  started_at: string | null
  stopped_at: string | null
  error_message: string | null
  use_tailscale: boolean
  lan_access: boolean
  ts_exit_node: string | null
  ts_accept_routes: boolean
  ts_accept_dns: boolean
  custom_dns: boolean
  dns_servers: string | null
  install_packages: string | null
  proot_apps: string | null
  appimages: string | null
  allow_sudo: boolean
}

export interface LanPolicy {
  enabled: boolean
  subnets: string[]
}

export interface TailscaleConfig {
  enabled: boolean
  has_auth_key: boolean
  login_server: string | null
}

export interface FileEntry {
  name: string
  type: 'dir' | 'file'
  size: number
  modified: string
}

export interface FileListing {
  path: string
  entries: FileEntry[]
}

export interface AppSettings {
  tailscale_image: string
  workspace_lan_access: boolean
  workspace_lan_subnets: string
  workspace_no_new_privileges: boolean
  workspace_max_runtime_hours: number
  workspace_cpu_limit: number
  workspace_memory_limit_mb: number
}

export interface EnvEntry {
  name: string
  value: string
}

export interface EnvSummary {
  entries: EnvEntry[]
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
