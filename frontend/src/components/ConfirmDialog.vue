<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    title: string
    message: string
    confirmLabel?: string
    busy?: boolean
  }>(),
  { confirmLabel: 'Delete permanently', busy: false },
)

const emit = defineEmits<{
  close: []
  confirm: []
}>()

const dialog = ref<HTMLElement | null>(null)
let previousFocus: HTMLElement | null = null

watch(
  () => props.modelValue,
  async (open) => {
    if (open) {
      previousFocus =
        document.activeElement instanceof HTMLElement ? document.activeElement : null
      await nextTick()
      dialog.value?.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus()
    } else {
      previousFocus?.focus()
      previousFocus = null
    }
  },
  { flush: 'post' },
)

function handleKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') {
    event.preventDefault()
    emit('close')
    return
  }
  if (event.key !== 'Tab' || !dialog.value) return

  const buttons = dialog.value.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')
  const first = buttons.item(0)
  const last = buttons.item(buttons.length - 1)
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first.focus()
  }
}
</script>

<template>
  <div
    v-if="modelValue"
    class="modal-backdrop"
    @click.self="emit('close')"
  >
    <section
      ref="dialog"
      class="confirm-dialog"
      role="alertdialog"
      tabindex="-1"
      aria-modal="true"
      aria-labelledby="confirm-title"
      aria-describedby="confirm-message"
      @keydown="handleKeydown"
    >
      <h2 id="confirm-title">{{ title }}</h2>
      <p id="confirm-message">{{ message }}</p>
      <div class="dialog-actions">
        <button class="secondary-button" type="button" :disabled="busy" @click="emit('close')">
          Cancel
        </button>
        <button class="danger-button" type="button" :disabled="busy" @click="emit('confirm')">
          {{ busy ? 'Please wait…' : confirmLabel }}
        </button>
      </div>
    </section>
  </div>
</template>
