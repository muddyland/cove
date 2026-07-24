<template>
  <div class="apps-fields">
    <template v-if="dockerEnabled">
      <label class="checkbox-row">
        <input type="checkbox" v-model="form.use_docker" /><Container :size="14" class="row-ico" /><span>Docker (dev)</span>
      </label>
      <p class="hint">
        Run <code>docker</code> inside this workspace via a per-workspace nested daemon.
        Runs a <strong>privileged</strong> sidecar — grant only to trusted users. Nested
        images and state are discarded when the workspace is halted.
      </p>
    </template>

    <div class="form-group">
      <label><Package :size="13" />Install packages</label>
      <input v-model="form.install_packages" type="text" placeholder="git vim htop" />
      <p v-if="pkgError" class="field-error">{{ pkgError }}</p>
      <p v-else class="hint">
        Distro packages installed at launch via the LinuxServer
        <code>universal-package-install</code> mod.
      </p>
    </div>

    <div class="form-group">
      <label><Box :size="13" />proot-apps</label>
      <ProotAppsSelect v-model="form.proot_apps" />
      <p class="hint">Portable apps via LinuxServer <code>proot-apps</code>. Select one or more.</p>
    </div>

    <div class="form-group">
      <label><Download :size="13" />AppImage apps</label>
      <textarea v-model="form.appimages" rows="2" placeholder="https://example.com/App.AppImage" />
      <p v-if="appImageError" class="field-error">{{ appImageError }}</p>
      <p v-else class="hint">
        One AppImage URL per line. Each is downloaded, extracted, and given a desktop
        launcher (Electron apps run with <code>--no-sandbox</code>).
      </p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { Container, Package, Box, Download } from 'lucide-vue-next'
import ProotAppsSelect from './ProotAppsSelect.vue'

export interface AppsForm {
  install_packages: string
  proot_apps: string[]
  appimages: string
  use_docker: boolean
}

defineProps<{
  form: AppsForm
  dockerEnabled?: boolean
  // Validation owned by the parent (see utils/workspaceForm) and passed in.
  pkgError?: string
  appImageError?: string
}>()
</script>

<style scoped>
.apps-fields { display: flex; flex-direction: column; gap: 16px; }
.form-group label { display: inline-flex; align-items: center; gap: 6px; }
.checkbox-row {
  display: flex; align-items: center; gap: 8px; cursor: pointer;
  font-size: 12px; color: var(--text); text-transform: none; letter-spacing: 0.5px;
}
.checkbox-row input { width: auto; margin: 0; }
.row-ico { color: var(--text-muted); flex: none; }
.hint { font-size: 11px; line-height: 1.5; color: var(--text-muted); margin: 4px 0 0; }
.hint code { font-family: var(--font-mono); font-size: 10px; color: var(--accent); }
.field-error { font-size: 11px; color: var(--red); margin: 4px 0 0; }
</style>
