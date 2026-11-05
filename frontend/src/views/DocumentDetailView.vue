<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { ApiError, apiFileUrl } from '../api/client'
import {
  assignDocumentTopic,
  deleteDocument,
  getDocument,
  getDocumentPages,
  retryDocument,
} from '../api/documents'
import { getTopics } from '../api/topics'
import AppShell from '../components/AppShell.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import LoadingSkeleton from '../components/LoadingSkeleton.vue'

const route = useRoute()
const router = useRouter()
const queryClient = useQueryClient()
const documentId = computed(() => String(route.params.documentId))
const page = ref(1)
const pageSize = 10
const topicId = ref('')
const actionError = ref('')
const actionStatus = ref('')
const pendingDelete = ref(false)

const documentQuery = useQuery({
  queryKey: computed(() => ['document', documentId.value]),
  queryFn: () => getDocument(documentId.value),
  refetchInterval: (query) =>
    query.state.data?.status === 'queued' || query.state.data?.status === 'processing'
      ? 3000
      : false,
})
const studySpaceId = computed(() => documentQuery.data.value?.study_space_id ?? '')
const topicsQuery = useQuery({
  queryKey: computed(() => ['topics', studySpaceId.value, 'document-detail']),
  queryFn: () => getTopics(studySpaceId.value, undefined, 1, 100),
  enabled: computed(() => Boolean(studySpaceId.value)),
})
const pagesQuery = useQuery({
  queryKey: computed(() => ['document-pages', documentId.value, page.value]),
  queryFn: () => getDocumentPages(documentId.value, page.value, pageSize),
  enabled: computed(
    () =>
      documentQuery.data.value?.status === 'completed' &&
      documentQuery.data.value.has_extractable_text,
  ),
})

watch(
  () => documentQuery.data.value?.topic_id,
  (value) => {
    topicId.value = value ?? ''
  },
  { immediate: true },
)

async function refreshDocumentData(): Promise<void> {
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: ['document', documentId.value] }),
    queryClient.invalidateQueries({ queryKey: ['documents'] }),
    queryClient.invalidateQueries({ queryKey: ['document-pages', documentId.value] }),
  ])
}

const retryMutation = useMutation({
  mutationFn: () => retryDocument(documentId.value),
  onSuccess: async () => {
    actionError.value = ''
    actionStatus.value = 'Retry scheduled.'
    await refreshDocumentData()
  },
  onError: (error: unknown) => {
    actionError.value =
      error instanceof ApiError ? error.message : 'The document cannot be retried.'
  },
})
const topicMutation = useMutation({
  mutationFn: () => assignDocumentTopic(documentId.value, topicId.value || null),
  onSuccess: async () => {
    actionError.value = ''
    actionStatus.value = 'Topic assignment saved.'
    await refreshDocumentData()
  },
  onError: (error: unknown) => {
    actionError.value =
      error instanceof ApiError ? error.message : 'Topic assignment could not be saved.'
  },
})
const deleteMutation = useMutation({
  mutationFn: () => deleteDocument(documentId.value),
  onSuccess: async () => {
    await queryClient.invalidateQueries({ queryKey: ['documents'] })
    await router.push('/documents')
  },
  onError: (error: unknown) => {
    actionError.value =
      error instanceof ApiError ? error.message : 'The document could not be deleted.'
  },
})

const canPreview = import.meta.env.DEV

function errorMessage(code: string | null): string {
  const messages: Record<string, string> = {
    encrypted_pdf: 'Password-protected PDFs are not supported.',
    page_limit_exceeded: 'This PDF exceeds the 500-page limit.',
    text_limit_exceeded: 'Extracted text exceeds the 10 MiB limit.',
    invalid_pdf: 'This PDF could not be read. Try another PDF file.',
    storage_unavailable: 'The private file is temporarily unavailable; retry may help.',
    attempts_exhausted: 'Processing stopped after three attempts.',
    processing_error: 'Processing failed unexpectedly. You can try another upload.',
  }
  return messages[code ?? ''] ?? 'The PDF could not be processed.'
}

function formatBytes(bytes: number): string {
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`
}
</script>

<template>
  <AppShell>
    <nav class="breadcrumbs" aria-label="Breadcrumb">
      <RouterLink to="/documents">Documents</RouterLink>
      <span aria-hidden="true">/</span>
      <span>{{ documentQuery.data.value?.original_filename ?? 'Document' }}</span>
    </nav>
    <LoadingSkeleton v-if="documentQuery.isPending.value" />
    <section v-else-if="documentQuery.isError.value" class="surface-panel">
      <h1>Document unavailable</h1>
      <p>This document may have been deleted, archived with its parent, or you may not have access.</p>
      <RouterLink class="secondary-button" to="/documents">Back to documents</RouterLink>
    </section>
    <template v-else-if="documentQuery.data.value">
      <section class="page-heading">
        <div>
          <p class="eyebrow">Private PDF</p>
          <h1>{{ documentQuery.data.value.original_filename }}</h1>
          <p class="page-description">
            {{ formatBytes(documentQuery.data.value.file_size_bytes) }} ·
            {{ documentQuery.data.value.status }}
            <span v-if="documentQuery.data.value.page_count !== null">
              · {{ documentQuery.data.value.page_count }} pages
            </span>
          </p>
        </div>
        <div class="card-actions">
          <a
            class="secondary-button"
            :href="apiFileUrl(`/documents/${documentId}/file?disposition=attachment`)"
            target="_blank"
            rel="noopener noreferrer"
          >Download PDF</a>
          <a
            v-if="canPreview"
            class="secondary-button"
            :href="apiFileUrl(`/documents/${documentId}/file?disposition=inline`)"
            target="_blank"
            rel="noopener noreferrer"
          >Preview original</a>
          <button
            class="small-button danger-text"
            type="button"
            @click="pendingDelete = true"
          >
            Delete
          </button>
        </div>
      </section>

      <p v-if="actionError" class="form-error" role="alert">{{ actionError }}</p>
      <p v-if="actionStatus" class="form-success" role="status">{{ actionStatus }}</p>

      <section class="surface-panel document-metadata">
        <p><strong>Status:</strong> {{ documentQuery.data.value.status }}</p>
        <p><strong>Processing attempts:</strong> {{ documentQuery.data.value.attempts_used }} / 3</p>
        <label class="sort-control">
          Topic
          <select v-model="topicId" aria-label="Assign document to a topic">
            <option value="">No topic</option>
            <option
              v-for="topic in topicsQuery.data.value?.items ?? []"
              :key="topic.id"
              :value="topic.id"
            >
              {{ topic.name }}
            </option>
          </select>
        </label>
        <button
          class="secondary-button"
          type="button"
          :disabled="topicMutation.isPending.value || topicId === (documentQuery.data.value.topic_id ?? '')"
          @click="topicMutation.mutate()"
        >
          Save topic
        </button>
        <p v-if="documentQuery.data.value.error_code" class="form-error" role="alert">
          {{ errorMessage(documentQuery.data.value.error_code) }}
        </p>
        <button
          v-if="documentQuery.data.value.retryable"
          class="secondary-button"
          type="button"
          :disabled="retryMutation.isPending.value"
          @click="retryMutation.mutate()"
        >
          Retry now
        </button>
        <p
          v-if="documentQuery.data.value.status === 'completed' && !documentQuery.data.value.has_extractable_text"
          role="status"
        >
          No selectable text found; scanned PDFs are not OCR-processed.
        </p>
      </section>

      <section v-if="documentQuery.data.value.has_extractable_text" class="document-text">
        <h2>Extracted text</h2>
        <p v-if="pagesQuery.isPending.value" role="status">Loading extracted text…</p>
        <p v-else-if="pagesQuery.isError.value" class="form-error" role="alert">
          Extracted text could not load.
        </p>
        <article
          v-for="item in pagesQuery.data.value?.items ?? []"
          :key="item.page_number"
          class="surface-panel extracted-page"
        >
          <h3>Page {{ item.page_number }}</h3>
          <pre>{{ item.extracted_text || 'No selectable text on this page.' }}</pre>
        </article>
        <nav
          v-if="(pagesQuery.data.value?.total ?? 0) > pageSize"
          class="pagination"
          aria-label="Extracted text pages"
        >
          <button class="secondary-button" type="button" :disabled="page === 1" @click="page--">
            Previous
          </button>
          <span>
            Page {{ page }} of {{ Math.ceil((pagesQuery.data.value?.total ?? 0) / pageSize) }}
          </span>
          <button
            class="secondary-button"
            type="button"
            :disabled="page * pageSize >= (pagesQuery.data.value?.total ?? 0)"
            @click="page++"
          >
            Next
          </button>
        </nav>
      </section>
    </template>

    <ConfirmDialog
      :model-value="pendingDelete"
      title="Delete this document?"
      message="Permanently delete this PDF and its extracted text? This cannot be undone."
      :busy="deleteMutation.isPending.value"
      @close="pendingDelete = false"
      @confirm="deleteMutation.mutate()"
    />
  </AppShell>
</template>
