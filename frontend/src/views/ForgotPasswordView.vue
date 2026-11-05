<script setup lang="ts">
import { ref } from 'vue'
import AuthPanel from '../components/AuthPanel.vue'
import { api, ApiError } from '../api/client'

const email = ref('')
const error = ref('')
const loading = ref(false)
const submitted = ref(false)

async function submit(): Promise<void> {
  error.value = ''
  loading.value = true
  try {
    await api.post('/auth/password/forgot', { email: email.value })
    submitted.value = true
  } catch (reason) {
    error.value =
      reason instanceof ApiError ? reason.message : 'Unable to submit your request. Please try again.'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <AuthPanel title="Reset your password" description="We’ll send a reset link if the account can receive email.">
    <div v-if="submitted" class="form-success" role="status">
      If the account can receive this email, instructions will be sent.
    </div>
    <form v-else class="auth-form" @submit.prevent="submit">
      <label>
        Email
        <input v-model="email" type="email" autocomplete="email" required maxlength="320" />
      </label>
      <p v-if="error" class="form-error" role="alert">{{ error }}</p>
      <button class="primary-button" type="submit" :disabled="loading">
        {{ loading ? 'Sending…' : 'Send reset link' }}
      </button>
    </form>
    <nav class="auth-links"><RouterLink to="/login">Back to sign in</RouterLink></nav>
  </AuthPanel>
</template>
