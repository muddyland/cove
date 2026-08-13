import type { Workspace } from '@/types'

/** The bits of a workspace that decide which icon it gets. */
export type IconSource = Pick<Workspace, 'id' | 'favicon_at' | 'image_logo'>

/**
 * The icon that stands for a workspace.
 *
 * A browser workspace opening a single site shows that site's own favicon —
 * fetched and cached server-side by server/favicons.py, because a node that only
 * ever opens github.com reads as "GitHub", not as "Chromium". Everything else,
 * including a browser opening several sites at once, shows the image's project
 * logo, which is what the workspace actually runs.
 *
 * `favicon_at` is both the server's "there is one" flag and its cache key, so a
 * workspace re-pointed at a different site picks up the new mark on the next
 * poll instead of holding the old one until a hard reload.
 *
 * Returns null when there's nothing to show; callers hide the <img> rather than
 * render a broken one.
 */
export function workspaceIconUrl(ws: IconSource): string | null {
  if (ws.favicon_at) {
    return `/api/workspaces/${ws.id}/favicon.png?v=${encodeURIComponent(ws.favicon_at)}`
  }
  return ws.image_logo
}
