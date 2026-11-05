<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AuthPanel from '../components/AuthPanel.vue'
import { api, ApiError } from '../api/client'
import { useAuthStore } from '../stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const email = ref(typeof route.query.email === 'string' ? route.query.email : auth.user?.email ?? '')
const error = ref('')
const message = ref('')
const loading = ref(false)
const tokenFlow = ref(false)

onMounted(async () => {
  const token =
    typeof route.query.token === 'string'
      ? route.query.token
      : new URLSearchParams(route.hash.slice(1)).get('token')
  if (!token) return
  tokenFlow.value = true
  await router.replace({ path: '/verify-email' })
  loading.value = true
  try {
    const result = await api.post<{ message: string }>('/auth/email-verification/confirm', { token })
    message.value = result.message
  } catch (reason) {
    error.value =
      reason instanceof ApiError ? reason.message : 'Unable to verify this email. Please try again.'
  } finally {
    loading.value = false
  }
})

async function resend(): Promise<void> {
  error.value = ''
  message.value = ''
  loading.value = true
  try {
    const result = await api.post<{ message: string }>('/auth/email-verification/resend', {
      email: email.value,
    })
    message.value = result.message
  } catch (reason) {
    error.value =
      reason instanceof ApiError ? reason.message : 'Unable to send a verification email. Please try again.'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <AuthPanel title="Verify your email" description="Verify your address to access your learning space.">
    <p v-if="loading && tokenFlow" role="status">Verifying your email…</p>
    <p v-if="message" class="form-success" role="status">{{ message }}</p>
    <form v-if="!message && (!tokenFlow || error)" class="auth-form" @submit.prevent="resend">
      <label>
        Email
        <input v-model="email" type="email" autocomplete="email" required maxlength="320" />
      </label>
      <p v-if="error" class="form-error" role="alert">{{ error }}</p>
      <button class="primary-button" type="submit" :disabled="loading">
        {{ loading ? 'Working…' : 'Resend verification email' }}
      </button>
    </form>
    <p v-if="!auth.isAuthenticated || auth.user?.email_verified" class="auth-links">
      <RouterLink to="/login">Go to sign in</RouterLink>
    </p>
  </AuthPanel>
</template>
