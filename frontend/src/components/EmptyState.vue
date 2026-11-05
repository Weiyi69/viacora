<script setup lang="ts">
withDefaults(
  defineProps<{
    title: string
    description: string
    compact?: boolean
  }>(),
  { compact: false },
)
</script>

<template>
  <section class="empty-state" :class="{ compact }">
    <div class="empty-state-mark" aria-hidden="true">+</div>
    <h2>{{ title }}</h2>
    <p>{{ description }}</p>
    <slot />
  </section>
</template>
