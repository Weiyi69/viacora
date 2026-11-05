<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AuthPanel from '../components/AuthPanel.vue'
import { api, ApiError } from '../api/client'

const route = useRoute()
const router = useRouter()
const token = ref('')
const password = ref('')
const confirmation = ref('')
const error = ref('')
const loading = ref(false)
const completed = ref(false)

onMounted(async () => {
  const queryToken =
    typeof route.query.token === 'string'
      ? route.query.token
      : new URLSearchParams(route.hash.slice(1)).get('token')
  if (queryToken) {
    token.value = queryToken
    await router.replace({ path: '/reset-password', query: {}, hash: '' })
  } else {
    error.value = 'This reset link is invalid or has expired.'
  }
})

async function submit(): Promise<void> {
  error.value = ''
  if (password.value !== confirmation.value) {
    error.value = 'The passwords do not match.'
    return
  }
  loading.value = true
  try {
    await api.post('/auth/password/reset', {
      token: token.value,
      new_password: password.value,
    })
    completed.value = true
    token.value = ''
    password.value = ''
    confirmation.value = ''
  } catch (reason) {
    error.value =
      reason instanceof ApiError ? reason.message : 'Unable to reset your password. Please try again.'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <AuthPanel title="Choose a new password" description="Use a new password for your account.">
    <div v-if="completed" class="form-success" role="status">
      Password reset. <RouterLink to="/login">Sign in</RouterLink>
    </div>
    <form v-else-if="token" class="auth-form" @submit.prevent="submit">
      <label>
        New password
        <input
          v-model="password"
          type="password"
          autocomplete="new-password"
          required
          minlength="12"
          maxlength="128"
        />
      </label>
      <label>
        Confirm password
        <input
          v-model="confirmation"
          type="password"
          autocomplete="new-password"
          required
          minlength="12"
          maxlength="128"
        />
      </label>
      <p v-if="error" class="form-error" role="alert">{{ error }}</p>
      <button class="primary-button" type="submit" :disabled="loading">
        {{ loading ? 'Updating…' : 'Reset password' }}
      </button>
    </form>
    <p v-else-if="error" class="form-error" role="alert">{{ error }}</p>
    <nav class="auth-links"><RouterLink to="/login">Back to sign in</RouterLink></nav>
  </AuthPanel>
</template>
