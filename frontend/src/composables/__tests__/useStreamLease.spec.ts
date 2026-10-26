import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { defineComponent, h, nextTick, ref } from 'vue'
import { mount } from '@vue/test-utils'

const claimStream = vi.fn()
const checkStream = vi.fn()
const releaseStream = vi.fn()
vi.mock('@/api/workspaces', () => ({
  workspacesApi: {
    claimStream: (...a: unknown[]) => claimStream(...a),
    checkStream: (...a: unknown[]) => checkStream(...a),
    releaseStream: (...a: unknown[]) => releaseStream(...a),
  },
}))

import { useStreamLease, LEASE_CHECK_MS } from '@/composables/useStreamLease'

function setup() {
  const wsId = ref(7)
  const streamUrl = ref<string | null>(null)
  let lease!: ReturnType<typeof useStreamLease>
  const wrapper = mount(defineComponent({
    setup() {
      lease = useStreamLease(wsId, streamUrl)
      return () => h('div')
    },
  }))
  return { wsId, streamUrl, lease: () => lease, wrapper }
}

async function flush() {
  for (let i = 0; i < 5; i++) await Promise.resolve()
  await nextTick()
}

describe('useStreamLease', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    claimStream.mockReset().mockResolvedValue({ holder: true, label: 'Firefox on Linux', since: null })
    checkStream.mockReset().mockResolvedValue({ holder: true, label: 'Firefox on Linux', since: null })
    releaseStream.mockReset().mockResolvedValue(undefined)
  })
  afterEach(() => vi.useRealTimers())

  it('claims when the stream loads and keeps it while it holds it', async () => {
    const { streamUrl, lease } = setup()
    streamUrl.value = '/workspace/abc/'
    await flush()
    expect(claimStream).toHaveBeenCalledWith(7, lease().viewerId)
    await vi.advanceTimersByTimeAsync(LEASE_CHECK_MS * 2)
    expect(checkStream).toHaveBeenCalledTimes(2)
    expect(streamUrl.value).toBe('/workspace/abc/')
    expect(lease().movedTo.value).toBeNull()
  })

  it('drops the dead stream and says where it went when another screen took it', async () => {
    const { streamUrl, lease } = setup()
    streamUrl.value = '/workspace/abc/'
    await flush()
    checkStream.mockResolvedValue({ holder: false, label: 'Desk (thin client)', since: '2026-10-26T21:40:00Z' })
    await vi.advanceTimersByTimeAsync(LEASE_CHECK_MS)
    await flush()
    expect(streamUrl.value).toBeNull()
    expect(lease().movedTo.value?.label).toBe('Desk (thin client)')
    expect(lease().movedSince.value).not.toBe('')
    // It stops checking, and doesn't release a lease that is no longer its own.
    const calls = checkStream.mock.calls.length
    await vi.advanceTimersByTimeAsync(LEASE_CHECK_MS * 3)
    expect(checkStream.mock.calls.length).toBe(calls)
    expect(releaseStream).not.toHaveBeenCalled()
  })

  it('taking it back claims again and clears the message', async () => {
    const { streamUrl, lease } = setup()
    streamUrl.value = '/workspace/abc/'
    await flush()
    checkStream.mockResolvedValueOnce({ holder: false, label: 'Desk (thin client)', since: null })
    await vi.advanceTimersByTimeAsync(LEASE_CHECK_MS)
    await flush()
    lease().dismissMoved()
    streamUrl.value = '/workspace/abc/?again'
    await flush()
    expect(claimStream).toHaveBeenCalledTimes(2)
    expect(lease().movedTo.value).toBeNull()
  })

  it('releases on its own teardown and on unmount', async () => {
    const { streamUrl, wrapper } = setup()
    streamUrl.value = '/workspace/abc/'
    await flush()
    streamUrl.value = null
    await flush()
    expect(releaseStream).toHaveBeenCalledTimes(1)
    streamUrl.value = '/workspace/abc/'
    await flush()
    wrapper.unmount()
    expect(releaseStream).toHaveBeenCalledTimes(2)
  })

  it('a failed check changes nothing', async () => {
    const { streamUrl, lease } = setup()
    streamUrl.value = '/workspace/abc/'
    await flush()
    checkStream.mockRejectedValueOnce(new Error('offline'))
    await vi.advanceTimersByTimeAsync(LEASE_CHECK_MS)
    await flush()
    expect(streamUrl.value).toBe('/workspace/abc/')
    expect(lease().movedTo.value).toBeNull()
  })
})
