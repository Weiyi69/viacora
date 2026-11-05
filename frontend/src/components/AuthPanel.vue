<script setup lang="ts">
defineProps<{
  title: string
  description: string
}>()
</script>

<template>
  <main class="auth-shell">
    <RouterLink class="brand auth-brand" to="/">
      <span class="brand-mark">V</span>
      <span>viacora</span>
    </RouterLink>
    <section class="auth-panel">
      <h1>{{ title }}</h1>
      <p class="auth-description">{{ description }}</p>
      <slot />
    </section>
  </main>
</template>
