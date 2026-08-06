import { describe, it, expect } from 'vitest'
import { soiOffset, streamSocketUrl } from '@/composables/useLocalPreview'

/** Build a stripe message: 0x03 marker, 3-byte frame id, 2-byte y, then JPEG. */
function stripe(frameId: number, y: number, body = [0x00, 0x11]): Uint8Array {
  return new Uint8Array([
    0x03,
    (frameId >> 16) & 0xff, (frameId >> 8) & 0xff, frameId & 0xff,
    (y >> 8) & 0xff, y & 0xff,
    0xff, 0xd8, 0xff, ...body,
  ])
}

describe('soiOffset', () => {
  it('finds the JPEG start after the 6-byte header', () => {
    expect(soiOffset(stripe(15, 624))).toBe(6)
  })

  it('yields the y-offset from the two bytes ahead of it', () => {
    const bytes = stripe(15, 624)
    const off = soiOffset(bytes)
    expect((bytes[off - 2] << 8) | bytes[off - 1]).toBe(624)
    const wide = stripe(17, 704)
    const off2 = soiOffset(wide)
    expect((wide[off2 - 2] << 8) | wide[off2 - 1]).toBe(704)
  })

  it('rejects a message with no SOI', () => {
    expect(soiOffset(new Uint8Array([0x03, 0, 0, 1, 0, 0, 0, 0, 0, 0]))).toBe(-1)
  })

  it('ignores an SOI-looking sequence beyond the header region', () => {
    // A marker this deep is payload data, not a header — treating it as one
    // would read two arbitrary bytes as the stripe's y-offset.
    const bytes = new Uint8Array(40)
    bytes.set([0xff, 0xd8, 0xff], 25)
    expect(soiOffset(bytes)).toBe(-1)
  })

  it('rejects a truncated message', () => {
    expect(soiOffset(new Uint8Array([0x03, 0x00, 0xff, 0xd8]))).toBe(-1)
  })
})

describe('streamSocketUrl', () => {
  it('builds a subdomain-mode url from the workspace origin', () => {
    // "//host/?__cove_t=..." is what stream-auth returns in subdomain mode.
    expect(streamSocketUrl('//abc123.cove.example/?__cove_t=tok')).toBe(
      'ws://abc123.cove.example/websockets',
    )
  })

  it('builds a subpath-mode url on our own origin', () => {
    expect(streamSocketUrl('/workspace/abc123/')).toBe(
      `ws://${location.host}/workspace/abc123/websockets`,
    )
  })

  it('tolerates a subpath url with no trailing slash', () => {
    expect(streamSocketUrl('/workspace/abc123')).toBe(
      `ws://${location.host}/workspace/abc123/websockets`,
    )
  })
})
