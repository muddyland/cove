<template>
  <BaseModal v-model="open" :title="profile ? `Edit VPN profile` : 'Add VPN profile'" width="560px">
    <form class="form" @submit.prevent="save">
      <div class="form-group">
        <label for="vpn-name">Name</label>
        <input
          id="vpn-name" v-model="f.name" required maxlength="64" autocomplete="off"
          placeholder="e.g. Mullvad Sweden, Proton work key"
        />
      </div>

      <div class="form-group">
        <span class="label">Set up from</span>
        <div class="segmented" role="radiogroup" aria-label="Set up from">
          <button
            type="button" role="radio" :aria-checked="f.mode === 'provider'"
            :class="{ on: f.mode === 'provider' }" @click="f.mode = 'provider'"
          >VPN provider</button>
          <button
            type="button" role="radio" :aria-checked="f.mode === 'custom'"
            :class="{ on: f.mode === 'custom' }" @click="f.mode = 'custom'"
          >Config file</button>
        </div>
        <p class="hint">
          <template v-if="f.mode === 'provider'">
            Gluetun knows these providers' servers: give it your key or login and, optionally,
            where to connect. No file needed.
          </template>
          <template v-else>
            Upload a WireGuard <code>.conf</code> or OpenVPN <code>.ovpn</code> — for providers not
            listed, a self-hosted server, or a specific server you already have a file for.
          </template>
        </p>
      </div>

      <!-- Built-in provider -->
      <template v-if="f.mode === 'provider'">
        <div class="row-2">
          <div class="form-group">
            <label for="vpn-provider">Provider</label>
            <select id="vpn-provider" v-model="f.provider" required>
              <option value="" disabled>Choose…</option>
              <option v-for="p in providers" :key="p.value" :value="p.value">{{ p.label }}</option>
            </select>
          </div>
          <div class="form-group">
            <label for="vpn-type">Protocol</label>
            <select id="vpn-type" v-model="f.vpn_type" :disabled="typeChoices.length < 2">
              <option v-for="t in typeChoices" :key="t" :value="t">{{ TYPE_LABEL[t] }}</option>
            </select>
          </div>
        </div>

        <template v-if="f.vpn_type === 'wireguard'">
          <div class="form-group">
            <label for="vpn-wgkey">WireGuard private key</label>
            <SecretInput
              id="vpn-wgkey" v-model="f.wireguard_private_key" :stored="stored.wg"
              placeholder="base64 key from the provider's WireGuard config"
              @clear="clears.wireguard_private_key = true"
            />
          </div>
          <div v-if="provider?.wg_addresses" class="form-group">
            <label for="vpn-wgaddr">WireGuard address</label>
            <input id="vpn-wgaddr" v-model="f.wireguard_addresses" autocomplete="off" placeholder="10.64.12.34/32" />
            <p class="hint">The <code>Address</code> line of the provider's WireGuard config (IPv4 only, unless IPv6 is set up).</p>
          </div>
          <div v-if="provider?.wg_preshared_key" class="form-group">
            <label for="vpn-wgpsk">Pre-shared key</label>
            <SecretInput
              id="vpn-wgpsk" v-model="f.wireguard_preshared_key" :stored="stored.psk"
              placeholder="PresharedKey from the config (if it has one)"
              @clear="clears.wireguard_preshared_key = true"
            />
          </div>
        </template>
        <div v-else class="row-2">
          <div class="form-group">
            <label for="vpn-ouser">Username</label>
            <SecretInput
              id="vpn-ouser" v-model="f.openvpn_user" :stored="stored.user" plain
              placeholder="OpenVPN / service username" @clear="clears.openvpn_user = true"
            />
          </div>
          <div class="form-group">
            <label for="vpn-opass">Password</label>
            <SecretInput
              id="vpn-opass" v-model="f.openvpn_password" :stored="stored.pass"
              placeholder="OpenVPN / service password" @clear="clears.openvpn_password = true"
            />
          </div>
        </div>

        <fieldset class="where">
          <legend>Where to connect <span class="opt">(optional)</span></legend>
          <div class="row-2">
            <div class="form-group">
              <label for="vpn-countries">Countries</label>
              <input id="vpn-countries" v-model="f.server_countries" autocomplete="off" placeholder="Sweden, Switzerland" />
            </div>
            <div class="form-group">
              <label for="vpn-cities">Cities</label>
              <input id="vpn-cities" v-model="f.server_cities" autocomplete="off" placeholder="Stockholm" />
            </div>
          </div>
          <div class="form-group">
            <label for="vpn-hosts">Server hostnames</label>
            <input id="vpn-hosts" v-model="f.server_hostnames" autocomplete="off" placeholder="se-sto-wg-001" />
          </div>
          <p class="hint">Comma-separated, spelled as the provider does. Leave empty for any server. Pinning a hostname breaks if the provider retires it.</p>
        </fieldset>
      </template>

      <!-- Config file -->
      <template v-else>
        <div class="form-group">
          <label for="vpn-ctype">Protocol</label>
          <select id="vpn-ctype" v-model="f.vpn_type">
            <option value="wireguard">WireGuard</option>
            <option value="openvpn">OpenVPN</option>
          </select>
        </div>
        <div class="form-group">
          <label for="vpn-file">Config file ({{ f.vpn_type === 'wireguard' ? '.conf' : '.ovpn' }})</label>
          <input id="vpn-file" type="file" accept=".ovpn,.conf,.txt" @change="onFile" />
          <p class="hint">
            <template v-if="fileName">{{ fileContent !== null ? 'New' : 'Stored' }}: <code>{{ fileName }}</code>{{ fileContent === null ? ' — choose a file to replace it.' : '' }}</template>
            <template v-else>Stored <strong>encrypted</strong> (it usually contains your key).</template>
          </p>
        </div>
        <details class="overrides" :open="stored.wg || stored.user || stored.pass">
          <summary>Override credentials in the file (optional)</summary>
          <div v-if="f.vpn_type === 'wireguard'" class="form-group">
            <label for="vpn-cwgkey">Private key</label>
            <SecretInput
              id="vpn-cwgkey" v-model="f.wireguard_private_key" :stored="stored.wg"
              placeholder="replaces PrivateKey in the file" @clear="clears.wireguard_private_key = true"
            />
          </div>
          <div v-else class="row-2">
            <div class="form-group">
              <label for="vpn-couser">Username</label>
              <SecretInput
                id="vpn-couser" v-model="f.openvpn_user" :stored="stored.user" plain
                placeholder="replaces the file's" @clear="clears.openvpn_user = true"
              />
            </div>
            <div class="form-group">
              <label for="vpn-copass">Password</label>
              <SecretInput
                id="vpn-copass" v-model="f.openvpn_password" :stored="stored.pass"
                placeholder="replaces the file's" @clear="clears.openvpn_password = true"
              />
            </div>
          </div>
          <p class="hint">Handy for using one file with several keys: add a profile per key with the same file.</p>
        </details>
      </template>

      <p v-if="error" class="form-error" role="alert">⚠ {{ error }}</p>
      <div class="form-actions">
        <NeonButton type="button" variant="secondary" :disabled="saving" @click="open = false">Cancel</NeonButton>
        <NeonButton type="submit" variant="primary" :loading="saving">{{ profile ? 'Save' : 'Add profile' }}</NeonButton>
      </div>
    </form>
  </BaseModal>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import BaseModal from './BaseModal.vue'
import NeonButton from './NeonButton.vue'
import SecretInput from './SecretInput.vue'
import { usersApi, type VpnProfileInput } from '@/api/users'
import type { VpnProfile, VpnProvider, VpnType } from '@/types'

const props = defineProps<{ profile: VpnProfile | null; providers: VpnProvider[] }>()
const emit = defineEmits<{ saved: [VpnProfile] }>()
const open = defineModel<boolean>({ default: false })

const TYPE_LABEL: Record<VpnType, string> = { wireguard: 'WireGuard', openvpn: 'OpenVPN' }
const SECRETS = ['wireguard_private_key', 'wireguard_preshared_key', 'openvpn_user', 'openvpn_password'] as const
type Secret = (typeof SECRETS)[number]

const f = reactive({
  name: '',
  mode: 'provider' as 'provider' | 'custom',
  provider: '',
  vpn_type: 'wireguard' as VpnType,
  wireguard_private_key: '',
  wireguard_preshared_key: '',
  wireguard_addresses: '',
  openvpn_user: '',
  openvpn_password: '',
  server_countries: '',
  server_cities: '',
  server_hostnames: '',
})
const clears = reactive<Record<Secret, boolean>>({
  wireguard_private_key: false, wireguard_preshared_key: false, openvpn_user: false, openvpn_password: false,
})
const stored = computed(() => ({
  wg: !!props.profile?.has_wireguard_private_key && !clears.wireguard_private_key,
  psk: !!props.profile?.has_wireguard_preshared_key && !clears.wireguard_preshared_key,
  user: !!props.profile?.has_openvpn_user && !clears.openvpn_user,
  pass: !!props.profile?.has_openvpn_password && !clears.openvpn_password,
}))
const fileContent = ref<string | null>(null)
const fileName = ref<string | null>(null)
const saving = ref(false)
const error = ref('')

const provider = computed(() => props.providers.find(p => p.value === f.provider) ?? null)
const typeChoices = computed<VpnType[]>(() => provider.value?.types ?? ['wireguard', 'openvpn'])
// Keep the protocol valid for the chosen provider (Mullvad is WireGuard-only…).
watch(typeChoices, types => {
  if (f.mode === 'provider' && !types.includes(f.vpn_type)) f.vpn_type = types[0]
})

watch(open, isOpen => {
  if (!isOpen) return
  const p = props.profile
  Object.assign(f, {
    name: p?.name ?? '',
    mode: p?.mode ?? 'provider',
    provider: p?.provider ?? '',
    vpn_type: p?.vpn_type ?? 'wireguard',
    wireguard_private_key: '', wireguard_preshared_key: '', openvpn_user: '', openvpn_password: '',
    wireguard_addresses: p?.wireguard_addresses ?? '',
    server_countries: p?.server_countries ?? '',
    server_cities: p?.server_cities ?? '',
    server_hostnames: p?.server_hostnames ?? '',
  })
  for (const k of SECRETS) clears[k] = false
  fileContent.value = null
  fileName.value = p?.config_filename ?? null
  error.value = ''
}, { immediate: true })

async function onFile(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0]
  if (!file) return
  if (file.size > 128 * 1024) {
    error.value = 'That file is over 128 KB — VPN configs are a few KB. Is it the right file?'
    return
  }
  fileContent.value = await file.text()
  fileName.value = file.name
  error.value = ''
}

async function save() {
  error.value = ''
  if (f.mode === 'provider' && !f.provider) { error.value = 'Choose a provider'; return }
  if (f.mode === 'custom' && !props.profile?.has_config && fileContent.value === null) {
    error.value = 'Choose a config file'
    return
  }
  const payload: VpnProfileInput = {
    name: f.name.trim(),
    mode: f.mode,
    vpn_type: f.vpn_type,
    provider: f.mode === 'provider' ? f.provider : null,
  }
  // Secrets: typed -> replace; cleared -> ''; untouched -> omitted (kept).
  for (const k of SECRETS) {
    if (f[k].trim()) payload[k] = f[k].trim()
    else if (clears[k]) payload[k] = ''
  }
  if (f.mode === 'provider') {
    payload.wireguard_addresses = f.wireguard_addresses
    payload.server_countries = f.server_countries
    payload.server_cities = f.server_cities
    payload.server_hostnames = f.server_hostnames
  } else if (fileContent.value !== null) {
    payload.config_file = fileContent.value
    payload.config_filename = fileName.value
  }
  saving.value = true
  try {
    const saved = props.profile
      ? await usersApi.updateVpnProfile(props.profile.id, payload)
      : await usersApi.createVpnProfile(payload)
    emit('saved', saved)
    open.value = false
  } catch (e: any) {
    error.value = e?.message || 'Saving failed'
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.form { display: flex; flex-direction: column; gap: 14px; }
.form-actions { display: flex; justify-content: flex-end; gap: 10px; }
.row-2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; }
.label {
  display: block; font-size: 11px; letter-spacing: 1px; text-transform: uppercase;
  color: var(--text-muted); margin-bottom: 6px;
}
.segmented { display: flex; border: 1px solid var(--border); border-radius: var(--radius); overflow: hidden; }
.segmented button {
  flex: 1; background: none; border: none; border-right: 1px solid var(--border);
  color: var(--text-muted); font-size: 12px; padding: 8px 6px; cursor: pointer; transition: all 0.15s;
}
.segmented button:last-child { border-right: none; }
.segmented button.on { background: var(--accent); color: #06060f; }
.hint { font-family: var(--font-mono); font-size: 12px; line-height: 1.5; color: var(--text-muted); margin: 6px 0 0; }
.hint code { font-family: var(--font-mono); font-size: 11px; color: var(--accent); }
.where { border: 1px solid var(--border); border-radius: var(--radius); padding: 12px 14px 14px; margin: 0; }
.where legend { font-size: 11px; letter-spacing: 1px; text-transform: uppercase; color: var(--text-muted); padding: 0 6px; }
.opt { text-transform: none; letter-spacing: 0; }
.where > :not(legend) + :not(legend) { margin-top: 10px; }
.row-2 > .form-group { margin: 0; }
.overrides { border: 1px solid var(--border); border-radius: var(--radius); padding: 10px 14px; }
.overrides summary { cursor: pointer; font-size: 12px; color: var(--text-muted); }
.overrides[open] summary { margin-bottom: 12px; }
.form-error { color: var(--red); font-size: 13px; margin: 0; }
</style>
