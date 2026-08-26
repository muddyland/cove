<template>
  <div class="access-fields">
    <div class="group-label"><ShieldCheck :size="12" />Permissions</div>

    <ToggleRow v-model="form.inject_ssh_key">
      <template #icon><KeyRound :size="14" /></template>
      Inject my SSH key
      <template #hint>
        Copy your account SSH key into the container's <code>~/.ssh</code> at launch
        (set it in Preferences → SSH key). Turn off to keep this workspace key-free.
      </template>
    </ToggleRow>

    <ToggleRow v-model="form.allow_sudo">
      <template #icon><ShieldAlert :size="14" /></template>
      Allow sudo
      <template #hint>
        Allow in-container <code>sudo</code>. Admins can force-disable sudo globally in
        Settings, which overrides this choice.
      </template>
    </ToggleRow>

    <div class="group-label"><FolderSync :size="12" />Storage</div>

    <ToggleRow v-model="form.shared_profile">
      <template #icon><FolderSync :size="14" /></template>
      Shared profile
      <template #hint>
        Share one persistent <code>/config</code> home across all your shared-profile
        workspaces — dotfiles, proot apps, browser profiles and VSCodium workspaces carry
        between distros. The profile is per-user and is <strong>never</strong> deleted when a
        workspace is removed. Best used one workspace at a time (concurrent desktops sharing a
        home can hit profile/browser locks — see “Clear stale browser lock”). Enabling this on
        an existing workspace switches its home; the old per-workspace files stay on disk.
      </template>
    </ToggleRow>

    <div class="group-label"><MonitorPlay :size="12" />Streaming</div>

    <ToggleRow v-model="form.pixelflux_wayland">
      <template #icon><MonitorPlay :size="14" /></template>
      Wayland streaming
      <template #hint>
        Stream the desktop over Wayland (default). Turn off to force the X11 fallback
        (<code>PIXELFLUX_WAYLAND=false</code>) if an app has Wayland compatibility issues.
      </template>
    </ToggleRow>

    <template v-if="gpuEnabled">
    <ToggleRow v-model="form.gpu_accel">
      <template #icon><Zap :size="14" /></template>
      GPU acceleration
      <template #hint>
        Use the host GPU for hardware video encode (VAAPI), offloading the stream from
        the CPU. Best with Wayland streaming on. Requires a GPU on the workspace's host.
      </template>
    </ToggleRow>
    </template>

    <template v-if="showBrowserLock">
    <ToggleRow v-model="form.clear_browser_lock">
      <template #icon><Lock :size="14" /></template>
      Clear stale browser lock
      <template #hint>
        For browser workspaces: remove a leftover single-instance lock from the saved
        profile at launch. Enable if the browser won't start after an unclean halt.
      </template>
    </ToggleRow>
    </template>
  </div>
</template>

<script setup lang="ts">
import ToggleRow from './ToggleRow.vue'
import { ShieldCheck, ShieldAlert, KeyRound, MonitorPlay, Zap, Lock, FolderSync } from 'lucide-vue-next'

export interface AccessForm {
  allow_sudo: boolean
  inject_ssh_key: boolean
  pixelflux_wayland: boolean
  clear_browser_lock: boolean
  gpu_accel: boolean
  shared_profile: boolean
}

defineProps<{
  form: AccessForm
  gpuEnabled?: boolean
  // Browser-only; hidden for desktop/app workspaces.
  showBrowserLock?: boolean
}>()
</script>

<style scoped>
.access-fields { display: flex; flex-direction: column; gap: 16px; }
.group-label {
  display: flex; align-items: center; gap: 6px;
  font-size: 11px; letter-spacing: 1px; text-transform: uppercase; color: var(--text-muted);
  padding-bottom: 4px; border-bottom: 1px solid var(--border);
}
.group-label:not(:first-child) { margin-top: 6px; }
</style>
