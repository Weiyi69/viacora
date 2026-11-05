<script setup lang="ts">
import type { Subject } from '../api/learningTypes'

defineProps<{ subject: Subject }>()

const emit = defineEmits<{
  edit: []
  archive: []
  restore: []
  remove: []
}>()
</script>

<template>
  <article class="resource-card">
    <div class="resource-card-heading">
      <span class="subject-icon" :class="`tone-${subject.color ?? 'blue'}`" aria-hidden="true">
        {{ subject.icon === 'flask' ? 'F' : subject.icon === 'atom' ? 'A' : 'B' }}
      </span>
      <div class="resource-card-copy">
        <RouterLink class="resource-title" :to="`/subjects/${subject.id}`">
          {{ subject.name }}
        </RouterLink>
        <p>{{ subject.description || 'No description yet' }}</p>
      </div>
      <span v-if="subject.archived_at" class="archive-badge">Archived</span>
    </div>
    <div class="resource-card-footer">
      <span>{{ subject.study_space_count }} study spaces</span>
      <div class="card-actions">
        <button
          v-if="subject.archived_at"
          class="small-button"
          type="button"
          :aria-label="`Restore ${subject.name}`"
          @click="emit('restore')"
        >
          Restore
        </button>
        <template v-else>
          <button class="small-button" type="button" :aria-label="`Edit ${subject.name}`" @click="emit('edit')">Edit</button>
          <button class="small-button" type="button" :aria-label="`Archive ${subject.name}`" @click="emit('archive')">Archive</button>
        </template>
        <button class="small-button danger-text" type="button" :aria-label="`Delete ${subject.name}`" @click="emit('remove')">
          Delete
        </button>
      </div>
    </div>
  </article>
</template>
