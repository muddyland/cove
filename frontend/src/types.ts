export interface User {
  id: number
  username: string
  is_admin: boolean
  docker_allowed: boolean
  /** False: every zone. True: only allowed_zone_ids. Admins always have every zone. */
  zones_restricted: boolean
  allowed_zone_ids: number[]
  auth_provider: string
  created_at: string
  last_login_at: string | null
}

/** What the admin user form submits (password only when set). */
export interface UserFormPayload {
  username: string
  password?: string
  is_admin: boolean
  docker_allowed: boolean
  zones_restricted: boolean
  allowed_zone_ids: number[]
}

export interface AuthConfig {
  oidc_enabled: boolean
  oidc_provider_name: string
  needs_setup: boolean
  oidc_only: boolean
}

export type ImageType = 'desktop' | 'browser' | 'link' | 'app'

export interface ExtensionInfo {
  // False when a build ships without the vendored extension.
  available: boolean
  version: string | null
  size_bytes: number
  filename: string
}

export interface BrowserFeatures {
  // Start-up options this browser image honours; the launcher hides the rest
  // rather than passing a flag the browser ignores.
  kiosk: boolean
  fullscreen: boolean
  dark: boolean
}

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
  // Which start-up options this image's browser honours (all false for a
  // non-browser image).
  browser: BrowserFeatures
}

export type WorkspaceStatus = 'creating' | 'running' | 'stopping' | 'migrating' | 'stopped' | 'error'

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

export interface ProotApp {
  // Default-repository app name; null for an app installed from another image.
  name: string | null
  // Upstream display name and icon, when known.
  full_name: string | null
  icon_url: string | null
  folder: string
  installed: boolean
  downloading: boolean
  // In the workspace's saved list, so it's reinstalled on boot and migration.
  in_config: boolean
  installed_digest: string | null
  latest_digest: string | null
  // null when unknown (not checked, or the registry couldn't be reached).
  update_available: boolean | null
}

export interface ProotApps {
  available: boolean
  arch: string | null
  checked: boolean
  // A catalog app's latest build couldn't be resolved (apps outside the catalog aren't checked).
  check_failed: boolean
  apps: ProotApp[]
}

export interface AppImageApp {
  // Install directory, derived from the URL it came from.
  slug: string
  name: string
  // Where it was downloaded from; null for one installed before Cove recorded
  // provenance, so updating it needs a URL.
  url: string | null
  size_kb: number
  installed_at: number | null
  // False for one in the saved list that isn't on disk (e.g. after a migration).
  installed?: boolean
}

export interface ProotAppMeta {
  full_name: string | null
  icon_url: string | null
}

export type ProotTaskOp = 'install' | 'update' | 'remove'
export type ProotTaskState = 'queued' | 'running' | 'done' | 'failed' | 'interrupted'

export type AppKind = 'proot' | 'appimage'

export interface ProotTask {
  id: string
  kind: AppKind
  op: ProotTaskOp
  state: ProotTaskState
  exit_code: number | null
  apps: string[]
  failed_apps: string[]
  current_app: string | null
  done_count: number
  // Epoch seconds, from the workspace's clock.
  created_at: number | null
  started_at: number | null
  finished_at: number | null
  // Queued by the workspace coming up (its missing saved apps), not by a user.
  boot?: boolean
}

export interface WorkspaceProotTasks {
  workspace_id: number
  workspace_name: string
  tasks: ProotTask[]
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
  // When the server-side screen capture was taken (null if there is none). The
  // image is fetched separately; this only says whether to ask, and changes when
  // a newer frame exists.
  preview_at: string | null
  // When the site favicon was fetched, or null if this workspace has none (not a
  // browser, several sites open, or nothing decodable at the site). The PNG is
  // fetched separately; this says whether to ask, and changes when it's refetched.
  favicon_at: string | null
  // Running AND its stream has rendered a first frame (or the server's fallback
  // window for uncapturable images passed). Connect on this, not on status: a
  // stream opened before Selkies renders never recovers without a reload.
  connectable: boolean
  browser: BrowserFeatures
  use_tailscale: boolean
  use_gluetun: boolean
  tailscale_profile_id: number | null
  gluetun_profile_id: number | null
  routing_profile_name: string | null
  ephemeral: boolean
  auto_remove: boolean
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
  pixelflux_wayland: boolean
  clear_browser_lock: boolean
  gpu_accel: boolean
  use_docker: boolean
  shared_profile: boolean
  // Live image download while a launch is still pulling (null otherwise). The
  // total grows as layers are discovered, so treat it as an estimate.
  pull_progress: PullProgress | null
  // When the user last pushed back the max-runtime auto-halt (see /extend).
  runtime_extended_at: string | null
}

export interface PullProgress {
  current: number
  total: number
  layers: number
}

export interface RuntimePolicy {
  // Admin max runtime in hours; 0 = unlimited.
  max_hours: number
}

export interface LanPolicy {
  enabled: boolean
  subnets: string[]
}

export interface GpuPolicy {
  enabled: boolean
}

export interface DockerPolicy {
  enabled: boolean
}

// A saved Tailscale identity (auth key + optional control server). The key is
// never returned, only whether one is stored.
export interface TailscaleProfile {
  id: number
  name: string
  has_auth_key: boolean
  login_server: string | null
  // Why it can't be used yet (null when ready).
  problem: string | null
  // Names of the workspaces that route through it.
  used_by: string[]
}

export type VpnType = 'openvpn' | 'wireguard'

// One of gluetun's built-in providers that works from a key / login alone.
export interface VpnProvider {
  value: string
  label: string
  types: VpnType[]
  wg_addresses: boolean
  wg_preshared_key: boolean
}

// A saved Gluetun VPN connection: an uploaded config file ("custom") or a
// built-in provider with a key / login. Secrets come back as presence flags.
export interface VpnProfile {
  id: number
  name: string
  mode: 'custom' | 'provider'
  provider: string | null
  provider_label: string | null
  vpn_type: VpnType
  has_config: boolean
  config_filename: string | null
  has_wireguard_private_key: boolean
  has_wireguard_preshared_key: boolean
  wireguard_addresses: string | null
  has_openvpn_user: boolean
  has_openvpn_password: boolean
  server_countries: string | null
  server_cities: string | null
  server_hostnames: string | null
  problem: string | null
  used_by: string[]
  // Running/starting workspace holding this profile's one connection.
  active_in: string | null
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

export interface TrashEntry {
  id: number
  zone_id: number
  original_path: string
  name: string
  is_dir: boolean
  size: number
  deleted_at: string
  expires_at: string
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
  workspace_pids_limit: number
  workspace_gpu_accel: boolean
  workspace_gpu_render_node: string
  workspace_gpu_render_gid: number
  workspace_docker: boolean
  dind_image: string
  trash_retention_days: number
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
  // The workspace a workspace.* entry refers to, while it still exists.
  workspace_id: number | null
  workspace_name: string | null
}

export interface AuditFacets {
  users: string[]
  actions: string[]
}

export interface AuditQuery {
  user?: string
  action?: string
  since?: string
  until?: string
  q?: string
  limit?: number
  offset?: number
}

export interface ImageDetail {
  status: 'present' | 'absent' | 'pulling'
  size: number | null
  updated_at: string | null
  workspace_count: number
  pull_progress: PullProgress | null
}

export interface ImageUpdateCheck {
  up_to_date: boolean | null
  local_digest: string | null
  remote_digest: string | null
  detail: string | null
}

export interface ZoneHealth {
  ok: boolean
  latency_ms: number | null
  docker_version: string | null
  containers_running: number | null
  error: string | null
  // Whether the zone's agent allows the Docker-in-Docker sidecar (null for the
  // local zone or an agent too old to say).
  agent_dind?: boolean | null
  // Whether the agent runs the control plane's own agent image (false = it
  // needs an Update; null for the local zone or when it can't be told).
  agent_current?: boolean | null
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
  // Per-zone GPU overrides (null = inherit Administration → Settings).
  gpu_accel: boolean | null
  gpu_render_node: string | null
  gpu_render_gid: number | null
  // Docker-in-Docker on this zone, and the agent host's own opt-in as last
  // reported (null = unknown).
  docker_enabled: boolean
  agent_allows_docker: boolean | null
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

export interface StorageCategory {
  key: string
  label: string
  total: number
  active: number
  size: number
  reclaimable: number
}

export interface HostDisk {
  total: number
  used: number
  free: number
}

export interface ZoneStorage {
  zone_id: number
  zone_name: string
  online: boolean
  error: string | null
  host: HostDisk | null
  categories: StorageCategory[]
}

export interface StorageInfo {
  zones: ZoneStorage[]
}

export interface PruneResult {
  zone_id: number
  deep: boolean
  space_reclaimed: number
}

// ── System info (stream page) ──────────────────────────────────────────────
export interface SysContainer {
  id: string
  name: string
  state: string | null
  started_at: string | null
  restart_count: number
  image_id: string
  cpu_limit: number | null
  mem_limit: number | null
  pids_limit: number | null
  shm_size: number | null
  privileged: boolean
  no_new_privileges: boolean
}

export interface SysResources {
  cpu_pct: number | null
  mem_used: number | null
  mem_limit: number | null
  mem_pct: number | null
  net_rx: number | null
  net_tx: number | null
  blk_read: number | null
  blk_write: number | null
  pids: number | null
  pids_limit: number | null
  online_cpus: number | null
}

export interface SysSidecar {
  name: string
  state: string | null
  health: string | null
  started_at: string | null
  restart_count: number
}

export interface SysNetwork {
  mode: 'direct' | 'tailscale' | 'gluetun'
  profile_name: string | null
  vpn_provider: string | null
  vpn_type: string | null
  vpn_servers: string | null
  tailscale_ip: string | null
  tailscale_hostname: string | null
  login_server: string | null
  exit_node: string | null
  accept_routes: boolean | null
  accept_dns: boolean | null
  bridge_ip: string | null
  custom_dns: string | null
  lan_access: boolean
  lan_subnets: string[]
  sidecar: SysSidecar | null
}

export interface SystemInfo {
  status: string
  zone_name: string | null
  image_name: string
  image_ref: string | null
  started_at: string | null
  auto_stop_at: string | null
  container: SysContainer | null
  resources: SysResources | null
  network: SysNetwork
  storage: { kind: 'persistent' | 'ephemeral' | 'shared'; measurable: boolean }
  features: Record<string, boolean>
}

export interface DiskUsage { bytes: number; files: number; partial: boolean }

export interface PublicIp {
  ip: string
  city: string | null
  region: string | null
  country: string | null
  org: string | null
  timezone: string | null
  source: string
}

// ── Files inside a running workspace (stream page) ─────────────────────────
export interface WsFileEntry { name: string; type: 'dir' | 'file' | 'link'; size: number; modified: string }
export interface WsListing { path: string; entries: WsFileEntry[] }
export interface WsUpload { path: string; name: string; size: number }

// ── Thin clients ────────────────────────────────────────────────────────────

export type ThinClientRotation = 'normal' | '90' | '180' | '270'
export type ThinClientAutoOpen = 'off' | 'running' | 'pinned'

export interface ThinClientPolicy {
  screen_off_minutes: number
  brightness: number | null
  rotation: ThinClientRotation
  scale: number | null
  on_hours_start: string | null
  on_hours_end: string | null
  media_keeps_awake: boolean
  volume: number | null
  signout_minutes: number
  session_max_hours: number
  auto_open: ThinClientAutoOpen
  pinned_workspace_id: string | null
  extra_allowed_hosts: string[]
  color_scheme: 'dark' | 'light' | 'system'
  auto_update: boolean
  maintenance_hour: number
  auto_reboot: boolean
}

export interface ThinClientOutput {
  name: string | null
  make: string | null
  model: string | null
  active: boolean | null
  power: boolean | null
  mode: string | null
  transform: string | null
  scale: number | null
}

/** What the device last reported. Untrusted, display-only, and any field may
 *  be missing (older agents, a device that never checked in). */
export interface ThinClientState {
  hostname?: string
  ips?: string[]
  os?: string
  kernel?: string
  agent_version?: string
  browser_version?: string | null
  sway_version?: string | null
  session_running?: boolean
  uptime_s?: number | null
  display_on?: boolean
  idle_s?: number
  media_playing?: boolean
  volume?: number | null
  muted?: boolean | null
  /** The default output's name; null when there's no real audio device. */
  audio_output?: string | null
  browser_running?: boolean
  browser_page?: string | null
  outputs?: ThinClientOutput[]
  upgradable?: number | null
  reboot_required?: boolean
  last_update_at?: string | null
  last_error?: string | null
}

export interface ThinClient {
  id: number
  public_id: string
  name: string
  location: string | null
  status: 'pending' | 'enrolled'
  online: boolean
  enrolled_at: string | null
  last_seen_at: string | null
  created_at: string
  policy: ThinClientPolicy
  policy_version: number
  applied_policy_version: number | null
  state: ThinClientState
  session_username: string | null
  session_started_at: string | null
  keep_awake_until: string | null
  bound_at: string | null
  screenshot_at: string | null
  pending_commands: number
  latest_agent_version: string | null
}

export type ThinClientCommandKind =
  | 'screen_on' | 'screen_off' | 'restart_browser' | 'identify' | 'screenshot'
  | 'apt_upgrade' | 'reboot' | 'poweroff' | 'logs'

export interface ThinClientCommand {
  id: number
  kind: ThinClientCommandKind
  args: Record<string, unknown>
  status: 'queued' | 'sent' | 'done' | 'failed' | 'lost'
  result: string | null
  created_by: string | null
  created_at: string
  sent_at: string | null
  finished_at: string | null
}

export interface ThinClientEnrollToken {
  code: string
  expires_at: string
  install_command: string
}

export interface ThinClientWorkspaceOption {
  public_id: string
  name: string
  username: string
  status: string
}

/** The thin client the current browser runs on (only on a kiosk). */
export interface ThinClientSelf {
  public_id: string
  name: string
  auto_open: ThinClientAutoOpen
  pinned_workspace_id: string | null
  signout_minutes: number
  screen_off_minutes: number
}
