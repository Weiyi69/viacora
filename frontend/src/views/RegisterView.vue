<script setup lang="ts">
import { ref } from 'vue'
import AuthPanel from '../components/AuthPanel.vue'
import { ApiError } from '../api/client'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const email = ref('')
const displayName = ref('')
const password = ref('')
const error = ref('')
const loading = ref(false)
const registered = ref(false)

async function submit(): Promise<void> {
  error.value = ''
  loading.value = true
  try {
    await auth.register(email.value, password.value, displayName.value)
    registered.value = true
  } catch (reason) {
    error.value =
      reason instanceof ApiError
        ? reason.message
        : 'Unable to create your account right now. Please try again.'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <AuthPanel title="Create your learning space" description="Start with a few details.">
    <div v-if="registered" class="form-success" role="status">
      Check your email for a verification link before signing in.
      <RouterLink to="/login">Go to sign in</RouterLink>
    </div>
    <form v-else class="auth-form" @submit.prevent="submit">
      <label>
        Display name
        <input v-model="displayName" autocomplete="name" required minlength="1" maxlength="80" />
      </label>
      <label>
        Email
        <input v-model="email" type="email" autocomplete="email" required maxlength="320" />
      </label>
      <label>
        Password
        <input
          v-model="password"
          type="password"
          autocomplete="new-password"
          required
          minlength="12"
          maxlength="128"
        />
        <small>Use 12 to 128 characters.</small>
      </label>
      <p v-if="error" class="form-error" role="alert">{{ error }}</p>
      <button class="primary-button" type="submit" :disabled="loading">
        {{ loading ? 'Creating account…' : 'Create account' }}
      </button>
    </form>
    <nav v-if="!registered" class="auth-links">
      <span>Already have an account? <RouterLink to="/login">Sign in</RouterLink></span>
    </nav>
  </AuthPanel>
</template>
