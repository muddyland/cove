<template>
  <div class="access-fields">
    <div class="group-label"><ShieldCheck :size="12" />Permissions</div>

    <label class="checkbox-row">
      <input type="checkbox" v-model="form.inject_ssh_key" /><KeyRound :size="14" class="row-ico" /><span>Inject my SSH key</span>
    </label>
    <p class="hint">
      Copy your account SSH key into the container's <code>~/.ssh</code> at launch
      (set it in Preferences → SSH key). Turn off to keep this workspace key-free.
    </p>

    <label class="checkbox-row">
      <input type="checkbox" v-model="form.allow_sudo" /><ShieldAlert :size="14" class="row-ico" /><span>Allow sudo</span>
    </label>
    <p class="hint">
      Allow in-container <code>sudo</code>. Admins can force-disable sudo globally in
      Settings, which overrides this choice.
    </p>

    <div class="group-label"><MonitorPlay :size="12" />Streaming</div>

    <label class="checkbox-row">
      <input type="checkbox" v-model="form.pixelflux_wayland" /><MonitorPlay :size="14" class="row-ico" /><span>Wayland streaming</span>
    </label>
    <p class="hint">
      Stream the desktop over Wayland (default). Turn off to force the X11 fallback
      (<code>PIXELFLUX_WAYLAND=false</code>) if an app has Wayland compatibility issues.
    </p>

    <template v-if="gpuEnabled">
      <label class="checkbox-row">
        <input type="checkbox" v-model="form.gpu_accel" /><Zap :size="14" class="row-ico" /><span>GPU acceleration</span>
      </label>
      <p class="hint">
        Use the host GPU for hardware video encode (VAAPI), offloading the stream from
        the CPU. Best with Wayland streaming on. Requires a GPU on the workspace's host.
      </p>
    </template>

    <template v-if="showBrowserLock">
      <label class="checkbox-row">
        <input type="checkbox" v-model="form.clear_browser_lock" /><Lock :size="14" class="row-ico" /><span>Clear stale browser lock</span>
      </label>
      <p class="hint">
        For browser workspaces: remove a leftover single-instance lock from the saved
        profile at launch. Enable if the browser won't start after an unclean halt.
      </p>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ShieldCheck, ShieldAlert, KeyRound, MonitorPlay, Zap, Lock } from 'lucide-vue-next'

export interface AccessForm {
  allow_sudo: boolean
  inject_ssh_key: boolean
  pixelflux_wayland: boolean
  clear_browser_lock: boolean
  gpu_accel: boolean
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
.checkbox-row {
  display: flex; align-items: center; gap: 8px; cursor: pointer;
  font-size: 12px; color: var(--text); text-transform: none; letter-spacing: 0.5px;
}
.checkbox-row input { width: auto; margin: 0; }
.row-ico { color: var(--text-muted); flex: none; }
.hint { font-size: 11px; line-height: 1.5; color: var(--text-muted); margin: 4px 0 0; }
.hint code { font-family: var(--font-mono); font-size: 10px; color: var(--accent); }
</style>
