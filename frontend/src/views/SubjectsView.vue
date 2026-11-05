<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { ApiError } from '../api/client'
import type { ResourceInput, Subject } from '../api/learningTypes'
import {
  archiveSubject,
  createSubject,
  deleteSubject,
  getSubjects,
  restoreSubject,
  updateSubject,
} from '../api/subjects'
import AppShell from '../components/AppShell.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import EmptyState from '../components/EmptyState.vue'
import LoadingSkeleton from '../components/LoadingSkeleton.vue'
import ModalDialog from '../components/ModalDialog.vue'
import ResourceForm from '../components/ResourceForm.vue'
import SubjectCard from '../components/SubjectCard.vue'

const queryClient = useQueryClient()
const search = ref('')
const includeArchived = ref(false)
const page = ref(1)
const pageSize = 20
const showForm = ref(false)
const editing = ref<Subject | null>(null)
const pendingDelete = ref<Subject | null>(null)
const actionError = ref('')
const actionStatus = ref('')
const queryKey = computed(() => [
  'subjects',
  { q: search.value, includeArchived: includeArchived.value, page: page.value },
])
const subjectsQuery = useQuery({
  queryKey,
  queryFn: () =>
    getSubjects({
      q: search.value,
      includeArchived: includeArchived.value,
      page: page.value,
      pageSize,
    }),
})
watch([search, includeArchived], () => {
  page.value = 1
})

async function refreshResources(): Promise<void> {
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: ['subjects'] }),
    queryClient.invalidateQueries({ queryKey: ['dashboard'] }),
  ])
}

const saveMutation = useMutation({
  mutationFn: (input: { id: string | null; resource: ResourceInput }) =>
    input.id
      ? updateSubject(input.id, input.resource)
      : createSubject(input.resource),
  onSuccess: async () => {
    showForm.value = false
    editing.value = null
    actionError.value = ''
    actionStatus.value = 'Subject saved.'
    await refreshResources()
  },
  onError: (error: unknown) => {
    actionError.value = error instanceof ApiError ? error.message : 'Unable to save the subject.'
  },
})
const archiveMutation = useMutation({
  mutationFn: archiveSubject,
  onSuccess: refreshResources,
  onError: (error: unknown) => {
    actionError.value = error instanceof ApiError ? error.message : 'Unable to archive the subject.'
  },
})
const restoreMutation = useMutation({
  mutationFn: restoreSubject,
  onSuccess: refreshResources,
  onError: (error: unknown) => {
    actionError.value = error instanceof ApiError ? error.message : 'Unable to restore the subject.'
  },
})
const deleteMutation = useMutation({
  mutationFn: deleteSubject,
  onSuccess: async () => {
    pendingDelete.value = null
    actionError.value = ''
    actionStatus.value = 'Subject deleted.'
    await refreshResources()
  },
  onError: (error: unknown) => {
    actionError.value = error instanceof ApiError ? error.message : 'Unable to delete the subject.'
  },
})

function openCreate(): void {
  actionError.value = ''
  editing.value = null
  showForm.value = true
}

function openEdit(subject: Subject): void {
  actionError.value = ''
  editing.value = subject
  showForm.value = true
}

function saveSubject(input: ResourceInput): void {
  void saveMutation.mutateAsync({ id: editing.value?.id ?? null, resource: input })
}
</script>

<template>
  <AppShell>
    <section class="page-heading">
      <div>
        <p class="eyebrow">Your library</p>
        <h1>Subjects</h1>
        <p class="page-description">Group your study spaces around the things you are learning.</p>
      </div>
      <button class="primary-button" type="button" @click="openCreate">New subject</button>
    </section>

    <p v-if="actionError" class="form-error" role="alert">{{ actionError }}</p>
    <p v-if="actionStatus" class="form-success" role="status">{{ actionStatus }}</p>

    <section class="filter-bar" aria-label="Subject filters">
      <label class="search-control">
        <span class="sr-only">Search subjects</span>
        <input v-model="search" type="search" placeholder="Search subjects" maxlength="120" />
      </label>
      <label class="toggle-control">
        <input v-model="includeArchived" type="checkbox" />
        Include archived
      </label>
    </section>

    <LoadingSkeleton v-if="subjectsQuery.isPending.value" :rows="3" />
    <section v-else-if="subjectsQuery.isError.value" class="surface-panel">
      <EmptyState
        title="Subjects could not load"
        description="Check your connection and try loading your subjects again."
      >
        <button class="secondary-button" type="button" @click="subjectsQuery.refetch()">
          Try again
        </button>
      </EmptyState>
    </section>
    <section v-else-if="subjectsQuery.data.value?.items.length" class="subject-grid">
      <SubjectCard
        v-for="subject in subjectsQuery.data.value.items"
        :key="subject.id"
        :subject="subject"
        @edit="openEdit(subject)"
        @archive="archiveMutation.mutate(subject.id)"
        @restore="restoreMutation.mutate(subject.id)"
        @remove="pendingDelete = subject"
      />
    </section>
    <EmptyState
      v-else
      :title="search ? 'No matching subjects' : includeArchived ? 'No archived subjects' : 'Your library is ready'"
      :description="search ? 'Try another search term.' : 'Create a subject to start organizing your learning.'"
    >
      <button v-if="!search && !includeArchived" class="primary-button" type="button" @click="openCreate">
        Create a subject
      </button>
    </EmptyState>
    <nav
      v-if="(subjectsQuery.data.value?.total ?? 0) > pageSize"
      class="pagination"
      aria-label="Subject pages"
    >
      <button class="secondary-button" type="button" :disabled="page === 1" @click="page--">
        Previous
      </button>
      <span>Page {{ page }} of {{ Math.ceil((subjectsQuery.data.value?.total ?? 0) / pageSize) }}</span>
      <button
        class="secondary-button"
        type="button"
        :disabled="page * pageSize >= (subjectsQuery.data.value?.total ?? 0)"
        @click="page++"
      >
        Next
      </button>
    </nav>

    <ModalDialog
      v-if="showForm"
      labelled-by="subject-form-title"
      @close="showForm = false"
    >
      <h2 id="subject-form-title">{{ editing ? 'Edit subject' : 'Create a subject' }}</h2>
      <ResourceForm
        :key="editing?.id ?? 'new-subject'"
        resource-type="subject"
        :initial-name="editing?.name"
        :initial-description="editing?.description"
        :initial-color="editing?.color"
        :initial-icon="editing?.icon"
        :busy="saveMutation.isPending.value"
        @save="saveSubject"
        @cancel="showForm = false"
      />
    </ModalDialog>

    <ConfirmDialog
      :model-value="pendingDelete !== null"
      title="Delete this subject?"
      :message="`Permanently delete ${pendingDelete?.name ?? 'this subject'}, its study spaces, topics, and related activity? This cannot be undone.`"
      :busy="deleteMutation.isPending.value"
      @close="pendingDelete = null"
      @confirm="pendingDelete && deleteMutation.mutate(pendingDelete.id)"
    />
  </AppShell>
</template>
