<script setup lang="ts">
defineProps<{
  title: string
  description: string
}>()
</script>

<template>
  <main class="auth-shell">
    <RouterLink class="brand auth-brand" to="/" aria-label="Viacora home">
      <img class="brand-logo" :src="'/images/logo.png'" alt="Viacora" />
    </RouterLink>
    <section class="auth-panel">
      <h1>{{ title }}</h1>
      <p class="auth-description">{{ description }}</p>
      <slot />
    </section>
  </main>
</template>
