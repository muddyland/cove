// Client-side validators mirroring the backend's rules, so bad input is caught
// inline before a launch round-trip. Each returns an error string ('' when ok).

function isIp(v: string): boolean {
  if (/^\d{1,3}(\.\d{1,3}){3}$/.test(v)) return v.split('.').every(o => Number(o) <= 255)
  return /^[0-9a-fA-F:]+$/.test(v) && v.includes(':') // loose IPv6
}

export function dnsError(customDns: boolean, dnsServers: string): string {
  if (!customDns) return ''
  const bad = dnsServers.split(/[,\s]+/).filter(Boolean).filter(t => !isIp(t))
  return bad.length ? `Not a valid IP: ${bad.join(', ')}` : ''
}

// Mirrors the backend _PKG_TOKEN_RE.
const PKG_RE = /^[A-Za-z0-9][A-Za-z0-9._+:-]*$/
export function packagesError(value: string): string {
  const bad = value.split(/[,\s]+/).filter(Boolean).filter(t => !PKG_RE.test(t))
  return bad.length ? `Invalid package name: ${bad.join(', ')}` : ''
}

export function appImagesError(value: string): string {
  const bad = value.split(/\s+/).filter(Boolean).filter(u => !/^https?:\/\/.+/i.test(u))
  return bad.length ? `AppImage entries must be http(s) URLs: ${bad.join(', ')}` : ''
}
