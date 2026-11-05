<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { ApiError } from '../api/client'
import { recordAccess } from '../api/activity'
import type { ResourceInput, Topic } from '../api/learningTypes'
import { getSubject } from '../api/subjects'
import { getStudySpace } from '../api/studySpaces'
import { createTopic, deleteTopic, getTopics, updateTopic } from '../api/topics'
import AppShell from '../components/AppShell.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import DocumentList from '../components/DocumentList.vue'
import EmptyState from '../components/EmptyState.vue'
import LoadingSkeleton from '../components/LoadingSkeleton.vue'
import ModalDialog from '../components/ModalDialog.vue'
import ResourceForm from '../components/ResourceForm.vue'

const route = useRoute()
const queryClient = useQueryClient()
const studySpaceId = computed(() => String(route.params.studySpaceId))
const search = ref('')
const page = ref(1)
const pageSize = 50
const showForm = ref(false)
const editing = ref<Topic | null>(null)
const pendingDelete = ref<Topic | null>(null)
const actionError = ref('')
const actionStatus = ref('')
const studySpaceQuery = useQuery({
  queryKey: computed(() => ['study-space', studySpaceId.value]),
  queryFn: () => getStudySpace(studySpaceId.value),
})
const subjectId = computed(() => studySpaceQuery.data.value?.subject_id ?? '')
const subjectQuery = useQuery({
  queryKey: computed(() => ['subject', subjectId.value]),
  queryFn: () => getSubject(subjectId.value),
  enabled: computed(() => Boolean(subjectId.value)),
})
const topicsQuery = useQuery({
  queryKey: computed(() => ['topics', studySpaceId.value, search.value, page.value]),
  queryFn: () => getTopics(studySpaceId.value, search.value, page.value, pageSize),
})
watch(search, () => {
  page.value = 1
})

const accessMutation = useMutation({
  mutationFn: (id: string) => recordAccess('study_space', id),
  onSuccess: async () => {
    await queryClient.invalidateQueries({ queryKey: ['dashboard'] })
    await queryClient.invalidateQueries({ queryKey: ['activity'] })
  },
  onError: (error: unknown) => {
    actionError.value =
      error instanceof ApiError ? error.message : 'Unable to record this visit.'
  },
})
watch(
  () => studySpaceQuery.data.value?.id,
  (id) => {
    if (id) accessMutation.mutate(id)
  },
  { immediate: true },
)

async function refreshResources(): Promise<void> {
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: ['topics', studySpaceId.value] }),
    queryClient.invalidateQueries({ queryKey: ['study-space', studySpaceId.value] }),
    queryClient.invalidateQueries({ queryKey: ['study-spaces'] }),
    queryClient.invalidateQueries({ queryKey: ['dashboard'] }),
    queryClient.invalidateQueries({ queryKey: ['activity'] }),
  ])
}

const saveMutation = useMutation({
  mutationFn: (input: { id: string | null; resource: ResourceInput }) =>
    input.id
      ? updateTopic(input.id, input.resource)
      : createTopic(studySpaceId.value, input.resource),
  onSuccess: async () => {
    showForm.value = false
    editing.value = null
    actionError.value = ''
    actionStatus.value = 'Topic saved.'
    await refreshResources()
  },
  onError: (error: unknown) => {
    actionError.value = error instanceof ApiError ? error.message : 'Unable to save the topic.'
  },
})
const reorderMutation = useMutation({
  mutationFn: (input: { topicId: string; position: number }) =>
    updateTopic(input.topicId, { position: input.position }),
  onSuccess: refreshResources,
  onError: (error: unknown) => {
    actionError.value = error instanceof ApiError ? error.message : 'Unable to reorder the topic.'
  },
})
const deleteMutation = useMutation({
  mutationFn: deleteTopic,
  onSuccess: async () => {
    pendingDelete.value = null
    actionError.value = ''
    actionStatus.value = 'Topic deleted.'
    await refreshResources()
  },
  onError: (error: unknown) => {
    actionError.value = error instanceof ApiError ? error.message : 'Unable to delete the topic.'
  },
})

function openCreate(): void {
  editing.value = null
  showForm.value = true
  actionError.value = ''
}

function openEdit(topic: Topic): void {
  editing.value = topic
  showForm.value = true
  actionError.value = ''
}

function saveTopic(input: ResourceInput): void {
  void saveMutation.mutateAsync({ id: editing.value?.id ?? null, resource: input })
}

function moveTopic(topic: Topic, offset: number): void {
  void reorderMutation.mutateAsync({
    topicId: topic.id,
    position: Math.max(0, topic.position + offset),
  })
}
</script>

<template>
  <AppShell>
    <nav class="breadcrumbs" aria-label="Breadcrumb">
      <RouterLink to="/subjects">Subjects</RouterLink>
      <span aria-hidden="true">/</span>
      <RouterLink v-if="subjectId" :to="`/subjects/${subjectId}`">
        {{ subjectQuery.data.value?.name ?? 'Subject' }}
      </RouterLink>
      <span aria-hidden="true">/</span>
      <span>{{ studySpaceQuery.data.value?.name ?? 'Study space' }}</span>
    </nav>
    <section class="page-heading">
      <div>
        <p class="eyebrow">Study space</p>
        <h1>{{ studySpaceQuery.data.value?.name ?? 'Topics' }}</h1>
        <p class="page-description">
          {{ studySpaceQuery.data.value?.description || 'Arrange this study space into ordered topics.' }}
        </p>
      </div>
      <button
        v-if="studySpaceQuery.isSuccess.value"
        class="primary-button"
        type="button"
        @click="openCreate"
      >
        New topic
      </button>
    </section>

    <p v-if="actionError" class="form-error" role="alert">{{ actionError }}</p>
    <p v-if="actionStatus" class="form-success" role="status">{{ actionStatus }}</p>

    <section class="filter-bar">
      <label class="search-control">
        <span class="sr-only">Search topics</span>
        <input v-model="search" type="search" placeholder="Search topics" maxlength="120" />
      </label>
    </section>

    <LoadingSkeleton v-if="studySpaceQuery.isPending.value || topicsQuery.isPending.value" />
    <section v-else-if="studySpaceQuery.isError.value" class="surface-panel">
      <EmptyState
        title="Study space unavailable"
        description="This study space may have been archived, removed, or you may not have access to it."
      >
        <RouterLink class="secondary-button" to="/subjects">Back to subjects</RouterLink>
      </EmptyState>
    </section>
    <section v-else-if="topicsQuery.isError.value" class="surface-panel">
      <EmptyState title="Topics could not load" description="Check your connection and try again.">
        <button class="secondary-button" type="button" @click="topicsQuery.refetch()">
          Try again
        </button>
      </EmptyState>
      <nav
        v-if="(topicsQuery.data.value?.total ?? 0) > pageSize"
        class="pagination"
        aria-label="Topic pages"
      >
        <button class="secondary-button" type="button" :disabled="page === 1" @click="page--">
          Previous
        </button>
        <span>Page {{ page }} of {{ Math.ceil((topicsQuery.data.value?.total ?? 0) / pageSize) }}</span>
        <button
          class="secondary-button"
          type="button"
          :disabled="page * pageSize >= (topicsQuery.data.value?.total ?? 0)"
          @click="page++"
        >
          Next
        </button>
      </nav>
    </section>
    <section v-else-if="topicsQuery.data.value?.items.length" class="topic-list">
      <article
        v-for="topic in topicsQuery.data.value.items"
        :key="topic.id"
        class="topic-row"
      >
        <span class="topic-position">{{ topic.position + 1 }}</span>
        <div class="resource-card-copy">
          <h2>{{ topic.name }}</h2>
          <p>{{ topic.description || 'No description yet' }}</p>
        </div>
        <div class="card-actions">
          <button
            class="small-button"
            type="button"
            :aria-label="`Move ${topic.name} up`"
            :disabled="topic.position === 0 || reorderMutation.isPending.value"
            @click="moveTopic(topic, -1)"
          >
            Up
          </button>
          <button
            class="small-button"
            type="button"
            :aria-label="`Move ${topic.name} down`"
            :disabled="topic.position >= (topicsQuery.data.value.total - 1) || reorderMutation.isPending.value"
            @click="moveTopic(topic, 1)"
          >
            Down
          </button>
          <button class="small-button" type="button" @click="openEdit(topic)">Edit</button>
          <button class="small-button danger-text" type="button" @click="pendingDelete = topic">
            Delete
          </button>
        </div>
      </article>
    </section>
    <EmptyState
      v-else
      :title="search ? 'No matching topics' : 'No topics yet'"
      :description="search ? 'Try another search term.' : 'Add topics to create an ordered outline for this study space.'"
    >
      <button v-if="!search" class="primary-button" type="button" @click="openCreate">
        Create a topic
      </button>
    </EmptyState>

    <DocumentList
      v-if="studySpaceQuery.data.value"
      :subject-id="subjectId"
      :study-space-id="studySpaceId"
    />

    <ModalDialog
      v-if="showForm"
      labelled-by="topic-form-title"
      @close="showForm = false"
    >
      <h2 id="topic-form-title">{{ editing ? 'Edit topic' : 'Create a topic' }}</h2>
      <ResourceForm
        :key="editing?.id ?? 'new-topic'"
        resource-type="topic"
        :initial-name="editing?.name"
        :initial-description="editing?.description"
        :initial-position="editing?.position ?? topicsQuery.data.value?.total ?? 0"
        :busy="saveMutation.isPending.value"
        @save="saveTopic"
        @cancel="showForm = false"
      />
    </ModalDialog>

    <ConfirmDialog
      :model-value="pendingDelete !== null"
      title="Delete this topic?"
      :message="`Permanently delete ${pendingDelete?.name ?? 'this topic'} and its related activity? This cannot be undone.`"
      :busy="deleteMutation.isPending.value"
      @close="pendingDelete = null"
      @confirm="pendingDelete && deleteMutation.mutate(pendingDelete.id)"
    />
  </AppShell>
</template>
