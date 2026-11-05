import { mount, flushPromises } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import { h } from 'vue'
import ModalDialog from './ModalDialog.vue'

describe('ModalDialog', () => {
  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('moves focus into the dialog, traps tab navigation, closes on Escape, and restores focus', async () => {
    const trigger = document.createElement('button')
    document.body.append(trigger)
    trigger.focus()

    const wrapper = mount(ModalDialog, {
      attachTo: document.body,
      props: { labelledBy: 'dialog-title' },
      slots: {
        default: () =>
          h('div', [
            h('h2', { id: 'dialog-title' }, 'Dialog'),
            h('button', { id: 'first' }, 'First'),
            h('button', { id: 'last' }, 'Last'),
          ]),
      },
    })
    await flushPromises()

    const first = wrapper.get('#first').element as HTMLButtonElement
    const last = wrapper.get('#last').element as HTMLButtonElement
    expect(document.activeElement).toBe(first)

    last.focus()
    last.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true }))
    expect(document.activeElement).toBe(first)
    first.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true }))
    expect(document.activeElement).toBe(last)

    last.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    expect(wrapper.emitted('close')).toHaveLength(1)

    wrapper.unmount()
    expect(document.activeElement).toBe(trigger)
  })
})
