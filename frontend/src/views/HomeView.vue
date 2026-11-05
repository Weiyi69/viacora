<script setup lang="ts">
import { ref } from 'vue'
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { useRouter } from 'vue-router'
import { createStudySpace } from '../api/studySpaces'
import { getDashboard } from '../api/dashboard'
import { createSubject, getSubjects } from '../api/subjects'
import type { ResourceInput } from '../api/learningTypes'
import AppShell from '../components/AppShell.vue'
import EmptyState from '../components/EmptyState.vue'
import LoadingSkeleton from '../components/LoadingSkeleton.vue'
import ModalDialog from '../components/ModalDialog.vue'
import ResourceForm from '../components/ResourceForm.vue'
import { ApiError } from '../api/client'

const queryClient = useQueryClient()
const router = useRouter()
const showSpaceForm = ref(false)
const showSubjectForm = ref(false)
const showSubjectPrompt = ref(false)
const selectedSubjectId = ref('')
const actionError = ref('')
const actionStatus = ref('')

const dashboardQuery = useQuery({
  queryKey: ['dashboard'],
  queryFn: getDashboard,
})
const subjectsQuery = useQuery({
  queryKey: ['subjects', { page: 1, pageSize: 100 }],
  queryFn: () => getSubjects({ page: 1, pageSize: 100 }),
})

const createSubjectMutation = useMutation({
  mutationFn: createSubject,
  onSuccess: async (subject) => {
    showSubjectForm.value = false
    actionError.value = ''
    actionStatus.value = 'Subject created.'
    await queryClient.invalidateQueries({ queryKey: ['dashboard'] })
    await queryClient.invalidateQueries({ queryKey: ['subjects'] })
    await router.push(`/subjects/${subject.id}`)
  },
  onError: (error: unknown) => {
    actionError.value =
      error instanceof ApiError ? error.message : 'Unable to create the subject.'
  },
})

const createSpaceMutation = useMutation({
  mutationFn: (input: { subjectId: string; resource: ResourceInput }) =>
    createStudySpace(input.subjectId, input.resource),
  onSuccess: async (space) => {
    showSpaceForm.value = false
    actionError.value = ''
    actionStatus.value = 'Study space created.'
    await queryClient.invalidateQueries({ queryKey: ['dashboard'] })
    await queryClient.invalidateQueries({ queryKey: ['study-spaces'] })
    await router.push(`/study-spaces/${space.id}`)
  },
  onError: (error: unknown) => {
    actionError.value =
      error instanceof ApiError ? error.message : 'Unable to create the study space.'
  },
})

function activityLabel(eventType: string): string {
  return eventType.replaceAll('_', ' ')
}

function openCreateSpace(): void {
  actionError.value = ''
  if (subjectsQuery.isPending.value) {
    actionError.value = 'Your subjects are still loading. Please try again in a moment.'
    return
  }
  if (subjectsQuery.isError.value) {
    actionError.value = 'Your subjects could not load. Retry the request before creating a space.'
    return
  }
  if ((subjectsQuery.data.value?.items.length ?? 0) === 0) {
    showSubjectPrompt.value = true
    return
  }
  selectedSubjectId.value = subjectsQuery.data.value?.items[0]?.id ?? ''
  showSpaceForm.value = true
}

function openCreateSubject(): void {
  actionError.value = ''
  showSubjectForm.value = true
}

function submitSpace(input: ResourceInput): void {
  if (!selectedSubjectId.value) {
    actionError.value = 'Choose a subject before creating a study space.'
    return
  }
  void createSpaceMutation.mutateAsync({
    subjectId: selectedSubjectId.value,
    resource: input,
  })
}

function submitSubject(input: ResourceInput): void {
  void createSubjectMutation.mutateAsync(input)
}
</script>

<template>
  <AppShell>
    <section class="page-heading dashboard-heading">
      <div>
        <p class="eyebrow">Your learning space</p>
        <h1>Welcome back, {{ dashboardQuery.data.value?.display_name || 'learner' }}.</h1>
        <p class="page-description">A clear place to continue learning at your own pace.</p>
      </div>
      <div class="heading-actions">
        <button class="secondary-button" type="button" @click="openCreateSubject">
          New subject
        </button>
        <button class="primary-button" type="button" @click="openCreateSpace">
          New study space
        </button>
      </div>
    </section>

    <p v-if="actionError" class="form-error" role="alert">{{ actionError }}</p>
    <p v-if="actionStatus" class="form-success" role="status">{{ actionStatus }}</p>
    <p v-if="subjectsQuery.isError.value" class="form-error" role="alert">
      Subjects could not be loaded.
      <button class="text-button" type="button" @click="subjectsQuery.refetch()">Retry</button>
    </p>

    <section v-if="dashboardQuery.isPending.value" class="dashboard-loading">
      <LoadingSkeleton :rows="4" />
    </section>
    <section v-else-if="dashboardQuery.isError.value" class="surface-panel">
      <EmptyState
        title="Your dashboard could not load"
        description="Your learning data is still safe. Check your connection and try again."
      >
        <button class="secondary-button" type="button" @click="dashboardQuery.refetch()">
          Try again
        </button>
      </EmptyState>
    </section>
    <template v-else-if="dashboardQuery.data.value">
      <section class="overview-grid" aria-label="Your learning overview">
        <article class="overview-card">
          <span class="overview-label">Subjects</span>
          <strong>{{ dashboardQuery.data.value.subject_count }}</strong>
          <span class="overview-note">Active subjects</span>
        </article>
        <article class="overview-card">
          <span class="overview-label">Study spaces</span>
          <strong>{{ dashboardQuery.data.value.study_space_count }}</strong>
          <span class="overview-note">Inside your subjects</span>
        </article>
      </section>

      <section class="dashboard-section" aria-labelledby="dashboard-subjects-title">
        <div class="section-heading">
          <div>
            <h2 id="dashboard-subjects-title">Your subjects</h2>
            <p>Pick up a subject or create a new one.</p>
          </div>
          <RouterLink class="text-button" to="/subjects">View all</RouterLink>
        </div>
        <div v-if="dashboardQuery.data.value.subjects.length" class="subject-grid">
          <article
            v-for="subject in dashboardQuery.data.value.subjects"
            :key="subject.id"
            class="dashboard-subject"
          >
            <span class="subject-icon" :class="`tone-${subject.color ?? 'blue'}`" aria-hidden="true">
              {{ subject.icon === 'flask' ? 'F' : subject.icon === 'atom' ? 'A' : 'B' }}
            </span>
            <div>
              <RouterLink class="resource-title" :to="`/subjects/${subject.id}`">
                {{ subject.name }}
              </RouterLink>
              <p>{{ subject.study_space_count }} study spaces</p>
            </div>
          </article>
        </div>
        <EmptyState
          v-else
          title="Start with a subject"
          description="Create a subject to organize the things you want to learn."
        >
          <RouterLink class="primary-button" to="/subjects">Create a subject</RouterLink>
        </EmptyState>
      </section>

      <div class="dashboard-columns">
        <section class="surface-panel" aria-labelledby="recent-access-title">
          <div class="section-heading">
            <div>
              <h2 id="recent-access-title">Recently opened</h2>
              <p>Subjects and study spaces you opened.</p>
            </div>
          </div>
          <ul v-if="dashboardQuery.data.value.recently_accessed.length" class="activity-list">
            <li
              v-for="item in dashboardQuery.data.value.recently_accessed"
              :key="item.id"
            >
              <RouterLink
                v-if="item.study_space_id"
                :to="`/study-spaces/${item.study_space_id}`"
              >
                {{ item.resource_name }}
              </RouterLink>
              <RouterLink v-else-if="item.subject_id" :to="`/subjects/${item.subject_id}`">
                {{ item.resource_name }}
              </RouterLink>
              <span v-else>{{ item.resource_name }}</span>
              <time :datetime="item.occurred_at">{{ new Date(item.occurred_at).toLocaleDateString() }}</time>
            </li>
          </ul>
          <p v-else class="muted-copy">Opened subjects and study spaces will appear here.</p>
        </section>

        <section class="surface-panel" aria-labelledby="recent-activity-title">
          <div class="section-heading">
            <div>
              <h2 id="recent-activity-title">Recent activity</h2>
              <p>A record of learning-space changes and visits.</p>
            </div>
          </div>
          <ul v-if="dashboardQuery.data.value.recent_activity.length" class="activity-list">
            <li v-for="item in dashboardQuery.data.value.recent_activity" :key="item.id">
              <span>
                <strong>{{ item.resource_name }}</strong>
                <small>{{ activityLabel(item.event_type) }}</small>
              </span>
              <time :datetime="item.occurred_at">{{ new Date(item.occurred_at).toLocaleDateString() }}</time>
            </li>
          </ul>
          <p v-else class="muted-copy">Your activity will appear here as you organize your learning.</p>
        </section>
      </div>
    </template>

    <ModalDialog
      v-if="showSubjectForm"
      labelled-by="dashboard-subject-title"
      @close="showSubjectForm = false"
    >
      <h2 id="dashboard-subject-title">Create a subject</h2>
      <ResourceForm
        resource-type="subject"
        :busy="createSubjectMutation.isPending.value"
        @save="submitSubject"
        @cancel="showSubjectForm = false"
      />
    </ModalDialog>

    <ModalDialog
      v-if="showSpaceForm"
      labelled-by="space-form-title"
      @close="showSpaceForm = false"
    >
      <h2 id="space-form-title">Create a study space</h2>
      <label>
        Subject
        <select v-model="selectedSubjectId" required>
          <option v-for="subject in subjectsQuery.data.value?.items ?? []" :key="subject.id" :value="subject.id">
            {{ subject.name }}
          </option>
        </select>
      </label>
      <ResourceForm
        resource-type="study-space"
        :busy="createSpaceMutation.isPending.value"
        @save="submitSpace"
        @cancel="showSpaceForm = false"
      />
    </ModalDialog>

    <ModalDialog
      v-if="showSubjectPrompt"
      labelled-by="subject-prompt-title"
      panel-class="confirm-dialog"
      @close="showSubjectPrompt = false"
    >
      <h2 id="subject-prompt-title">Create a subject first</h2>
      <p>Study spaces belong to a subject. Add one before creating a study space.</p>
      <div class="dialog-actions">
        <button class="secondary-button" type="button" @click="showSubjectPrompt = false">
          Close
        </button>
        <RouterLink class="primary-button" to="/subjects" @click="showSubjectPrompt = false">
          Go to subjects
        </RouterLink>
      </div>
    </ModalDialog>
  </AppShell>
</template>
