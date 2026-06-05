import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import StatusBadge from '@/components/StatusBadge.vue'
import type { WorkspaceStatus } from '@/types'

const cases: Array<[WorkspaceStatus, string]> = [
  ['creating', 'Starting'],
  ['running', 'Online'],
  ['stopping', 'Halting'],
  ['stopped', 'Offline'],
  ['error', 'Error'],
]

describe('StatusBadge', () => {
  it.each(cases)('renders status %s with label "%s" and the status class', (status, label) => {
    const wrapper = mount(StatusBadge, { props: { status } })
    expect(wrapper.text()).toBe(label)
    expect(wrapper.classes()).toContain('badge')
    expect(wrapper.classes()).toContain(status)
  })

  it('falls back to the raw status string for an unknown status', () => {
    const wrapper = mount(StatusBadge, {
      // @ts-expect-error testing the runtime fallback branch
      props: { status: 'unknown' },
    })
    expect(wrapper.text()).toBe('unknown')
    expect(wrapper.classes()).toContain('unknown')
  })
})
