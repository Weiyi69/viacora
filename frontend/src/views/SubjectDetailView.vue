<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { ApiError } from '../api/client'
import { recordAccess } from '../api/activity'
import type { ResourceInput, StudySpace } from '../api/learningTypes'
import {
  archiveStudySpace,
  createStudySpace,
  deleteStudySpace,
  getStudySpaces,
  restoreStudySpace,
  updateStudySpace,
} from '../api/studySpaces'
import { getSubject } from '../api/subjects'
import AppShell from '../components/AppShell.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import EmptyState from '../components/EmptyState.vue'
import LoadingSkeleton from '../components/LoadingSkeleton.vue'
import ModalDialog from '../components/ModalDialog.vue'
import ResourceForm from '../components/ResourceForm.vue'
import StudySpaceCard from '../components/StudySpaceCard.vue'

const route = useRoute()
const queryClient = useQueryClient()
const subjectId = computed(() => String(route.params.subjectId))
const search = ref('')
const includeArchived = ref(false)
const sortBy = ref<'recent' | 'name'>('recent')
const page = ref(1)
const pageSize = 20
const showForm = ref(false)
const editing = ref<StudySpace | null>(null)
const pendingDelete = ref<StudySpace | null>(null)
const actionError = ref('')
const actionStatus = ref('')
const subjectQuery = useQuery({
  queryKey: computed(() => ['subject', subjectId.value]),
  queryFn: () => getSubject(subjectId.value),
})
const studySpacesQuery = useQuery({
  queryKey: computed(() => [
    'study-spaces',
    subjectId.value,
    { q: search.value, sortBy: sortBy.value, includeArchived: includeArchived.value, page: page.value },
  ]),
  queryFn: () =>
    getStudySpaces(subjectId.value, {
      q: search.value,
      sortBy: sortBy.value,
      includeArchived: includeArchived.value,
      page: page.value,
      pageSize,
    }),
})
watch([search, includeArchived, sortBy], () => {
  page.value = 1
})

const accessMutation = useMutation({
  mutationFn: (id: string) => recordAccess('subject', id),
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
  () => subjectQuery.data.value?.id,
  (id) => {
    if (id) accessMutation.mutate(id)
  },
  { immediate: true },
)

async function refreshResources(): Promise<void> {
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: ['study-spaces', subjectId.value] }),
    queryClient.invalidateQueries({ queryKey: ['subject', subjectId.value] }),
    queryClient.invalidateQueries({ queryKey: ['subjects'] }),
    queryClient.invalidateQueries({ queryKey: ['dashboard'] }),
  ])
}

const saveMutation = useMutation({
  mutationFn: (input: { id: string | null; resource: ResourceInput }) =>
    input.id
      ? updateStudySpace(input.id, input.resource)
      : createStudySpace(subjectId.value, input.resource),
  onSuccess: async () => {
    showForm.value = false
    editing.value = null
    actionError.value = ''
    actionStatus.value = 'Study space saved.'
    await refreshResources()
  },
  onError: (error: unknown) => {
    actionError.value =
      error instanceof ApiError ? error.message : 'Unable to save the study space.'
  },
})
const archiveMutation = useMutation({
  mutationFn: archiveStudySpace,
  onSuccess: refreshResources,
  onError: (error: unknown) => {
    actionError.value =
      error instanceof ApiError ? error.message : 'Unable to archive the study space.'
  },
})
const restoreMutation = useMutation({
  mutationFn: restoreStudySpace,
  onSuccess: refreshResources,
  onError: (error: unknown) => {
    actionError.value =
      error instanceof ApiError ? error.message : 'Unable to restore the study space.'
  },
})
const deleteMutation = useMutation({
  mutationFn: deleteStudySpace,
  onSuccess: async () => {
    pendingDelete.value = null
    actionError.value = ''
    actionStatus.value = 'Study space deleted.'
    await refreshResources()
  },
  onError: (error: unknown) => {
    actionError.value =
      error instanceof ApiError ? error.message : 'Unable to delete the study space.'
  },
})

function openCreate(): void {
  editing.value = null
  showForm.value = true
  actionError.value = ''
}

function openEdit(studySpace: StudySpace): void {
  editing.value = studySpace
  showForm.value = true
  actionError.value = ''
}

function saveStudySpace(input: ResourceInput): void {
  void saveMutation.mutateAsync({ id: editing.value?.id ?? null, resource: input })
}
</script>

<template>
  <AppShell>
    <nav class="breadcrumbs" aria-label="Breadcrumb">
      <RouterLink to="/subjects">Subjects</RouterLink>
      <span aria-hidden="true">/</span>
      <span>{{ subjectQuery.data.value?.name ?? 'Subject' }}</span>
    </nav>
    <section class="page-heading">
      <div>
        <p class="eyebrow">Subject</p>
        <h1>{{ subjectQuery.data.value?.name ?? 'Study spaces' }}</h1>
        <p class="page-description">
          {{ subjectQuery.data.value?.description || 'Organize your learning into focused study spaces.' }}
        </p>
      </div>
      <button
        v-if="subjectQuery.isSuccess.value"
        class="primary-button"
        type="button"
        @click="openCreate"
      >
        New study space
      </button>
    </section>

    <p v-if="actionError" class="form-error" role="alert">{{ actionError }}</p>
    <p v-if="actionStatus" class="form-success" role="status">{{ actionStatus }}</p>

    <section class="filter-bar" aria-label="Study space filters">
      <label class="search-control">
        <span class="sr-only">Search study spaces</span>
        <input v-model="search" type="search" placeholder="Search study spaces" maxlength="120" />
      </label>
      <label class="toggle-control">
        <input v-model="includeArchived" type="checkbox" />
        Include archived
      </label>
      <label class="sort-control">
        Sort
        <select v-model="sortBy" aria-label="Sort study spaces">
          <option value="recent">Recently updated</option>
          <option value="name">Name A–Z</option>
        </select>
      </label>
    </section>

    <LoadingSkeleton v-if="subjectQuery.isPending.value || studySpacesQuery.isPending.value" />
    <section v-else-if="subjectQuery.isError.value" class="surface-panel">
      <EmptyState
        title="Subject unavailable"
        description="This subject may have been removed or you may not have access to it."
      >
        <RouterLink class="secondary-button" to="/subjects">Back to subjects</RouterLink>
      </EmptyState>
    </section>
    <section v-else-if="studySpacesQuery.isError.value" class="surface-panel">
      <EmptyState
        title="Study spaces could not load"
        description="Check your connection and try again."
      >
        <button class="secondary-button" type="button" @click="studySpacesQuery.refetch()">
          Try again
        </button>
      </EmptyState>
      <nav
        v-if="(studySpacesQuery.data.value?.total ?? 0) > pageSize"
        class="pagination"
        aria-label="Study space pages"
      >
        <button class="secondary-button" type="button" :disabled="page === 1" @click="page--">
          Previous
        </button>
        <span>Page {{ page }} of {{ Math.ceil((studySpacesQuery.data.value?.total ?? 0) / pageSize) }}</span>
        <button
          class="secondary-button"
          type="button"
          :disabled="page * pageSize >= (studySpacesQuery.data.value?.total ?? 0)"
          @click="page++"
        >
          Next
        </button>
      </nav>
    </section>
    <section v-else-if="studySpacesQuery.data.value?.items.length" class="subject-grid">
      <StudySpaceCard
        v-for="studySpace in studySpacesQuery.data.value.items"
        :key="studySpace.id"
        :study-space="studySpace"
        :subject-id="subjectId"
        @edit="openEdit(studySpace)"
        @archive="archiveMutation.mutate(studySpace.id)"
        @restore="restoreMutation.mutate(studySpace.id)"
        @remove="pendingDelete = studySpace"
      />
    </section>
    <EmptyState
      v-else
      :title="search ? 'No matching study spaces' : includeArchived ? 'No archived study spaces' : 'No study spaces yet'"
      :description="search ? 'Try another search term.' : 'Create a study space to start organizing topics.'"
    >
      <button
        v-if="!search && !includeArchived && subjectQuery.data.value"
        class="primary-button"
        type="button"
        @click="openCreate"
      >
        Create a study space
      </button>
    </EmptyState>

    <ModalDialog
      v-if="showForm"
      labelled-by="space-form-title"
      @close="showForm = false"
    >
      <h2 id="space-form-title">{{ editing ? 'Edit study space' : 'Create a study space' }}</h2>
      <ResourceForm
        :key="editing?.id ?? 'new-space'"
        resource-type="study-space"
        :initial-name="editing?.name"
        :initial-description="editing?.description"
        :busy="saveMutation.isPending.value"
        @save="saveStudySpace"
        @cancel="showForm = false"
      />
    </ModalDialog>

    <ConfirmDialog
      :model-value="pendingDelete !== null"
      title="Delete this study space?"
      :message="`Permanently delete ${pendingDelete?.name ?? 'this study space'}, its topics, and related activity? This cannot be undone.`"
      :busy="deleteMutation.isPending.value"
      @close="pendingDelete = null"
      @confirm="pendingDelete && deleteMutation.mutate(pendingDelete.id)"
    />
  </AppShell>
</template>
