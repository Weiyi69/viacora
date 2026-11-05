<script setup lang="ts">
import type { StudySpace } from '../api/learningTypes'

defineProps<{ studySpace: StudySpace; subjectId: string }>()

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
      <span class="subject-icon tone-violet" aria-hidden="true">▱</span>
      <div class="resource-card-copy">
        <RouterLink
          class="resource-title"
          :to="`/subjects/${subjectId}/study-spaces/${studySpace.id}`"
        >
          {{ studySpace.name }}
        </RouterLink>
        <p>{{ studySpace.description || 'No description yet' }}</p>
      </div>
      <span v-if="studySpace.archived_at" class="archive-badge">Archived</span>
    </div>
    <div class="resource-card-footer">
      <span>{{ studySpace.topic_count }} topics</span>
      <div class="card-actions">
        <button
          v-if="studySpace.archived_at"
          class="small-button"
          type="button"
          :aria-label="`Restore ${studySpace.name}`"
          @click="emit('restore')"
        >
          Restore
        </button>
        <template v-else>
          <button class="small-button" type="button" :aria-label="`Edit ${studySpace.name}`" @click="emit('edit')">Edit</button>
          <button class="small-button" type="button" :aria-label="`Archive ${studySpace.name}`" @click="emit('archive')">Archive</button>
        </template>
        <button class="small-button danger-text" type="button" :aria-label="`Delete ${studySpace.name}`" @click="emit('remove')">
          Delete
        </button>
      </div>
    </div>
  </article>
</template>
