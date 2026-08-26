<template>
  <BaseModal v-model="open" title="Documentation" width="min(1180px, 95vw)" flush>
    <div class="docs">
      <aside class="rail" :class="{ 'rail-open': railOpen }">
        <div class="search">
          <Search :size="14" class="search-icon" />
          <input
            v-model="query"
            class="search-input"
            type="search"
            placeholder="Search documentation…"
            aria-label="Search documentation"
          />
        </div>

        <nav class="rail-nav" aria-label="Documentation">
          <div v-for="group in groups" :key="group.key" class="group">
            <div class="group-head">
              <component :is="group.icon" :size="12" />
              {{ group.label }}
            </div>
            <button
              v-for="d in group.items"
              :key="d.slug"
              type="button"
              class="rail-item"
              :class="{ active: d.slug === slug }"
              @click="go(d.slug)"
            >
              {{ d.title }}
            </button>
          </div>
          <p v-if="entries.length && !matches.length" class="rail-empty">
            No documents match “{{ query }}”.
          </p>
        </nav>
      </aside>

      <article class="pane">
        <header class="pane-head">
          <button
            type="button"
            class="rail-toggle"
            aria-label="Toggle document list"
            @click="railOpen = !railOpen"
          >
            <PanelLeft :size="15" />
          </button>
          <button
            v-if="trail.length"
            type="button"
            class="back-btn"
            :title="`Back to ${trailTopTitle}`"
            @click="back"
          >
            <ArrowLeft :size="14" /> Back
          </button>
          <h2 class="pane-title">{{ current?.title || 'Documentation' }}</h2>
          <span v-if="current?.scope === 'admin'" class="scope-tag">
            <Shield :size="11" /> ADMIN
          </span>
        </header>

        <div ref="scroller" class="pane-body">
          <div v-if="loading" class="skeleton" aria-busy="true">
            <span v-for="n in 7" :key="n" class="skeleton-line" />
          </div>
          <p v-else-if="error" class="state error">
            <TriangleAlert :size="15" /> {{ error }}
          </p>
          <QuickHelp v-else-if="slug === QUICK" />
          <!-- Docs are first-party Markdown shipped inside the image, rendered
               by marked; they are not user-authored content. -->
          <div v-else class="markdown-body" v-html="rendered" @click="onContentClick"></div>
        </div>
      </article>
    </div>
  </BaseModal>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { marked } from 'marked'
import { ArrowLeft, BookOpen, PanelLeft, Search, Shield, TriangleAlert } from 'lucide-vue-next'
import BaseModal from '@/components/BaseModal.vue'
import QuickHelp from '@/components/QuickHelp.vue'
import { docsApi, type DocEntry } from '@/api/docs'

/** Synthetic entry: the workspace quick-reference, rendered as a component
 *  rather than Markdown so it keeps its icons. Listed alongside the real docs
 *  so search, grouping and the active state all work unchanged. */
const QUICK = '__quick'
const QUICK_ENTRY: DocEntry = { slug: QUICK, title: 'Quick help', scope: 'user' }

const open = defineModel<boolean>({ required: true })
// Which entry to land on when the modal opens — the workspace screen's ? button
// points straight at the quick reference.
const props = withDefaults(defineProps<{ initial?: string }>(), { initial: '' })

const entries = ref<DocEntry[]>([])
const slug = ref('')
const rendered = ref('')
const loading = ref(false)
const error = ref('')
const query = ref('')
const railOpen = ref(false)
const trail = ref<string[]>([])
const scroller = ref<HTMLElement | null>(null)

const current = computed(() => entries.value.find(d => d.slug === slug.value))
const trailTopTitle = computed(() => {
  const prev = trail.value[trail.value.length - 1]
  return entries.value.find(d => d.slug === prev)?.title ?? 'previous'
})

const matches = computed(() => {
  const q = query.value.trim().toLowerCase()
  if (!q) return entries.value
  return entries.value.filter(d => d.title.toLowerCase().includes(q) || d.slug.includes(q))
})

// Admin-scoped docs are only ever present for admins — the API filters them out
// for everyone else, so the second group simply doesn't render.
const groups = computed(() =>
  [
    { key: 'user', label: 'USING COVE', icon: BookOpen, items: matches.value.filter(d => d.scope === 'user') },
    { key: 'admin', label: 'OPERATING COVE', icon: Shield, items: matches.value.filter(d => d.scope === 'admin') },
  ].filter(g => g.items.length),
)

/** GitHub-style heading slug, so in-doc links like `#gpu-acceleration` resolve.
 *  Each whitespace character becomes its own hyphen — dropped punctuation leaves
 *  a gap behind, which is why `## Sudo & container hardening` is
 *  `#sudo--container-hardening` and not `#sudo-container-hardening`. */
function slugify(text: string): string {
  return text
    .trim()
    .toLowerCase()
    .replace(/[^\w\s-]/g, '')
    .replace(/\s/g, '-')
}

/** marked doesn't emit heading ids, and external links should leave the modal. */
function postProcess(html: string): string {
  return html
    .replace(/<h([1-6])>([\s\S]*?)<\/h\1>/g, (_m, lvl: string, inner: string) => {
      const id = slugify(inner.replace(/<[^>]*>/g, ''))
      return `<h${lvl} id="${id}">${inner}</h${lvl}>`
    })
    .replace(/<a href="(https?:)\/\//g, '<a target="_blank" rel="noopener noreferrer" href="$1//')
}

async function load(next: string, anchor = '') {
  if (next === QUICK) {
    rendered.value = ''
    error.value = ''
    slug.value = QUICK
    await nextTick()
    scrollTo('')
    return
  }
  loading.value = true
  error.value = ''
  try {
    const doc = await docsApi.get(next)
    rendered.value = postProcess(await marked.parse(doc.content))
    slug.value = next
    // Keep the listing's scope/title authoritative even if it wasn't listed.
    if (!entries.value.some(d => d.slug === doc.slug)) {
      entries.value = [...entries.value, { slug: doc.slug, title: doc.title, scope: doc.scope }]
    }
    await nextTick()
    scrollTo(anchor)
  } catch (e: unknown) {
    rendered.value = ''
    // The index is user-scoped but cross-links to operator pages, so a non-admin
    // can legitimately click through to a 403. Say why rather than leaking the
    // raw error.
    error.value =
      (e as { status?: number })?.status === 403
        ? 'That page is part of the operator documentation and needs an admin account.'
        : e instanceof Error
          ? e.message
          : 'Failed to load document'
  } finally {
    loading.value = false
  }
}

function scrollTo(anchor: string) {
  if (!scroller.value) return
  if (!anchor) {
    scroller.value.scrollTop = 0
    return
  }
  const target = scroller.value.querySelector<HTMLElement>(`#${CSS.escape(anchor)}`)
  if (target) scroller.value.scrollTop = target.offsetTop - 12
  else scroller.value.scrollTop = 0
}

/** Sidebar / cross-link navigation, recording a breadcrumb for the Back button. */
function go(next: string, anchor = '') {
  if (next === slug.value) {
    scrollTo(anchor)
    return
  }
  if (slug.value) trail.value = [...trail.value, slug.value]
  railOpen.value = false
  load(next, anchor)
}

function back() {
  const prev = trail.value[trail.value.length - 1]
  if (!prev) return
  trail.value = trail.value.slice(0, -1)
  load(prev)
}

// Cross-links between docs (`[zones](zones.md#storage)`) and in-page anchors
// resolve inside the modal; anything else is left to the browser.
function onContentClick(e: MouseEvent) {
  const a = (e.target as HTMLElement).closest('a')
  if (!a) return
  const href = a.getAttribute('href') || ''
  const cross = href.match(/^\.?\/?([A-Za-z0-9_-]+)\.md(?:#(.*))?$/)
  if (cross) {
    e.preventDefault()
    go(cross[1], cross[2] || '')
    return
  }
  if (href.startsWith('#')) {
    e.preventDefault()
    scrollTo(href.slice(1))
  }
}

// Fetch the index the first time the modal is opened, then reuse it. Each open
// resets the breadcrumb and lands on `initial` (or the first entry).
watch(open, async (isOpen) => {
  if (!isOpen) return
  query.value = ''
  railOpen.value = false
  trail.value = []

  if (!entries.value.length) {
    loading.value = true
    try {
      entries.value = [QUICK_ENTRY, ...(await docsApi.list())]
    } catch (e: unknown) {
      error.value = e instanceof Error ? e.message : 'Failed to load documentation'
      return
    } finally {
      loading.value = false
    }
  }

  const target = props.initial || entries.value[0]?.slug
  if (target) await load(target)
  else error.value = 'No documentation is available.'
})
</script>

<style scoped>
.docs {
  display: grid;
  grid-template-columns: 240px 1fr;
  height: min(78vh, 780px);
  width: 100%;
  min-height: 0;
}

/* ── Sidebar ─────────────────────────────────────────────────────────────── */
.rail {
  display: flex;
  flex-direction: column;
  min-height: 0;
  border-right: 1px solid var(--border);
  background: var(--bg);
}
.search {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 12px 14px;
  border-bottom: 1px solid var(--border);
  color: var(--text-muted);
}
.search-icon { flex: none; }
.search-input {
  flex: 1;
  min-width: 0;
  background: none;
  border: none;
  outline: none;
  color: var(--text);
  font-family: var(--font-mono);
  font-size: 12.5px;
}
.search-input::placeholder { color: var(--text-muted); }
.search-input::-webkit-search-cancel-button { filter: grayscale(1) opacity(0.6); }

.rail-nav { overflow-y: auto; padding: 10px 8px 16px; min-height: 0; }
.group + .group { margin-top: 14px; }
.group-head {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 1.6px;
  color: var(--accent);
  opacity: 0.75;
}
.rail-item {
  display: block;
  width: 100%;
  text-align: left;
  padding: 7px 10px;
  background: none;
  border: none;
  border-left: 2px solid transparent;
  border-radius: var(--radius-sm);
  color: var(--text-muted);
  font-size: 13px;
  cursor: pointer;
  transition: color 0.15s, background 0.15s, border-color 0.15s;
}
.rail-item:hover { color: var(--text); background: var(--accent-dim); }
.rail-item.active {
  color: var(--accent);
  background: var(--accent-dim);
  border-left-color: var(--accent);
}
.rail-empty { padding: 10px; color: var(--text-muted); font-size: 12.5px; }

/* ── Content pane ────────────────────────────────────────────────────────── */
.pane { display: flex; flex-direction: column; min-width: 0; min-height: 0; }
.pane-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 11px 20px;
  border-bottom: 1px solid var(--border);
  background: var(--surface-2);
}
.pane-title {
  margin: 0;
  font-family: var(--font-display);
  font-size: 14px;
  font-weight: 600;
  letter-spacing: 0.6px;
  color: var(--text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.rail-toggle,
.back-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  flex: none;
  padding: 4px 8px;
  background: none;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-muted);
  font-family: var(--font-mono);
  font-size: 11px;
  cursor: pointer;
  transition: color 0.15s, border-color 0.15s;
}
.rail-toggle { display: none; }
.back-btn:hover,
.rail-toggle:hover { color: var(--accent); border-color: var(--accent); }
.scope-tag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex: none;
  margin-left: auto;
  padding: 2px 7px;
  border: 1px solid var(--amber);
  border-radius: 999px;
  color: var(--amber);
  font-family: var(--font-mono);
  font-size: 9.5px;
  letter-spacing: 1.2px;
}
.pane-body { overflow-y: auto; padding: 26px 34px 44px; min-height: 0; }

.state { display: flex; align-items: center; gap: 8px; font-family: var(--font-mono); font-size: 13px; }
.state.error { color: var(--red); }

.skeleton { display: flex; flex-direction: column; gap: 13px; }
.skeleton-line {
  height: 11px;
  border-radius: 4px;
  background: linear-gradient(90deg, var(--surface-2), var(--border), var(--surface-2));
  background-size: 200% 100%;
  animation: shimmer 1.3s ease-in-out infinite;
}
.skeleton-line:nth-child(1) { width: 45%; height: 18px; }
.skeleton-line:nth-child(4) { width: 78%; }
.skeleton-line:nth-child(7) { width: 60%; }
@keyframes shimmer {
  from { background-position: 200% 0; }
  to { background-position: -200% 0; }
}
@media (prefers-reduced-motion: reduce) {
  .skeleton-line { animation: none; }
}

/* ── Rendered markdown ───────────────────────────────────────────────────── */
.markdown-body { color: var(--text); line-height: 1.75; font-size: 14.5px; max-width: 78ch; }
.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3),
.markdown-body :deep(h4) {
  color: var(--accent);
  font-family: var(--font-display);
  letter-spacing: 0.5px;
  margin: 1.7em 0 0.6em;
  line-height: 1.3;
  scroll-margin-top: 12px;
}
.markdown-body :deep(h1) { font-size: 1.7em; margin-top: 0; text-shadow: var(--glow-sm); }
.markdown-body :deep(h2) {
  font-size: 1.32em;
  border-bottom: 1px solid var(--border);
  padding-bottom: 0.3em;
}
.markdown-body :deep(h3) { font-size: 1.12em; color: var(--accent-2); }
.markdown-body :deep(h4) { font-size: 1em; color: var(--accent-2); }
.markdown-body :deep(p) { margin: 0.85em 0; }
.markdown-body :deep(a) { color: var(--accent-2); text-decoration: none; border-bottom: 1px solid transparent; }
.markdown-body :deep(a:hover) { border-bottom-color: var(--accent-2); }
.markdown-body :deep(ul),
.markdown-body :deep(ol) { margin: 0.85em 0; padding-left: 1.6em; }
.markdown-body :deep(li) { margin: 0.35em 0; }
.markdown-body :deep(li::marker) { color: var(--accent); }
.markdown-body :deep(code) {
  font-family: var(--font-mono);
  font-size: 0.88em;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 1px 5px;
}
.markdown-body :deep(pre) {
  background: var(--bg);
  border: 1px solid var(--border);
  border-left: 2px solid var(--accent);
  border-radius: var(--radius-sm);
  padding: 14px 16px;
  overflow-x: auto;
  margin: 1.1em 0;
}
.markdown-body :deep(pre code) { background: none; border: none; padding: 0; font-size: 0.85em; color: var(--text); }
.markdown-body :deep(blockquote) {
  margin: 1.1em 0;
  padding: 0.5em 1em;
  border-left: 3px solid var(--accent);
  background: var(--accent-dim);
  color: var(--text-muted);
}
.markdown-body :deep(blockquote p) { margin: 0.3em 0; }
/* Tables carry most of the reference material, so let them scroll rather than
   squeeze the prose column. */
.markdown-body :deep(table) {
  border-collapse: collapse;
  margin: 1.1em 0;
  width: 100%;
  font-size: 0.9em;
  display: block;
  overflow-x: auto;
}
.markdown-body :deep(th),
.markdown-body :deep(td) { border: 1px solid var(--border); padding: 7px 11px; text-align: left; vertical-align: top; }
.markdown-body :deep(th) { background: var(--surface-2); color: var(--accent-2); font-family: var(--font-mono); white-space: nowrap; }
.markdown-body :deep(tbody tr:nth-child(even)) { background: rgba(255, 255, 255, 0.02); }
.markdown-body :deep(hr) { border: none; border-top: 1px solid var(--border); margin: 1.8em 0; }
.markdown-body :deep(img) { max-width: 100%; border-radius: var(--radius-sm); }

/* ── Narrow screens: the rail becomes a drawer over the content ──────────── */
@media (max-width: 780px) {
  .docs { grid-template-columns: 1fr; height: min(84vh, 780px); }
  .rail {
    position: absolute;
    z-index: 2;
    top: 49px;
    bottom: 0;
    left: 0;
    width: 250px;
    border-right: 1px solid var(--accent);
    box-shadow: var(--shadow);
    transform: translateX(-102%);
    transition: transform 0.18s ease;
  }
  .rail.rail-open { transform: none; }
  .rail-toggle { display: inline-flex; }
  .pane-body { padding: 20px; }
  .markdown-body { font-size: 14px; }
}
</style>
