<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'

const props = withDefaults(
  defineProps<{
    labelledBy: string
    panelClass?: string
  }>(),
  { panelClass: 'form-dialog' },
)

const emit = defineEmits<{
  close: []
}>()

const panel = ref<HTMLElement | null>(null)
const previousFocus =
  typeof document !== 'undefined' && document.activeElement instanceof HTMLElement
    ? document.activeElement
    : null

onMounted(async () => {
  await nextTick()
  const initialFocus =
    panel.value?.querySelector<HTMLElement>('[autofocus], button:not(:disabled), input, select, textarea')
  ;(initialFocus ?? panel.value)?.focus()
})

onBeforeUnmount(() => previousFocus?.focus())

function handleKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') {
    event.preventDefault()
    emit('close')
    return
  }
  if (event.key !== 'Tab' || !panel.value) return

  const focusable = panel.value.querySelectorAll<HTMLElement>(
    'button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href], [tabindex]:not([tabindex="-1"])',
  )
  const first = focusable.item(0)
  const last = focusable.item(focusable.length - 1)
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
  <div class="modal-backdrop" @click.self="emit('close')">
    <section
      ref="panel"
      :class="props.panelClass"
      role="dialog"
      tabindex="-1"
      aria-modal="true"
      :aria-labelledby="props.labelledBy"
      @keydown="handleKeydown"
    >
      <slot />
    </section>
  </div>
</template>
