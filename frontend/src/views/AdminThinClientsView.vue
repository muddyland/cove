<template>
  <AppShell>
    <div class="page-header">
      <div>
        <h2>// THIN CLIENTS</h2>
        <p class="subtitle">
          Kiosk screens that boot straight into Cove. Each one dials out to Cove, so it needs no open port.
        </p>
      </div>
      <NeonButton variant="primary" @click="showCreate = true"><Plus :size="14" /> Add Thin Client</NeonButton>
    </div>

    <LoadingSpinner v-if="loading" block />
    <div v-else-if="!clients.length" class="empty">
      <MonitorSmartphone :size="28" />
      <p>No thin clients yet. Add one, then run its install command on a fresh Debian machine.</p>
    </div>
    <div v-else class="grid">
      <article v-for="c in clients" :key="c.id" class="tc-card" :class="cardClass(c)">
        <div class="shot" :class="{ empty: !shots[c.id] }" @click="c.status === 'enrolled' && openShot(c)">
          <img v-if="shots[c.id]" :src="shots[c.id]" alt="" />
          <template v-else>
            <MonitorSmartphone :size="22" />
            <span v-if="c.status === 'enrolled'">no screenshot yet</span>
          </template>
          <span v-if="c.status === 'enrolled' && c.state.display_on === false" class="shot-tag">SCREEN OFF</span>
          <div class="scan" aria-hidden="true" />
        </div>

        <header class="tc-head">
          <div class="tc-title" :title="c.name">{{ c.name }}</div>
          <span class="badge" :class="badge(c).cls">{{ badge(c).label }}</span>
        </header>
        <div class="tc-sub">
          <span v-if="c.location">{{ c.location }}</span>
          <span v-if="c.state.hostname">{{ c.state.hostname }}</span>
          <span v-if="c.state.ips?.length">{{ c.state.ips[0] }}</span>
        </div>

        <dl v-if="c.status === 'enrolled'" class="facts">
          <div>
            <dt>Session</dt>
            <dd>
              <template v-if="c.session_username">
                <UserRound :size="12" /> {{ c.session_username }}
                <span class="muted" v-if="c.session_started_at">· {{ relativeTime(c.session_started_at) }}</span>
              </template>
              <span v-else class="muted">nobody signed in</span>
            </dd>
          </div>
          <div>
            <dt>Screen</dt>
            <dd>
              <span :class="c.state.display_on === false ? 'muted' : 'ok'">
                {{ c.state.display_on === false ? 'off' : c.state.display_on ? 'on' : '—' }}
              </span>
              <span v-if="c.state.idle_s !== undefined" class="muted">&nbsp;· idle {{ fmtIdle(c.state.idle_s) }}</span>
              <span v-if="c.state.media_playing" class="accent2">· ♪ playing</span>
              <span v-if="awake(c)" class="accent2" :title="`Kept awake until ${absoluteTime(c.keep_awake_until)}`">
                · kept awake
              </span>
            </dd>
          </div>
          <div>
            <dt>Display</dt>
            <dd>{{ displaySummary(c) }}</dd>
          </div>
          <div v-if="c.state.audio_output === null && c.state.session_running">
            <dt>Audio</dt>
            <dd class="warn" title="PipeWire found no sound card it can play through (a VM's sound device may need an audio backend)">
              no audio output
            </dd>
          </div>
          <div v-else-if="c.state.volume !== undefined && c.state.volume !== null">
            <dt>Audio</dt>
            <dd :title="c.state.audio_output ?? ''">
              <span :class="c.state.muted ? 'muted' : ''">{{ c.state.volume }}%</span>
              <span v-if="c.state.muted" class="warn">&nbsp;· muted</span>
            </dd>
          </div>
          <div>
            <dt>System</dt>
            <dd>
              {{ c.state.os || '—' }}
              <span v-if="c.state.upgradable" class="warn">· {{ c.state.upgradable }} update{{ c.state.upgradable === 1 ? '' : 's' }}</span>
              <span v-if="c.state.reboot_required" class="warn">· reboot needed</span>
            </dd>
          </div>
          <div>
            <dt>Agent</dt>
            <dd>
              {{ c.state.agent_version || '—' }}
              <span
                v-if="c.latest_agent_version && c.state.agent_version && c.latest_agent_version !== c.state.agent_version"
                class="warn"
              >→ {{ c.latest_agent_version }}</span>
              <span v-if="c.applied_policy_version !== null && c.applied_policy_version !== c.policy_version" class="warn">
                · policy pending
              </span>
            </dd>
          </div>
          <div>
            <dt>Seen</dt>
            <dd :title="c.last_seen_at ? absoluteTime(c.last_seen_at) : ''">
              {{ c.last_seen_at ? relativeTime(c.last_seen_at) : 'never' }}
              <span v-if="c.pending_commands" class="muted">· {{ c.pending_commands }} queued</span>
            </dd>
          </div>
          <div v-if="c.state.last_error" class="err-row">
            <dt>Error</dt>
            <dd :title="c.state.last_error">{{ c.state.last_error }}</dd>
          </div>
        </dl>
        <p v-else class="pending-hint">
          Waiting for its first install. Mint an enrolment code and run the command on the device.
        </p>

        <footer class="tc-actions">
          <template v-if="c.status === 'enrolled'">
            <NeonButton
              variant="ghost"
              :loading="busy[c.id] === 'screen'"
              :title="c.state.display_on === false ? 'Wake the screen' : 'Turn the screen off until someone touches it'"
              @click="send(c, c.state.display_on === false ? 'screen_on' : 'screen_off', {}, 'screen')"
            >
              <Sun v-if="c.state.display_on === false" :size="13" /><MoonStar v-else :size="13" />
              {{ c.state.display_on === false ? 'Wake' : 'Sleep' }}
            </NeonButton>
            <NeonButton
              variant="ghost"
              :disabled="!c.session_username"
              :loading="busy[c.id] === 'signout'"
              title="Sign the current user out of this screen only"
              @click="signOut(c)"
            ><LogOut :size="13" /> Sign out</NeonButton>
            <NeonButton variant="ghost" @click="openSettings(c)"><SlidersHorizontal :size="13" /> Settings</NeonButton>
          </template>
          <NeonButton v-else variant="primary" @click="openEnroll(c)"><KeyRound :size="13" /> Enroll</NeonButton>
          <RowMenu :items="menuFor(c)" :label="`Actions for ${c.name}`" />
        </footer>
      </article>
    </div>

    <!-- Create -->
    <BaseModal v-model="showCreate" title="Add Thin Client">
      <form class="form" @submit.prevent="handleCreate">
        <label>Name<input v-model="form.name" required maxlength="128" placeholder="Workbench" /></label>
        <label>
          Location
          <input v-model="form.location" maxlength="256" placeholder="Downstairs, by the 3D printer" />
          <small>Optional. Helps tell screens apart.</small>
        </label>
        <NeonButton variant="primary" type="submit" :loading="creating">Create</NeonButton>
      </form>
    </BaseModal>

    <!-- Enrol -->
    <BaseModal v-model="showEnroll" :title="`Enroll ${enrollTarget?.name ?? ''}`" width="680px">
      <p class="hint">
        On a fresh <strong>Debian 12 or 13</strong> install (the netinst with only "standard system utilities"
        is enough), run this as root. It installs the kiosk and enrols the machine. The code works once.
      </p>
      <div class="ttl-row">
        <span>Code valid for</span>
        <select v-model.number="enrollTtl" @change="mintCode">
          <option :value="1">1 hour</option>
          <option :value="24">24 hours</option>
          <option :value="168">7 days (for an install USB)</option>
        </select>
        <span v-if="enrollToken" class="muted">until {{ absoluteTime(enrollToken.expires_at) }}</span>
      </div>
      <LoadingSpinner v-if="minting" block />
      <template v-else-if="enrollToken">
        <div class="code">{{ enrollToken.code }}</div>
        <pre class="install">{{ enrollToken.install_command }}</pre>
        <div class="row-buttons">
          <NeonButton variant="ghost" @click="copy(enrollToken.install_command)"><Copy :size="13" /> Copy command</NeonButton>
          <NeonButton variant="ghost" @click="copy(enrollToken.code)"><Copy :size="13" /> Copy code</NeonButton>
        </div>
        <p class="hint small">
          Installing from the Cove thin client USB instead? Type the code above when the first boot asks for it.
          <template v-if="enrollTarget?.status === 'enrolled'">
            Using the code replaces this device's current install.
          </template>
        </p>
      </template>
    </BaseModal>

    <!-- Settings (policy) -->
    <BaseModal v-model="showSettings" :title="`${settingsTarget?.name ?? ''} — Settings`" width="720px">
      <div v-if="draft" class="settings">
        <div class="tabs" role="tablist">
          <button
            v-for="t in tabs"
            :key="t.key"
            type="button"
            role="tab"
            class="tab"
            :class="{ active: tab === t.key }"
            :aria-selected="tab === t.key"
            @click="tab = t.key"
          ><component :is="t.icon" :size="13" /> {{ t.label }}</button>
        </div>

        <section v-show="tab === 'general'" class="form">
          <label>Name<input v-model="draftName" maxlength="128" /></label>
          <label>Location<input v-model="draftLocation" maxlength="256" /></label>
        </section>

        <section v-show="tab === 'display'" class="form">
          <label>
            Screen off after
            <select v-model.number="draft.screen_off_minutes">
              <option v-for="m in minuteChoices" :key="m" :value="m">{{ m ? fmtMinutes(m) : 'never' }} idle</option>
            </select>
            <small>Any key, click or touch wakes it again.</small>
          </label>
          <div class="row">
            <label>On hours from<input v-model="hoursStart" type="time" /></label>
            <label>to<input v-model="hoursEnd" type="time" /></label>
          </div>
          <small class="note">Outside these hours the screen goes dark after one idle minute. Leave both empty for always.</small>
          <ToggleRow v-model="draft.media_keeps_awake">
            Playing audio keeps it awake
            <template #hint>Films and music count as activity, so the screen and session stay up.</template>
          </ToggleRow>
          <div class="row">
            <label>
              Rotation
              <select v-model="draft.rotation">
                <option value="normal">Landscape</option>
                <option value="90">90°</option>
                <option value="180">180°</option>
                <option value="270">270°</option>
              </select>
            </label>
            <label>
              Scale
              <select v-model="scaleChoice">
                <option value="">Auto</option>
                <option v-for="s in [1, 1.25, 1.5, 1.75, 2]" :key="s" :value="String(s)">{{ s }}×</option>
              </select>
            </label>
          </div>
          <div class="row">
            <label>
              Brightness <span class="val">{{ draft.brightness ?? 'unchanged' }}</span>
              <input v-model.number="brightness" type="range" min="-1" max="100" step="5" />
              <small>Laptop backlight, or the monitor over DDC/CI. Far left leaves it alone.</small>
            </label>
            <label>
              Volume <span class="val">{{ draft.volume === null ? 'unchanged' : `${draft.volume}%` }}</span>
              <input v-model.number="volume" type="range" min="-1" max="150" step="5" />
              <small>Set when you save; the keyboard's volume keys still work. Above 100% amplifies.</small>
            </label>
          </div>
        </section>

        <section v-show="tab === 'session'" class="form">
          <label>
            Sign out after
            <select v-model.number="draft.signout_minutes">
              <option v-for="m in minuteChoices" :key="m" :value="m">{{ m ? fmtMinutes(m) : 'never' }} idle</option>
            </select>
            <small>
              The browser is reset on a wiped profile, which signs whoever was using it out of this screen only.
              Their other browsers stay signed in.
            </small>
          </label>
          <label>
            Longest session
            <select v-model.number="draft.session_max_hours">
              <option :value="1">1 hour</option>
              <option :value="4">4 hours</option>
              <option :value="8">8 hours</option>
              <option :value="12">12 hours</option>
              <option :value="24">1 day</option>
              <option :value="168">1 week</option>
              <option :value="0">Cove default</option>
            </select>
            <small>A hard cap, however busy the screen is. Enforced by Cove.</small>
          </label>
          <label>
            After sign-in
            <select v-model="draft.auto_open">
              <option value="off">Show the dashboard</option>
              <option value="running">Open the workspace if exactly one is running</option>
              <option value="pinned">Open a pinned workspace</option>
            </select>
          </label>
          <label v-if="draft.auto_open === 'pinned'">
            Pinned workspace
            <select v-model="draft.pinned_workspace_id">
              <option :value="null">— choose —</option>
              <option v-for="w in workspaceOptions" :key="w.public_id" :value="w.public_id">
                {{ w.name }} ({{ w.username }})
              </option>
            </select>
            <small>Opens only for its owner; it's started first if it's halted. Handy for a KVM console on the rack.</small>
          </label>
        </section>

        <section v-show="tab === 'browser'" class="form">
          <p class="note">
            The browser can only reach Cove, its sign-in provider and the workspace domain. Add other hosts this
            screen may open directly (for example a KVM's web UI). Prefer opening those inside a Cove workspace: that
            keeps Cove's sign-in and audit in front of them.
          </p>
          <label>
            Extra allowed hosts
            <textarea v-model="hostsText" rows="4" placeholder="kvm.lan&#10;*.docs.example.org" />
            <small>One per line. A bare host, or *.domain for its subdomains. No scheme, port or path.</small>
          </label>
          <label>
            Colour scheme for web pages
            <select v-model="draft.color_scheme">
              <option value="dark">Dark (matches Cove)</option>
              <option value="light">Light</option>
              <option value="system">Browser default (light on a kiosk)</option>
            </select>
            <small>
              What pages such as your sign-in provider see as the preferred scheme. Takes effect when the browser next
              starts: straight away if nobody is signed in, otherwise at the next sign-out.
            </small>
          </label>
        </section>

        <section v-show="tab === 'updates'" class="form">
          <ToggleRow v-model="draft.auto_update">
            Install updates every night
            <template #hint>A full Debian upgrade in the maintenance hour, skipped while someone is signed in.</template>
          </ToggleRow>
          <label>
            Maintenance hour (device time)
            <select v-model.number="draft.maintenance_hour">
              <option v-for="h in 24" :key="h - 1" :value="h - 1">{{ String(h - 1).padStart(2, '0') }}:00</option>
            </select>
          </label>
          <ToggleRow v-model="draft.auto_reboot">
            Reboot when an update needs it
            <template #hint>Only in the maintenance hour, and never while someone is signed in.</template>
          </ToggleRow>
          <p class="note">
            The agent itself updates from Cove automatically whenever this Cove server ships a newer one.
          </p>
        </section>

        <div class="modal-actions">
          <NeonButton variant="ghost" @click="showSettings = false">Cancel</NeonButton>
          <NeonButton variant="primary" :loading="saving" @click="saveSettings">Save</NeonButton>
        </div>
      </div>
    </BaseModal>

    <!-- Screenshot -->
    <BaseModal v-model="showShot" :title="`${shotTarget?.name ?? ''} — Screen`" width="960px">
      <div class="shot-big">
        <img v-if="shotTarget && shots[shotTarget.id]" :src="shots[shotTarget.id]" alt="Screenshot of the thin client" />
        <LoadingSpinner v-else-if="shotTarget && busy[shotTarget.id] === 'screenshot'" block />
        <p v-else class="muted">No screenshot yet.</p>
      </div>
      <div class="row-buttons">
        <NeonButton
          v-if="shotTarget"
          variant="ghost"
          :loading="busy[shotTarget.id] === 'screenshot'"
          @click="takeShot(shotTarget)"
        ><Camera :size="13" /> Take a new one</NeonButton>
        <span v-if="shotTarget?.screenshot_at" class="muted">Taken {{ relativeTime(shotTarget.screenshot_at) }}</span>
      </div>
    </BaseModal>

    <!-- History -->
    <BaseModal v-model="showHistory" :title="`${historyTarget?.name ?? ''} — Activity`" width="820px">
      <LoadingSpinner v-if="historyLoading" block />
      <p v-else-if="!history.length" class="muted">No commands yet.</p>
      <ul v-else class="history">
        <li v-for="h in history" :key="h.id" :class="h.status">
          <button type="button" class="history-row" @click="expanded = expanded === h.id ? null : h.id">
            <span class="h-kind">{{ commandLabel(h.kind) }}</span>
            <span class="h-status">{{ h.status }}</span>
            <span class="muted">{{ h.created_by || 'system' }} · {{ relativeTime(h.created_at) }}</span>
          </button>
          <pre v-if="expanded === h.id && h.result" class="install">{{ h.result }}</pre>
        </li>
      </ul>
    </BaseModal>

    <ConfirmModal
      v-model="showConfirm"
      :title="confirm?.title ?? ''"
      :message="confirm?.message ?? ''"
      :confirm-label="confirm?.label ?? 'OK'"
      :loading="confirmBusy"
      @confirm="runConfirm"
    />
  </AppShell>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch, type Component } from 'vue'
import AppShell from '@/components/AppShell.vue'
import LoadingSpinner from '@/components/LoadingSpinner.vue'
import NeonButton from '@/components/NeonButton.vue'
import BaseModal from '@/components/BaseModal.vue'
import ConfirmModal from '@/components/ConfirmModal.vue'
import RowMenu, { type RowMenuItem } from '@/components/RowMenu.vue'
import ToggleRow from '@/components/ToggleRow.vue'
import {
  Camera, Copy, Download, Eye, History, KeyRound, LogOut, Monitor, MonitorSmartphone, MoonStar, Plus,
  Power, RefreshCw, RotateCcw, ScrollText, Settings2, Shield, SlidersHorizontal, Sun, Timer, Trash2,
  UserRound, Globe,
} from 'lucide-vue-next'
import { thinClientsApi } from '@/api/thinClients'
import { toDataUrl } from '@/utils/dataUrl'
import { useUiStore } from '@/stores/ui'
import { absoluteTime, relativeTime } from '@/utils/workspaceStatus'
import type {
  ThinClient, ThinClientCommand, ThinClientCommandKind, ThinClientEnrollToken, ThinClientPolicy,
  ThinClientWorkspaceOption,
} from '@/types'

const ui = useUiStore()
const clients = ref<ThinClient[]>([])
const loading = ref(true)
// Per-device in-flight action (drives button spinners).
const busy = ref<Record<number, string | undefined>>({})
// Screenshot object URLs, keyed by device id, with the screenshot_at they show.
const shots = ref<Record<number, string>>({})
const shotStamp: Record<number, string> = {}

// ── Loading and polling ──────────────────────────────────────────────────

let timer: ReturnType<typeof setTimeout> | null = null
async function load() {
  clients.value = await thinClientsApi.list()
  for (const c of clients.value) loadShot(c)
}
function poll() {
  timer = setTimeout(async () => {
    if (!document.hidden) {
      try { await load() } catch { /* keep the last view on a blip */ }
    }
    poll()
  }, 5000)
}
onMounted(async () => {
  try { await load() } catch (e: any) { ui.toast(e.message, 'error') } finally { loading.value = false }
  poll()
})
onUnmounted(() => {
  if (timer) clearTimeout(timer)
})

async function loadShot(c: ThinClient) {
  if (!c.screenshot_at || shotStamp[c.id] === c.screenshot_at) return
  shotStamp[c.id] = c.screenshot_at
  try {
    const blob = await thinClientsApi.screenshot(c.id)
    if (!blob) return
    // A data: URL, not an object URL: Cove's CSP has no blob: in img-src.
    shots.value = { ...shots.value, [c.id]: await toDataUrl(blob) }
  } catch {
    delete shotStamp[c.id]
  }
}

function upsert(c: ThinClient) {
  const i = clients.value.findIndex(x => x.id === c.id)
  if (i >= 0) clients.value.splice(i, 1, c)
  else clients.value.push(c)
}

// ── Presentation ─────────────────────────────────────────────────────────

function badge(c: ThinClient) {
  if (c.status !== 'enrolled') return { label: 'pending', cls: 'pending' }
  return c.online ? { label: 'online', cls: 'online' } : { label: 'offline', cls: 'offline' }
}
function cardClass(c: ThinClient) {
  return { offline: c.status === 'enrolled' && !c.online, pending: c.status !== 'enrolled' }
}
function awake(c: ThinClient) {
  return !!c.keep_awake_until && new Date(c.keep_awake_until + (c.keep_awake_until.endsWith('Z') ? '' : 'Z')) > new Date()
}
function fmtIdle(s: number) {
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.floor(s / 60)}m`
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`
}
function fmtMinutes(m: number) {
  return m < 60 ? `${m} min` : m % 60 === 0 ? `${m / 60} h` : `${(m / 60).toFixed(1)} h`
}
function displaySummary(c: ThinClient) {
  const outs = (c.state.outputs ?? []).filter(o => o.active)
  if (!outs.length) return c.state.session_running === false ? 'no session on screen' : '—'
  return outs
    .map(o => [o.make && o.make !== 'Unknown' ? o.model || o.make : o.name, o.mode].filter(Boolean).join(' '))
    .join(', ')
}
const COMMAND_LABELS: Record<ThinClientCommandKind, string> = {
  screen_on: 'Wake screen',
  screen_off: 'Sleep screen',
  restart_browser: 'Restart browser',
  identify: 'Identify',
  screenshot: 'Screenshot',
  apt_upgrade: 'Update system',
  reboot: 'Reboot',
  poweroff: 'Power off',
  logs: 'Logs',
}
function commandLabel(k: ThinClientCommandKind) { return COMMAND_LABELS[k] ?? k }

// ── Commands ─────────────────────────────────────────────────────────────

async function waitFor(c: ThinClient, cmd: ThinClientCommand, timeoutMs = 60000): Promise<ThinClientCommand> {
  const until = Date.now() + timeoutMs
  while (Date.now() < until) {
    await new Promise(r => setTimeout(r, 1500))
    const found = (await thinClientsApi.commands(c.id)).find(x => x.id === cmd.id)
    if (found && !['queued', 'sent'].includes(found.status)) return found
  }
  return { ...cmd, status: 'lost' }
}

async function send(c: ThinClient, kind: ThinClientCommandKind, args: Record<string, unknown> = {}, tag: string = kind) {
  busy.value = { ...busy.value, [c.id]: tag }
  try {
    const cmd = await thinClientsApi.command(c.id, kind, args)
    if (!c.online) {
      ui.toast(`${c.name} is offline — it runs this when it's back`, 'info')
      return cmd
    }
    const done = await waitFor(c, cmd, kind === 'apt_upgrade' ? 30 * 60000 : 60000)
    if (done.status === 'done') ui.toast(`${c.name}: ${commandLabel(kind)} done`, 'success')
    else ui.toast(`${c.name}: ${commandLabel(kind)} ${done.status}${done.result ? ` — ${done.result.slice(-160)}` : ''}`, 'error')
    await load()
    return done
  } catch (e: any) {
    ui.toast(e.message, 'error')
  } finally {
    busy.value = { ...busy.value, [c.id]: undefined }
  }
}

async function signOut(c: ThinClient) {
  busy.value = { ...busy.value, [c.id]: 'signout' }
  try {
    upsert(await thinClientsApi.signOut(c.id))
    ui.toast(`Signed ${c.name} out`, 'success')
  } catch (e: any) { ui.toast(e.message, 'error') }
  finally { busy.value = { ...busy.value, [c.id]: undefined } }
}

async function keepAwake(c: ThinClient, minutes: number) {
  try {
    upsert(await thinClientsApi.keepAwake(c.id, minutes))
    ui.toast(minutes ? `${c.name} stays awake for ${fmtMinutes(minutes)}` : `${c.name} sleeps normally again`, 'success')
  } catch (e: any) { ui.toast(e.message, 'error') }
}

async function showLogs(c: ThinClient, unit: string) {
  const done = await send(c, 'logs', { unit, lines: 300 }, 'logs')
  if (done) openHistory(c, done.id)
}

function menuFor(c: ThinClient): RowMenuItem[] {
  const enrolled = c.status === 'enrolled'
  const items: RowMenuItem[] = []
  if (enrolled) {
    items.push(
      { key: 'shot', label: 'Screenshot', icon: Camera, action: () => openShot(c, true) },
      { key: 'identify', label: 'Identify (banner on screen)', icon: Eye, action: () => send(c, 'identify', { seconds: 15 }) },
      { key: 'restart', label: 'Restart browser (signs out)', icon: RotateCcw, action: () => send(c, 'restart_browser') },
      awake(c)
        ? { key: 'awake', label: 'Stop keeping awake', icon: Timer, action: () => keepAwake(c, 0) }
        : { key: 'awake', label: 'Keep awake for 2 hours', icon: Timer, action: () => keepAwake(c, 120) },
      { key: 'update', label: 'Update system now', icon: Download, separator: true, action: () => send(c, 'apt_upgrade') },
      { key: 'logs', label: 'Agent logs', icon: ScrollText, action: () => showLogs(c, 'agent') },
      { key: 'syslogs', label: 'System warnings', icon: ScrollText, action: () => showLogs(c, 'system') },
      { key: 'history', label: 'Activity', icon: History, action: () => openHistory(c) },
      { key: 'reenroll', label: 'Re-enroll…', icon: KeyRound, separator: true, action: () => openEnroll(c) },
      {
        key: 'reboot', label: 'Reboot', icon: RefreshCw,
        action: () => ask('Reboot', `Reboot '${c.name}'? Whoever is using it is signed out.`, 'Reboot',
          () => send(c, 'reboot')),
      },
      {
        key: 'poweroff', label: 'Power off', icon: Power,
        action: () => ask('Power off', `Power '${c.name}' off? Turning it back on needs someone at the machine (or Wake-on-LAN).`,
          'Power off', () => send(c, 'poweroff')),
      },
    )
  } else {
    items.push({ key: 'enroll', label: 'Enroll…', icon: KeyRound, action: () => openEnroll(c) })
  }
  items.push({
    key: 'delete', label: 'Delete', icon: Trash2, danger: true, separator: true,
    action: () => ask('Delete Thin Client',
      `Delete '${c.name}'? The device stops working until it is enrolled again, and its sessions end.`, 'Delete',
      async () => {
        await thinClientsApi.remove(c.id)
        clients.value = clients.value.filter(x => x.id !== c.id)
        ui.toast(`${c.name} deleted`, 'success')
      }),
  })
  return items
}

// ── Confirm ──────────────────────────────────────────────────────────────

const showConfirm = ref(false)
const confirmBusy = ref(false)
const confirm = ref<{ title: string; message: string; label: string; run: () => Promise<unknown> } | null>(null)
function ask(title: string, message: string, label: string, fn: () => Promise<unknown>) {
  confirm.value = { title, message, label, run: fn }
  showConfirm.value = true
}
async function runConfirm() {
  if (!confirm.value) return
  confirmBusy.value = true
  try {
    const run = confirm.value.run
    showConfirm.value = false
    await run()
  } catch (e: any) { ui.toast(e.message, 'error') }
  finally { confirmBusy.value = false }
}

// ── Create ───────────────────────────────────────────────────────────────

const showCreate = ref(false)
const creating = ref(false)
const form = ref({ name: '', location: '' })
async function handleCreate() {
  if (creating.value) return
  creating.value = true
  try {
    const c = await thinClientsApi.create({ name: form.value.name, location: form.value.location || undefined })
    upsert(c)
    showCreate.value = false
    form.value = { name: '', location: '' }
    openEnroll(c)
  } catch (e: any) { ui.toast(e.message, 'error') }
  finally { creating.value = false }
}

// ── Enrol ────────────────────────────────────────────────────────────────

const showEnroll = ref(false)
const enrollTarget = ref<ThinClient | null>(null)
const enrollToken = ref<ThinClientEnrollToken | null>(null)
const enrollTtl = ref(1)
const minting = ref(false)
function openEnroll(c: ThinClient) {
  enrollTarget.value = c
  enrollToken.value = null
  showEnroll.value = true
  mintCode()
}
async function mintCode() {
  if (!enrollTarget.value) return
  minting.value = true
  try { enrollToken.value = await thinClientsApi.enrollToken(enrollTarget.value.id, enrollTtl.value) }
  catch (e: any) { ui.toast(e.message, 'error') }
  finally { minting.value = false }
}
function copy(text: string) {
  navigator.clipboard?.writeText(text)
  ui.toast('Copied', 'success')
}

// ── Screenshot ───────────────────────────────────────────────────────────

const showShot = ref(false)
const shotTarget = ref<ThinClient | null>(null)
function openShot(c: ThinClient, fresh = false) {
  shotTarget.value = c
  showShot.value = true
  if (fresh || !c.screenshot_at) takeShot(c)
}
async function takeShot(c: ThinClient) {
  const done = await send(c, 'screenshot', {}, 'screenshot')
  if (done?.status === 'done') {
    const fresh = clients.value.find(x => x.id === c.id)
    if (fresh) { shotTarget.value = fresh; await loadShot(fresh) }
  }
}

// ── History ──────────────────────────────────────────────────────────────

const showHistory = ref(false)
const historyTarget = ref<ThinClient | null>(null)
const history = ref<ThinClientCommand[]>([])
const historyLoading = ref(false)
const expanded = ref<number | null>(null)
async function openHistory(c: ThinClient, expand: number | null = null) {
  historyTarget.value = c
  showHistory.value = true
  historyLoading.value = true
  expanded.value = expand
  try { history.value = await thinClientsApi.commands(c.id) }
  catch (e: any) { ui.toast(e.message, 'error') }
  finally { historyLoading.value = false }
}

// ── Settings ─────────────────────────────────────────────────────────────

type Tab = 'general' | 'display' | 'session' | 'browser' | 'updates'
const tabs: { key: Tab; label: string; icon: Component }[] = [
  { key: 'display', label: 'Display', icon: Monitor },
  { key: 'session', label: 'Session', icon: Shield },
  { key: 'browser', label: 'Browser', icon: Globe },
  { key: 'updates', label: 'Updates', icon: Download },
  { key: 'general', label: 'General', icon: Settings2 },
]
const minuteChoices = [0, 1, 2, 5, 10, 15, 30, 45, 60, 90, 120, 180, 240, 480]
const showSettings = ref(false)
const settingsTarget = ref<ThinClient | null>(null)
const tab = ref<Tab>('display')
const draft = ref<ThinClientPolicy | null>(null)
const draftName = ref('')
const draftLocation = ref('')
const hostsText = ref('')
const saving = ref(false)
const workspaceOptions = ref<ThinClientWorkspaceOption[]>([])

function openSettings(c: ThinClient) {
  settingsTarget.value = c
  draft.value = JSON.parse(JSON.stringify(c.policy))
  // Keep any odd value an older Cove saved selectable.
  for (const v of [draft.value!.screen_off_minutes, draft.value!.signout_minutes]) {
    if (!minuteChoices.includes(v)) minuteChoices.push(v)
  }
  minuteChoices.sort((a, b) => a - b)
  draftName.value = c.name
  draftLocation.value = c.location ?? ''
  hostsText.value = c.policy.extra_allowed_hosts.join('\n')
  tab.value = 'display'
  showSettings.value = true
  thinClientsApi.workspaceOptions().then(w => { workspaceOptions.value = w }).catch(() => {})
}
const hoursStart = computed({
  get: () => draft.value?.on_hours_start ?? '',
  set: v => { if (draft.value) draft.value.on_hours_start = v || null },
})
const hoursEnd = computed({
  get: () => draft.value?.on_hours_end ?? '',
  set: v => { if (draft.value) draft.value.on_hours_end = v || null },
})
const scaleChoice = computed({
  get: () => (draft.value?.scale ? String(draft.value.scale) : ''),
  set: v => { if (draft.value) draft.value.scale = v ? Number(v) : null },
})
// Range inputs can't be "unset": -1 stands for "leave it alone".
const brightness = computed({
  get: () => draft.value?.brightness ?? -1,
  set: v => { if (draft.value) draft.value.brightness = v < 0 ? null : v },
})
const volume = computed({
  get: () => draft.value?.volume ?? -1,
  set: v => { if (draft.value) draft.value.volume = v < 0 ? null : v },
})
watch(() => draft.value?.auto_open, v => { if (draft.value && v !== 'pinned') draft.value.pinned_workspace_id = null })

async function saveSettings() {
  if (!settingsTarget.value || !draft.value) return
  if (!!draft.value.on_hours_start !== !!draft.value.on_hours_end) {
    tab.value = 'display'
    ui.toast('Set both ends of the on hours, or neither', 'error')
    return
  }
  saving.value = true
  try {
    const policy = {
      ...draft.value,
      extra_allowed_hosts: hostsText.value.split(/[\s,]+/).map(h => h.trim()).filter(Boolean),
    }
    const c = await thinClientsApi.update(settingsTarget.value.id, {
      name: draftName.value.trim() || undefined,
      location: draftLocation.value,
      policy,
    })
    upsert(c)
    showSettings.value = false
    ui.toast(c.online ? `Saved — ${c.name} applies it now` : `Saved — ${c.name} applies it when it's back online`, 'success')
  } catch (e: any) { ui.toast(e.message, 'error') }
  finally { saving.value = false }
}
</script>

<style scoped>
@import '@/styles/tables.css';
.subtitle { font-family: var(--font-mono); font-size: 12px; color: var(--text-muted); margin-top: 3px; max-width: 70ch; }
.page-header { align-items: flex-start; gap: 16px; }
.empty {
  display: flex; flex-direction: column; align-items: center; gap: 10px; padding: 56px 16px;
  color: var(--text-muted); font-family: var(--font-mono); font-size: 12px; text-align: center;
  border: 1px dashed var(--border); border-radius: var(--radius);
}
.empty svg { color: var(--accent); }

.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(330px, 1fr)); gap: 16px; }

.tc-card {
  display: flex; flex-direction: column; gap: 10px;
  background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);
  padding: 12px; position: relative; min-width: 0;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.tc-card:hover { border-color: var(--accent); box-shadow: var(--glow-sm); }
.tc-card.offline { opacity: 0.75; }
.tc-card.offline:hover { border-color: var(--red); box-shadow: 0 0 6px rgba(255, 32, 85, 0.3); }

.shot {
  position: relative; aspect-ratio: 16 / 9; overflow: hidden; cursor: pointer;
  background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius-sm);
  display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 6px;
  color: var(--text-faint); font-family: var(--font-mono); font-size: 11px;
}
.shot img { width: 100%; height: 100%; object-fit: cover; display: block; }
.shot .scan {
  position: absolute; inset: 0; pointer-events: none;
  background: repeating-linear-gradient(0deg, rgba(0, 0, 0, 0.18) 0 1px, transparent 1px 3px);
}
.shot-tag {
  position: absolute; top: 6px; left: 6px; padding: 1px 6px;
  font-family: var(--font-mono); font-size: 10px; letter-spacing: 1px;
  color: var(--amber); border: 1px solid var(--amber); background: rgba(6, 6, 15, 0.8);
}
.tc-card.pending .shot { cursor: default; }

.tc-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.tc-title {
  font-family: var(--font-display); font-size: 13px; letter-spacing: 2px; color: var(--text);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; text-transform: uppercase;
}
.tc-sub { display: flex; flex-wrap: wrap; gap: 4px 10px; font-family: var(--font-mono); font-size: 11px; color: var(--text-muted); }
.tc-sub span + span::before { content: '·'; margin-right: 10px; color: var(--text-faint); }

.badge {
  font-family: var(--font-mono); font-size: 10px; letter-spacing: 1px; text-transform: uppercase;
  border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 1px 6px; color: var(--text-muted);
  white-space: nowrap;
}
.badge.online { color: var(--green); border-color: var(--green); box-shadow: 0 0 6px rgba(0, 255, 157, 0.25); }
.badge.offline { color: var(--red); border-color: var(--red); }
.badge.pending { color: var(--amber); border-color: var(--amber); }

.facts { margin: 0; display: grid; gap: 3px; font-family: var(--font-mono); font-size: 11.5px; }
.facts > div { display: grid; grid-template-columns: 64px 1fr; gap: 8px; min-width: 0; }
.facts dt { color: var(--text-faint); text-transform: uppercase; letter-spacing: 1px; font-size: 10px; line-height: 18px; }
.facts dd { margin: 0; color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; line-height: 18px; }
.facts dd svg { vertical-align: -2px; color: var(--accent); }
.err-row dd { color: var(--red); }
.muted { color: var(--text-muted); }
.ok { color: var(--green); }
.warn { color: var(--amber); }
.accent2 { color: var(--accent-2); }
.pending-hint { margin: 0; font-family: var(--font-mono); font-size: 12px; color: var(--text-muted); line-height: 1.5; }

.tc-actions { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-top: auto; padding-top: 4px; border-top: 1px solid var(--border); }
.tc-actions > :last-child { margin-left: auto; }

.hint { font-family: var(--font-mono); font-size: 12px; line-height: 1.5; color: var(--text-muted); margin: 0 0 12px; }
.hint.small { font-size: 11px; margin-top: 12px; }
.note { font-family: var(--font-mono); font-size: 11px; line-height: 1.5; color: var(--text-muted); margin: 0; }
.ttl-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; font-family: var(--font-mono); font-size: 12px; color: var(--text-muted); margin-bottom: 12px; }
.code {
  font-family: var(--font-mono); font-size: 26px; letter-spacing: 4px; text-align: center;
  color: var(--accent); text-shadow: var(--glow-sm); padding: 14px 8px; margin-bottom: 12px;
  border: 1px solid var(--accent); border-radius: var(--radius-sm); background: var(--accent-dim);
  word-break: break-all;
}
.install {
  background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius-sm);
  padding: 10px; font-family: var(--font-mono); font-size: 12px; color: var(--text);
  white-space: pre-wrap; word-break: break-all; margin: 0 0 12px; max-height: 50vh; overflow: auto;
}
.row-buttons { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; font-family: var(--font-mono); font-size: 12px; }

.form { display: flex; flex-direction: column; gap: 12px; }
.form label { display: flex; flex-direction: column; gap: 4px; font-family: var(--font-mono); font-size: 12px; color: var(--text-muted); }
.form input:not([type='range']), .form select, .form textarea, .ttl-row select {
  padding: 6px 8px; background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius-sm);
  color: var(--text); font-family: var(--font-mono); font-size: 12px;
}
.form input:focus, .form select:focus, .form textarea:focus { outline: none; border-color: var(--accent); }
.form input[type='range'] { accent-color: var(--accent); }
.form small { color: var(--text-faint); font-size: 11px; line-height: 1.4; }
.form .val { color: var(--accent); margin-left: 6px; }
.row { display: flex; gap: 12px; flex-wrap: wrap; }
.row label { flex: 1; min-width: 160px; }

.tabs { display: flex; gap: 4px; flex-wrap: wrap; border-bottom: 1px solid var(--border); margin-bottom: 14px; }
.tab {
  display: inline-flex; align-items: center; gap: 6px; padding: 7px 12px; cursor: pointer;
  background: none; border: none; border-bottom: 2px solid transparent; margin-bottom: -1px;
  color: var(--text-muted); font-family: var(--font-mono); font-size: 12px; letter-spacing: 1px; text-transform: uppercase;
}
.tab:hover { color: var(--text); }
.tab.active { color: var(--accent); border-bottom-color: var(--accent); text-shadow: var(--glow-sm); }
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 18px; }

.shot-big { min-height: 200px; display: flex; align-items: center; justify-content: center; margin-bottom: 12px; }
.shot-big img { max-width: 100%; max-height: 70vh; border: 1px solid var(--border); }

.history { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
.history-row {
  width: 100%; display: grid; grid-template-columns: 160px 70px 1fr; gap: 10px; align-items: center;
  background: var(--surface-2); border: 1px solid var(--border); border-radius: var(--radius-sm);
  padding: 6px 10px; cursor: pointer; text-align: left; color: var(--text);
  font-family: var(--font-mono); font-size: 12px;
}
.history-row:hover { border-color: var(--accent); }
.h-status { text-transform: uppercase; font-size: 10px; letter-spacing: 1px; color: var(--text-muted); }
.history li.done .h-status { color: var(--green); }
.history li.failed .h-status, .history li.lost .h-status { color: var(--red); }
.history li.queued .h-status, .history li.sent .h-status { color: var(--amber); }
.history pre { margin-top: 4px; }

@media (max-width: 640px) {
  .grid { grid-template-columns: 1fr; }
  .history-row { grid-template-columns: 1fr auto; }
  .history-row .muted { grid-column: 1 / -1; }
}
</style>
