<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const router = useRouter()
const logoutError = ref('')
const signingOut = ref(false)

async function signOut(): Promise<void> {
  logoutError.value = ''
  signingOut.value = true
  try {
    await auth.logout()
    await router.push('/login')
  } catch {
    logoutError.value = 'Unable to sign out right now. Please try again.'
  } finally {
    signingOut.value = false
  }
}
</script>

<template>
  <div class="app-shell">
    <header class="app-topbar">
      <RouterLink class="brand" to="/" aria-label="Viacora dashboard">
        <img class="brand-logo" :src="'/images/logo.png'" alt="Viacora" />
      </RouterLink>
      <nav class="app-nav" aria-label="Main navigation">
        <RouterLink to="/" exact-active-class="active">Dashboard</RouterLink>
        <RouterLink to="/subjects" active-class="active">Subjects</RouterLink>
        <RouterLink to="/documents" active-class="active">Documents</RouterLink>
        <RouterLink to="/system-status" active-class="active">System status</RouterLink>
      </nav>
      <button class="text-button" type="button" :disabled="signingOut" @click="signOut">
        {{ signingOut ? 'Signing out…' : 'Sign out' }}
      </button>
    </header>
    <p v-if="logoutError" class="form-error shell-error" role="alert">{{ logoutError }}</p>
    <main class="app-content">
      <slot />
    </main>
  </div>
</template>
