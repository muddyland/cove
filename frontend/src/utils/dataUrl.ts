/**
 * Images fetched through the API are shown as `data:` URLs, NOT `blob:`
 * object URLs (workspace previews, thin client screenshots).
 *
 * Cove's CSP is `img-src 'self' data: https:` (the cove-headers middleware in
 * docker-compose.yml). It does not list `blob:`, so an <img> pointing at an
 * object URL is blocked outright — the fetch succeeds, the URL is created, and
 * the browser silently refuses to paint it. Encoding inline keeps previews
 * working on every existing deployment without loosening the policy or
 * requiring a Traefik redeploy. Base64 costs ~33% over the raw bytes, which on
 * a ~3KB thumbnail is nothing.
 */
export function toDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result as string)
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(blob)
  })
}
