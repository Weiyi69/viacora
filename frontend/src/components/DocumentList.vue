<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { ApiError } from '../api/client'
import {
  getDocuments,
  uploadDocument,
  type DocumentStatus,
  type LearningDocument,
} from '../api/documents'
import { getSubjects } from '../api/subjects'
import { getTopics } from '../api/topics'

const props = defineProps<{
  subjectId?: string
  studySpaceId?: string
}>()

const queryClient = useQueryClient()
const search = ref('')
const subjectFilter = ref('')
const statusFilter = ref<DocumentStatus | ''>('')
const page = ref(1)
const pageSize = 20
const topicId = ref('')
const uploadProgress = ref(0)
const uploadError = ref('')
const uploadStatus = ref('')
const dragging = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)

const filters = computed(() => ({
  subjectId: props.subjectId ?? (subjectFilter.value || undefined),
  studySpaceId: props.studySpaceId,
  q: search.value.trim() || undefined,
  status: statusFilter.value || undefined,
  page: page.value,
  pageSize,
}))

const documentsQuery = useQuery({
  queryKey: computed(() => ['documents', filters.value]),
  queryFn: () => getDocuments(filters.value),
  refetchInterval: (query) =>
    query.state.data?.items.some(
      (document) => document.status === 'queued' || document.status === 'processing',
    )
      ? 3000
      : false,
})
const subjectsQuery = useQuery({
  queryKey: ['subjects', 'document-filter'],
  queryFn: () => getSubjects({ page: 1, pageSize: 100 }),
  enabled: computed(() => !props.subjectId),
})
const topicsQuery = useQuery({
  queryKey: computed(() => ['topics', props.studySpaceId, 'document-upload']),
  queryFn: () => getTopics(props.studySpaceId!, undefined, 1, 100),
  enabled: computed(() => Boolean(props.studySpaceId)),
})

watch([search, statusFilter, subjectFilter], () => {
  page.value = 1
})

const uploadMutation = useMutation({
  mutationFn: (file: File) =>
    uploadDocument(
      props.subjectId!,
      props.studySpaceId!,
      file,
      topicId.value || null,
      (progress) => {
        uploadProgress.value = progress
      },
    ),
  onSuccess: async (document: LearningDocument) => {
    uploadError.value = ''
    uploadStatus.value = `${document.original_filename} uploaded and queued for processing.`
    page.value = 1
    await queryClient.invalidateQueries({ queryKey: ['documents'] })
  },
  onError: (error: unknown) => {
    uploadError.value =
      error instanceof ApiError ? error.message : 'The document could not be uploaded.'
  },
  onSettled: () => {
    uploadProgress.value = 0
    if (fileInput.value) fileInput.value.value = ''
  },
})

function chooseFile(file: File | undefined): void {
  uploadError.value = ''
  uploadStatus.value = ''
  if (!file) return
  if (!file.name.toLowerCase().endsWith('.pdf')) {
    uploadError.value = 'Choose a PDF file.'
    return
  }
  if (file.size > 25 * 1024 * 1024) {
    uploadError.value = 'The PDF must be 25 MiB or smaller.'
    return
  }
  uploadMutation.mutate(file)
}

function handleDrop(event: DragEvent): void {
  dragging.value = false
  event.preventDefault()
  const files = event.dataTransfer?.files
  if (files && files.length > 1) {
    uploadError.value = 'Upload one PDF at a time.'
    return
  }
  chooseFile(files?.item(0) ?? undefined)
}

function handleKeydown(event: KeyboardEvent): void {
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault()
    fileInput.value?.click()
  }
}

function formatBytes(bytes: number): string {
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`
}

function statusLabel(status: DocumentStatus): string {
  return {
    queued: 'Queued',
    processing: 'Processing',
    completed: 'Ready',
    failed: 'Failed',
  }[status]
}
</script>

<template>
  <section class="document-section" aria-labelledby="documents-heading">
    <div v-if="subjectId || studySpaceId" class="page-heading">
      <div>
        <p class="eyebrow">Learning materials</p>
        <h2 id="documents-heading">Documents</h2>
      </div>
      <RouterLink class="secondary-button" to="/documents">Open library</RouterLink>
    </div>

    <div v-if="documentsQuery.data.value" class="surface-panel storage-summary">
      <span>Storage used</span>
      <strong>
        {{ formatBytes(documentsQuery.data.value.storage_used_bytes) }} /
        {{ formatBytes(documentsQuery.data.value.storage_quota_bytes) }}
      </strong>
    </div>

    <section v-if="studySpaceId" class="surface-panel document-upload">
      <h3>Upload a PDF</h3>
      <label
        class="upload-dropzone"
        :class="{ 'upload-dropzone-active': dragging }"
        for="document-file-input"
        tabindex="0"
        @keydown="handleKeydown"
        @dragenter.prevent="dragging = true"
        @dragover.prevent="dragging = true"
        @dragleave.prevent="dragging = false"
        @drop="handleDrop"
      >
        <input
          id="document-file-input"
          ref="fileInput"
          class="sr-only"
          type="file"
          accept=".pdf,application/pdf"
          :disabled="uploadMutation.isPending.value"
          @change="chooseFile(($event.target as HTMLInputElement).files?.item(0) ?? undefined)"
        />
        <span>Drop a PDF here or press Enter to choose a file</span>
        <small>Maximum 25 MiB. Scanned PDFs are not processed with OCR.</small>
      </label>
      <label v-if="topicsQuery.data.value?.items.length" class="sort-control">
        Add to topic
        <select v-model="topicId" aria-label="Assign uploaded PDF to a topic">
          <option value="">No topic</option>
          <option v-for="topic in topicsQuery.data.value.items" :key="topic.id" :value="topic.id">
            {{ topic.name }}
          </option>
        </select>
      </label>
      <progress
        v-if="uploadMutation.isPending.value"
        class="upload-progress"
        :value="uploadProgress"
        max="100"
        :aria-label="`Upload progress: ${uploadProgress}%`"
      />
      <p v-if="uploadError" class="form-error" role="alert">{{ uploadError }}</p>
      <p v-if="uploadStatus" class="form-success" role="status">{{ uploadStatus }}</p>
    </section>

    <section class="filter-bar" aria-label="Document filters">
      <label class="search-control">
        <span class="sr-only">Search documents</span>
        <input v-model="search" type="search" placeholder="Search documents" maxlength="255" />
      </label>
      <label class="sort-control">
        Status
        <select v-model="statusFilter" aria-label="Filter documents by status">
          <option value="">All statuses</option>
          <option value="queued">Queued</option>
          <option value="processing">Processing</option>
          <option value="completed">Ready</option>
          <option value="failed">Failed</option>
        </select>
      </label>
      <label v-if="!subjectId" class="sort-control">
        Subject
        <select v-model="subjectFilter" aria-label="Filter documents by subject">
          <option value="">All subjects</option>
          <option
            v-for="subject in subjectsQuery.data.value?.items ?? []"
            :key="subject.id"
            :value="subject.id"
          >
            {{ subject.name }}
          </option>
        </select>
      </label>
    </section>

    <p v-if="documentsQuery.isPending.value" role="status">Loading documents…</p>
    <section v-else-if="documentsQuery.isError.value" class="surface-panel">
      <p class="form-error" role="alert">Documents could not load.</p>
      <button class="secondary-button" type="button" @click="documentsQuery.refetch()">
        Try again
      </button>
    </section>
    <section v-else-if="documentsQuery.data.value?.items.length" class="document-list">
      <article
        v-for="document in documentsQuery.data.value.items"
        :key="document.id"
        class="topic-row document-row"
      >
        <div class="resource-card-copy">
          <h3><RouterLink :to="`/documents/${document.id}`">{{ document.original_filename }}</RouterLink></h3>
          <p>
            {{ statusLabel(document.status) }} · {{ formatBytes(document.file_size_bytes) }}
            <span v-if="document.page_count !== null"> · {{ document.page_count }} pages</span>
          </p>
        </div>
        <RouterLink class="small-button" :to="`/documents/${document.id}`">Open</RouterLink>
      </article>
    </section>
    <p v-else class="surface-panel empty-state-copy">
      {{ search || statusFilter ? 'No documents match these filters.' : 'No documents yet.' }}
    </p>

    <nav
      v-if="(documentsQuery.data.value?.total ?? 0) > pageSize"
      class="pagination"
      aria-label="Document pages"
    >
      <button class="secondary-button" type="button" :disabled="page === 1" @click="page--">
        Previous
      </button>
      <span>Page {{ page }} of {{ Math.ceil((documentsQuery.data.value?.total ?? 0) / pageSize) }}</span>
      <button
        class="secondary-button"
        type="button"
        :disabled="page * pageSize >= (documentsQuery.data.value?.total ?? 0)"
        @click="page++"
      >
        Next
      </button>
    </nav>
  </section>
</template>
