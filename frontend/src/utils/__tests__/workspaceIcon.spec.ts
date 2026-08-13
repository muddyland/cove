import { describe, it, expect } from 'vitest'
import { workspaceIconUrl } from '@/utils/workspaceIcon'

describe('workspaceIconUrl', () => {
  it('uses the site favicon when the server has one', () => {
    const url = workspaceIconUrl({ id: 7, favicon_at: '2026-08-13T10:00:00Z', image_logo: '/logo.png' })
    expect(url).toContain('/api/workspaces/7/favicon.png')
  })

  it('falls back to the image logo when there is no favicon', () => {
    // A browser opening several sites, a desktop node, or a site whose icon
    // couldn't be decoded — all land here, showing what the workspace runs.
    expect(workspaceIconUrl({ id: 7, favicon_at: null, image_logo: '/logo.png' })).toBe('/logo.png')
  })

  it('returns null when there is neither', () => {
    expect(workspaceIconUrl({ id: 7, favicon_at: null, image_logo: null })).toBeNull()
  })

  it('keys the URL on favicon_at so a re-pointed workspace refreshes its icon', () => {
    const before = workspaceIconUrl({ id: 7, favicon_at: '2026-08-13T10:00:00Z', image_logo: null })
    const after = workspaceIconUrl({ id: 7, favicon_at: '2026-08-13T11:30:00Z', image_logo: null })
    expect(before).not.toBe(after)
  })
})
