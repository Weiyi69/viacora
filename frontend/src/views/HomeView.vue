<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useQuery } from '@tanstack/vue-query'
import {
  Check,
  CircleHelp,
  Database,
  GraduationCap,
  Layers3,
  LoaderCircle,
  Server,
  Sparkles,
} from '@lucide/vue'
import { useAuthStore } from '../stores/auth'

type HealthStatus = {
  status: 'ok'
}

const auth = useAuthStore()
const router = useRouter()
const logoutError = ref('')
const loggingOut = ref(false)

async function fetchHealth(path: string): Promise<HealthStatus> {
  const response = await fetch(path)

  if (!response.ok) {
    throw new Error(`Health check returned ${response.status}`)
  }

  return response.json() as Promise<HealthStatus>
}

async function logout(): Promise<void> {
  logoutError.value = ''
  loggingOut.value = true
  try {
    await auth.logout()
    await router.push('/login')
  } catch {
    logoutError.value = 'Unable to sign out right now. Please try again.'
  } finally {
    loggingOut.value = false
  }
}

const apiHealth = useQuery({
  queryKey: ['health', 'api'],
  queryFn: () => fetchHealth('/api/v1/health'),
  refetchInterval: 30_000,
  retry: 1,
})

const databaseHealth = useQuery({
  queryKey: ['health', 'database'],
  queryFn: () => fetchHealth('/api/v1/health/database'),
  refetchInterval: 30_000,
  retry: 1,
})
</script>

<template>
  <main class="shell">
    <header class="topbar">
      <RouterLink class="brand" to="/" aria-label="Viacora home">
        <img class="brand-logo" :src="'/images/logo.png'" alt="Viacora" />
      </RouterLink>
      <span class="topbar-label">Your learning space</span>
      <button class="text-button" type="button" :disabled="loggingOut" @click="logout">
        {{ loggingOut ? 'Signing out…' : 'Sign out' }}
      </button>
    </header>

    <p v-if="logoutError" class="form-error" role="alert">{{ logoutError }}</p>
    <section class="hero" aria-labelledby="welcome-title">
      <div class="hero-copy">
        <div class="eyebrow"><Sparkles :size="14" /> A little more you, in every lesson</div>
        <h1 id="welcome-title">Make room for what you want to learn.</h1>
        <p>
          Your personal space for turning ideas into understanding. A calmer,
          more intentional way to make progress at your own pace.
        </p>
      </div>
      <aside class="welcome-card" aria-label="Learning space introduction">
        <div class="welcome-icon"><GraduationCap :size="22" /></div>
        <h2>Your next chapter starts here</h2>
        <p>Bring your notes, a curious question, or simply a little time to focus.</p>
      </aside>
    </section>

    <section aria-labelledby="services-title">
      <div class="section-heading">
        <h2 id="services-title">Your foundation</h2>
        <p>Everything is getting ready for you.</p>
      </div>

      <div class="status-grid" aria-live="polite">
        <article class="status-card">
          <div class="status-icon" :class="{ ready: apiHealth.isSuccess, failed: apiHealth.isError }">
            <Server :size="19" />
          </div>
          <div class="status-copy">
            <h3>Learning space</h3>
            <p>Viacora application service</p>
          </div>
          <span class="status-pill" :class="{ ready: apiHealth.isSuccess, failed: apiHealth.isError }">
            <LoaderCircle v-if="apiHealth.isPending" :size="13" class="spin" />
            <Check v-else-if="apiHealth.isSuccess" :size="13" />
            <CircleHelp v-else :size="13" />
            {{ apiHealth.isPending ? 'Checking' : apiHealth.isSuccess ? 'Connected' : 'Unavailable' }}
          </span>
        </article>

        <article class="status-card">
          <div
            class="status-icon"
            :class="{ ready: databaseHealth.isSuccess, failed: databaseHealth.isError }"
          >
            <Database :size="19" />
          </div>
          <div class="status-copy">
            <h3>Your library</h3>
            <p>Private, local learning data</p>
          </div>
          <span
            class="status-pill"
            :class="{ ready: databaseHealth.isSuccess, failed: databaseHealth.isError }"
          >
            <LoaderCircle v-if="databaseHealth.isPending" :size="13" class="spin" />
            <Check v-else-if="databaseHealth.isSuccess" :size="13" />
            <CircleHelp v-else :size="13" />
            {{
              databaseHealth.isPending
                ? 'Checking'
                : databaseHealth.isSuccess
                  ? 'Connected'
                  : 'Unavailable'
            }}
          </span>
        </article>
      </div>
    </section>

    <p class="foundation-note">
      <Layers3 :size="15" />
      Your foundation is in place. Summaries, flashcards, and guided learning are coming next.
    </p>
  </main>
</template>

<style scoped>
.spin {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
