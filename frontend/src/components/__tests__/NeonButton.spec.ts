import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import NeonButton from '@/components/NeonButton.vue'

describe('NeonButton', () => {
  it('renders slot content', () => {
    const wrapper = mount(NeonButton, { slots: { default: 'Launch' } })
    expect(wrapper.text()).toBe('Launch')
    expect(wrapper.find('button').exists()).toBe(true)
  })

  it('applies the variant class', () => {
    const wrapper = mount(NeonButton, { props: { variant: 'danger' }, slots: { default: 'X' } })
    expect(wrapper.classes()).toContain('danger')
  })

  it('is disabled and shows a spinner when loading', () => {
    const wrapper = mount(NeonButton, { props: { loading: true }, slots: { default: 'Go' } })
    const button = wrapper.find('button')
    expect(button.attributes('disabled')).toBeDefined()
    expect(wrapper.classes()).toContain('loading')
    expect(wrapper.find('.spinner').exists()).toBe(true)
  })

  it('is disabled when the disabled prop is true', () => {
    const wrapper = mount(NeonButton, { props: { disabled: true }, slots: { default: 'Go' } })
    expect(wrapper.find('button').attributes('disabled')).toBeDefined()
  })

  it('has no spinner when not loading', () => {
    const wrapper = mount(NeonButton, { slots: { default: 'Go' } })
    expect(wrapper.find('.spinner').exists()).toBe(false)
  })

  it('emits click when enabled', async () => {
    const wrapper = mount(NeonButton, { slots: { default: 'Go' } })
    await wrapper.find('button').trigger('click')
    expect(wrapper.emitted('click')).toHaveLength(1)
  })

  it('does not emit click when disabled', async () => {
    const wrapper = mount(NeonButton, { props: { disabled: true }, slots: { default: 'Go' } })
    await wrapper.find('button').trigger('click')
    expect(wrapper.emitted('click')).toBeUndefined()
  })

  it('does not emit click when loading', async () => {
    const wrapper = mount(NeonButton, { props: { loading: true }, slots: { default: 'Go' } })
    await wrapper.find('button').trigger('click')
    expect(wrapper.emitted('click')).toBeUndefined()
  })
})
