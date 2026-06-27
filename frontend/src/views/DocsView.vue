<template>
  <AppShell>
    <div class="docs-layout">
      <aside class="docs-sidebar">
        <div class="docs-sidebar-head"><BookOpen :size="15" /> DOCS</div>
        <nav class="docs-nav">
          <RouterLink
            v-for="d in entries"
            :key="d.slug"
            :to="`/app/docs/${d.slug}`"
            class="docs-nav-item"
            :class="{ active: d.slug === currentSlug }"
          >{{ d.title }}</RouterLink>
        </nav>
      </aside>

      <article class="docs-content">
        <div v-if="loading" class="docs-state">Loading…</div>
        <div v-else-if="error" class="docs-state error">⚠ {{ error }}</div>
        <div v-else class="markdown-body" @click="onContentClick" v-html="rendered"></div>
      </article>
    </div>
  </AppShell>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue'
import { useRoute, useRouter, RouterLink } from 'vue-router'
import { marked } from 'marked'
import { BookOpen } from 'lucide-vue-next'
import AppShell from '@/components/AppShell.vue'
import { docsApi, type DocEntry } from '@/api/docs'

const route = useRoute()
const router = useRouter()

const entries = ref<DocEntry[]>([])
const rendered = ref('')
const loading = ref(true)
const error = ref('')

const currentSlug = computed(() => (route.params.slug as string) || entries.value[0]?.slug || 'README')

async function loadDoc(slug: string) {
  loading.value = true
  error.value = ''
  try {
    const doc = await docsApi.get(slug)
    rendered.value = await marked.parse(doc.content)
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : 'Failed to load document'
    rendered.value = ''
  } finally {
    loading.value = false
  }
}

// Cross-links between docs (e.g. [zones](zones.md)) route in-app instead of
// hitting a dead URL; external links behave normally.
function onContentClick(e: MouseEvent) {
  const a = (e.target as HTMLElement).closest('a')
  if (!a) return
  const m = (a.getAttribute('href') || '').match(/^\.?\/?([A-Za-z0-9_-]+)\.md(#.*)?$/)
  if (m) {
    e.preventDefault()
    router.push(`/app/docs/${m[1]}`)
  }
}

onMounted(async () => {
  try {
    entries.value = await docsApi.list()
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : 'Failed to load docs'
    loading.value = false
    return
  }
  // Land on the first doc with a shareable URL when none is specified.
  if (!route.params.slug && entries.value.length) {
    router.replace(`/app/docs/${entries.value[0].slug}`)
    return
  }
  loadDoc(currentSlug.value)
})

watch(
  () => route.params.slug,
  (slug) => {
    if (slug) loadDoc(slug as string)
  },
)
</script>

<style scoped>
.docs-layout {
  display: grid;
  grid-template-columns: 220px 1fr;
  gap: 24px;
  align-items: start;
}

/* ── Sidebar ─────────────────────────────────────────────────────────────── */
.docs-sidebar {
  position: sticky;
  top: 0;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  padding: 12px;
}
.docs-sidebar-head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 2px;
  color: var(--accent);
  text-shadow: var(--glow-sm);
  padding: 4px 8px 12px;
}
.docs-nav { display: flex; flex-direction: column; gap: 2px; }
.docs-nav-item {
  display: block;
  padding: 8px 10px;
  color: var(--text-muted);
  text-decoration: none;
  font-size: 13px;
  border-radius: var(--radius-sm);
  border-left: 2px solid transparent;
  transition: all 0.15s;
}
.docs-nav-item:hover { color: var(--text); background: var(--accent-dim); }
.docs-nav-item.active {
  color: var(--accent);
  background: var(--accent-dim);
  border-left-color: var(--accent);
}

/* ── Content ─────────────────────────────────────────────────────────────── */
.docs-content {
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  padding: 28px 34px;
  min-width: 0;
  min-height: 60vh;
}
.docs-state { color: var(--text-muted); font-family: var(--font-mono); }
.docs-state.error { color: var(--red); }

/* Rendered markdown. */
.markdown-body { color: var(--text); line-height: 1.7; font-size: 14.5px; }
.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3),
.markdown-body :deep(h4) {
  color: var(--accent);
  font-family: var(--font-display);
  letter-spacing: 0.5px;
  margin: 1.6em 0 0.6em;
  line-height: 1.3;
}
.markdown-body :deep(h1) { font-size: 1.8em; margin-top: 0; text-shadow: var(--glow-sm); }
.markdown-body :deep(h2) {
  font-size: 1.4em;
  border-bottom: 1px solid var(--border);
  padding-bottom: 0.3em;
}
.markdown-body :deep(h3) { font-size: 1.15em; color: var(--accent-2); }
.markdown-body :deep(h4) { font-size: 1em; color: var(--accent-2); }
.markdown-body :deep(p) { margin: 0.8em 0; }
.markdown-body :deep(a) { color: var(--accent-2); text-decoration: none; border-bottom: 1px solid transparent; }
.markdown-body :deep(a:hover) { border-bottom-color: var(--accent-2); }
.markdown-body :deep(ul),
.markdown-body :deep(ol) { margin: 0.8em 0; padding-left: 1.6em; }
.markdown-body :deep(li) { margin: 0.3em 0; }
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
  border-radius: var(--radius-sm);
  padding: 14px 16px;
  overflow-x: auto;
  margin: 1em 0;
}
.markdown-body :deep(pre code) {
  background: none;
  border: none;
  padding: 0;
  font-size: 0.85em;
  color: var(--text);
}
.markdown-body :deep(blockquote) {
  margin: 1em 0;
  padding: 0.4em 1em;
  border-left: 3px solid var(--accent);
  background: var(--accent-dim);
  color: var(--text-muted);
}
.markdown-body :deep(table) {
  border-collapse: collapse;
  margin: 1em 0;
  width: 100%;
  font-size: 0.92em;
}
.markdown-body :deep(th),
.markdown-body :deep(td) {
  border: 1px solid var(--border);
  padding: 7px 11px;
  text-align: left;
}
.markdown-body :deep(th) { background: var(--surface-2); color: var(--accent-2); font-family: var(--font-mono); }
.markdown-body :deep(hr) { border: none; border-top: 1px solid var(--border); margin: 1.6em 0; }
.markdown-body :deep(img) { max-width: 100%; border-radius: var(--radius-sm); }

@media (max-width: 760px) {
  .docs-layout { grid-template-columns: 1fr; }
  .docs-sidebar { position: static; }
  .docs-content { padding: 20px; }
}
</style>
