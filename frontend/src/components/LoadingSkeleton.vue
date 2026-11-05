<script setup lang="ts">
withDefaults(defineProps<{ rows?: number }>(), { rows: 3 })
</script>

<template>
  <div class="skeleton-list" role="status" aria-label="Loading content">
    <span v-for="row in rows" :key="row" class="skeleton-row" />
  </div>
</template>
