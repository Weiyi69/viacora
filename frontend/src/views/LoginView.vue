<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AuthPanel from '../components/AuthPanel.vue'
import { ApiError } from '../api/client'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const email = ref('')
const password = ref('')
const error = ref('')
const loading = ref(false)

async function submit(): Promise<void> {
  error.value = ''
  loading.value = true
  try {
    await auth.login(email.value, password.value)
    const redirect = route.query.redirect
    const safeRedirect =
      typeof redirect === 'string' && redirect.startsWith('/') && !redirect.startsWith('//')
        ? redirect
        : '/'
    await router.replace(safeRedirect)
  } catch (reason) {
    error.value =
      reason instanceof ApiError
        ? reason.message
        : 'Unable to sign in right now. Please try again.'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <AuthPanel title="Welcome back" description="Sign in to return to your learning space.">
    <p v-if="route.query.serviceUnavailable || auth.initializationFailed" class="form-error" role="alert">
      We could not check your session. You can try signing in again.
    </p>
    <form class="auth-form" @submit.prevent="submit">
      <label>
        Email
        <input v-model="email" type="email" autocomplete="email" required maxlength="320" />
      </label>
      <label>
        Password
        <input
          v-model="password"
          type="password"
          autocomplete="current-password"
          required
          maxlength="128"
        />
      </label>
      <p v-if="error" class="form-error" role="alert">{{ error }}</p>
      <button class="primary-button" type="submit" :disabled="loading">
        {{ loading ? 'Signing in…' : 'Sign in' }}
      </button>
    </form>
    <nav class="auth-links">
      <RouterLink to="/forgot-password">Forgot password?</RouterLink>
      <span>New to Viacora? <RouterLink to="/register">Create an account</RouterLink></span>
    </nav>
  </AuthPanel>
</template>
