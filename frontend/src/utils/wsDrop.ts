import type { Workspace } from '@/types'

/** Where a file dropped onto the stream lands: the desktop for a desktop,
 *  Downloads for a browser or app (they have no desktop to show it on). */
export function dropFolderFor(ws: Pick<Workspace, 'workspace_type'>): string {
  return ws.workspace_type === 'desktop' ? 'Desktop' : 'Downloads'
}

/**
 * The files in a drop, leaving out folders. A dropped folder shows up in
 * `dataTransfer.files` as a zero-byte entry that can't be read, so it would
 * become an empty file; `webkitGetAsEntry` tells them apart where the browser
 * has it (all current ones), with a size/type guess as the fallback.
 */
export function filesFromDrop(dt: DataTransfer | null | undefined): { files: File[]; folders: number } {
  if (!dt) return { files: [], folders: 0 }
  const files: File[] = []
  let folders = 0
  const items = dt.items ? [...dt.items].filter(i => i.kind === 'file') : []
  if (items.length && typeof items[0].webkitGetAsEntry === 'function') {
    for (const item of items) {
      const entry = item.webkitGetAsEntry()
      if (entry?.isDirectory) { folders++; continue }
      const f = item.getAsFile()
      if (f) files.push(f)
    }
    return { files, folders }
  }
  for (const f of [...dt.files]) {
    if (f.size === 0 && f.type === '' && !f.name.includes('.')) folders++
    else files.push(f)
  }
  return { files, folders }
}

export const isFileDrag = (e: DragEvent) => [...(e.dataTransfer?.types ?? [])].includes('Files')
