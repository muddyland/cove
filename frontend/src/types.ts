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

export interface TailscaleStatus {
  available: boolean
  output: string
}

export interface ContainerLogs {
  source: 'desktop' | 'tailscale' | 'gluetun'
  available: boolean
  output: string
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
  zone_id: number
  zone_name: string | null
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
  use_gluetun: boolean
  ephemeral: boolean
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
  inject_ssh_key: boolean
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

export interface GluetunConfig {
  enabled: boolean
  vpn_type: 'openvpn' | 'wireguard'
  has_config: boolean
  config_filename: string | null
  has_wireguard_private_key: boolean
  has_openvpn_user: boolean
  has_openvpn_password: boolean
}

export interface SshKeyConfig {
  has_key: boolean
  public_key: string | null
  key_type: string | null
  fingerprint: string | null
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
  gluetun_image: string
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

export interface Zone {
  id: number
  public_id: string
  name: string
  status: string
  endpoint_host: string | null
  endpoint_port: number
  enrolled_at: string | null
  last_seen_at: string | null
  created_at: string
  workspace_count: number
}

export interface ZoneEnrollToken {
  token: string
  expires_at: string
  install_command: string
}

export interface ZoneOption {
  id: number
  name: string
}
