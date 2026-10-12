import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'

vi.mock('@/api/workspaces', () => ({
  workspacesApi: { info: vi.fn(), diskUsage: vi.fn(), publicIp: vi.fn(), tailscaleStatus: vi.fn() },
}))

import { workspacesApi } from '@/api/workspaces'
import SystemInfoModal from '@/components/SystemInfoModal.vue'
import type { SystemInfo, Workspace } from '@/types'

const ws = { id: 3, name: 'Research', status: 'running', use_gluetun: true, use_tailscale: false, runtime_extended_at: null } as unknown as Workspace

const info = (over: Partial<SystemInfo> = {}): SystemInfo => ({
  status: 'running', zone_name: 'Local', image_name: 'Webtop XFCE', image_ref: 'lscr.io/linuxserver/webtop:latest',
  started_at: new Date(Date.now() - 65 * 60_000).toISOString(),
  auto_stop_at: new Date(Date.now() + 30 * 60_000).toISOString(),
  container: {
    id: 'abc123', name: 'cove-ws-3', state: 'running', started_at: null, restart_count: 0, image_id: 'def456',
    cpu_limit: 2, mem_limit: 4 * 1024 ** 3, pids_limit: 512, shm_size: 1024 ** 3, privileged: false, no_new_privileges: true,
  },
  resources: {
    cpu_pct: 10, mem_used: 1024 ** 3, mem_limit: 4 * 1024 ** 3, mem_pct: 25,
    net_rx: 10 * 1024 ** 2, net_tx: 1024 ** 2, blk_read: 2048, blk_write: 1024, pids: 87, pids_limit: 512, online_cpus: 8,
  },
  network: {
    mode: 'gluetun', profile_name: 'Mullvad SE', vpn_provider: 'Mullvad', vpn_type: 'WireGuard', vpn_servers: 'Sweden',
    tailscale_ip: null, tailscale_hostname: null, login_server: null, exit_node: null, accept_routes: null, accept_dns: null,
    bridge_ip: null, custom_dns: null, lan_access: false, lan_subnets: [],
    sidecar: { name: 'cove-gluetun-3', state: 'running', health: 'healthy', started_at: null, restart_count: 0 },
  },
  storage: { kind: 'persistent', measurable: true },
  features: { wayland: true, gpu_accel: false, docker: true, sudo: false, ssh_key: true, kiosk: false },
  ...over,
})

describe('SystemInfoModal', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    document.body.innerHTML = ''
    setActivePinia(createPinia())
    vi.mocked(workspacesApi.info).mockResolvedValue(info())
    vi.mocked(workspacesApi.diskUsage).mockResolvedValue({ bytes: 3 * 1024 ** 3, files: 12345, partial: true })
  })
  afterEach(() => { document.body.innerHTML = '' })

  const mountIt = async () => {
    const w = mount(SystemInfoModal, { props: { modelValue: true, ws }, attachTo: document.body })
    await flushPromises()
    return w
  }

  it('shows uptime, auto-stop, route, resources, storage and container in one place', async () => {
    const w = await mountIt()
    const t = document.body.textContent!
    expect(t).toContain('1 h 5 min')
    expect(t).toMatch(/in (29|30) min/)
    expect(t).toContain('VPN · Mullvad SE')
    // CPU against the 2-core cap: 10% of 8 cores = 0.8 cores = 40% of the cap.
    expect(t).toContain('capped at 2 cores')
    expect(document.body.querySelector<HTMLElement>('.fill')!.style.width).toBe('40%')
    expect(t).toContain('1.0 GB / 4.0 GB')
    expect(t).toContain('87 / 512 max')
    expect(t).toContain('↓ 10 MB')
    expect(t).toContain('connected')
    expect(t).toContain('over 3.0 GB')
    expect(t).toContain('12,345+ files')
    expect(t).toContain('no-new-privileges')
    w.unmount()
  })

  it('checks the public IP only when asked', async () => {
    vi.mocked(workspacesApi.publicIp).mockResolvedValue({
      ip: '185.65.1.2', city: 'Stockholm', region: null, country: 'SE', org: 'AS39351', timezone: null, source: 'ipinfo.io',
    })
    const w = await mountIt()
    expect(workspacesApi.publicIp).not.toHaveBeenCalled()
    const btn = [...document.body.querySelectorAll<HTMLButtonElement>('.link-btn')].find(b => b.textContent!.includes('Check'))!
    btn.click()
    await flushPromises()
    expect(document.body.textContent).toContain('185.65.1.2')
    expect(document.body.textContent).toContain('Stockholm, SE')
    w.unmount()
  })

  it('flags a VPN tunnel whose health check fails, and skips sizing an ephemeral home', async () => {
    const base = info()
    vi.mocked(workspacesApi.info).mockResolvedValue(info({
      network: { ...base.network, sidecar: { ...base.network.sidecar!, health: 'unhealthy' } },
      storage: { kind: 'ephemeral', measurable: false },
    }))
    const w = await mountIt()
    expect(document.body.textContent).toContain('down (health check failing)')
    expect(document.body.querySelector('.dot.bad')).not.toBeNull()
    expect(workspacesApi.diskUsage).not.toHaveBeenCalled()
    expect(document.body.textContent).toContain('wiped when it stops')
    w.unmount()
  })

  it('emits extend from the auto-stop row', async () => {
    const w = await mountIt()
    const btn = [...document.body.querySelectorAll<HTMLButtonElement>('.link-btn')].find(b => b.textContent!.includes('Extend'))!
    btn.click()
    expect(w.emitted('extend')).toHaveLength(1)
    w.unmount()
  })
})
