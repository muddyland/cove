<template>
  <div class="quick">
    <p class="quick-intro">Common actions for working with a workspace node.</p>

    <h4 class="quick-h">On the workspace screen</h4>
    <ul class="quick-list">
      <li>
        <span class="quick-ic"><PanelLeft :size="15" /></span>
        <div><strong>Desktop menu</strong><p>The streamed desktop has its own controls behind the thin vertical bar on the <em>left edge</em> of the stream — click it to slide out the Selkies panel: video quality, clipboard sync, audio, gamepad, and fullscreen.</p></div>
      </li>
      <li>
        <span class="quick-ic"><MousePointer2 :size="15" /></span>
        <div><strong>Give focus back</strong><p>The desktop captures your mouse &amp; keyboard while you work in it. If the buttons in the top bar don't respond, press <kbd>Esc</kbd> (or click the bar) to release focus back to Cove.</p></div>
      </li>
      <li>
        <span class="quick-ic"><Square :size="15" /></span>
        <div><strong>Halt</strong><p>Stop the running container. Your files persist (unless the node is ephemeral) — boot it again anytime.</p></div>
      </li>
      <li>
        <span class="quick-ic"><ScanLine :size="15" /></span>
        <div><strong>CRT effect</strong><p>Toggle the retro scanline overlay on the stream. Purely visual; remembered per browser.</p></div>
      </li>
      <li>
        <span class="quick-ic"><Maximize :size="15" /></span>
        <div><strong>Fullscreen</strong><p>Expand the stream to fill the screen. Press again — or Esc — to exit.</p></div>
      </li>
      <li>
        <span class="quick-ic"><Package :size="15" /></span>
        <div><strong>Apps</strong><p>On a desktop node: see the proot-apps and AppImages installed in it. Update or remove them, install more, and check which proot-apps have a newer build. Work runs in the background — you can close the dialog.</p></div>
      </li>
      <li>
        <span class="quick-ic"><Activity :size="15" /></span>
        <div><strong>Logs</strong><p>Open diagnostics: container logs, plus connection status for VPN / Tailscale nodes.</p></div>
      </li>
      <li>
        <span class="quick-ic"><Download :size="15" /></span>
        <div><strong>Install as app</strong><p>Add this node to your device as its own app (APP) that launches straight into it.</p></div>
      </li>
      <li>
        <span class="quick-ic"><ChevronDown :size="15" /></span>
        <div><strong>Switch nodes</strong><p>Use the name dropdown at the top-left to jump between nodes — and boot a stopped one.</p></div>
      </li>
    </ul>

    <h4 class="quick-h">From the dashboard grid</h4>
    <ul class="quick-list">
      <li>
        <span class="quick-ic"><ListChecks :size="15" /></span>
        <div><strong>Background tasks</strong><p>The checklist icon in the top bar tracks app installs, updates and removals across your running nodes — including the ones a node runs at boot. It spins with a count while work is in flight, and each task keeps its log.</p></div>
      </li>
      <li>
        <span class="quick-ic"><Package :size="15" /></span>
        <div><strong>Apps</strong><p>Same app manager as on the workspace screen, from a running desktop's <em>Actions</em> menu.</p></div>
      </li>
      <li>
        <span class="quick-ic"><Pencil :size="15" /></span>
        <div><strong>Edit</strong><p>Change a node's settings (packages, VPN, DNS…). Stop it first — changes apply on the next boot.</p></div>
      </li>
      <li>
        <span class="quick-ic"><CopyPlus :size="15" /></span>
        <div><strong>Clone</strong><p>Duplicate a stopped node with a copy of its home — handy for trying a different distro.</p></div>
      </li>
      <li>
        <span class="quick-ic"><ArrowRightLeft :size="15" /></span>
        <div><strong>Migrate</strong><p>Move a stopped node's storage to another zone (remote host).</p></div>
      </li>
      <li>
        <span class="quick-ic"><Trash2 :size="15" /></span>
        <div><strong>Purge</strong><p>Destroy the container, optionally deleting its saved home too. Can't be undone.</p></div>
      </li>
    </ul>
  </div>
</template>

<script setup lang="ts">
import {
  Activity, ArrowRightLeft, ChevronDown, CopyPlus, Download, ListChecks, Maximize,
  MousePointer2, Package, PanelLeft, Pencil, ScanLine, Square, Trash2,
} from 'lucide-vue-next'
</script>

<style scoped>
.quick { max-width: 70ch; }
.quick-intro {
  margin: 0 0 18px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-muted);
}
.quick-h {
  margin: 26px 0 12px;
  font-family: var(--font-display);
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 1.4px;
  text-transform: uppercase;
  color: var(--accent);
}
.quick-h:first-of-type { margin-top: 0; }
.quick-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 12px; }
.quick-list li { display: flex; align-items: flex-start; gap: 12px; }
.quick-ic {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
  width: 28px;
  height: 28px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--accent);
  background: var(--surface-2);
}
.quick-list strong { display: block; font-size: 13px; color: var(--text); margin-bottom: 2px; }
.quick-list p { margin: 0; font-size: 12px; line-height: 1.5; color: var(--text-muted); }
.quick-list em { color: var(--accent); font-style: normal; }
.quick-list kbd {
  font-family: var(--font-mono);
  font-size: 0.85em;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 1px 5px;
}
</style>
