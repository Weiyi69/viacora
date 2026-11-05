<script setup lang="ts">
import { useQuery } from '@tanstack/vue-query'
import {
  Check,
  CircleHelp,
  Database,
  LoaderCircle,
  Server,
} from '@lucide/vue'
import AppShell from '../components/AppShell.vue'

type HealthStatus = { status: 'ok' }

async function fetchHealth(path: string): Promise<HealthStatus> {
  const response = await fetch(path)
  if (!response.ok) throw new Error(`Health check returned ${response.status}`)
  return response.json() as Promise<HealthStatus>
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
  <AppShell>
    <section class="page-heading">
      <div>
        <p class="eyebrow">System status</p>
        <h1>Service connections</h1>
        <p class="page-description">Live checks for the Viacora API and database.</p>
      </div>
    </section>
    <section class="status-grid" aria-live="polite">
      <article class="status-card">
        <div class="status-icon" :class="{ ready: apiHealth.isSuccess.value, failed: apiHealth.isError.value }">
          <Server :size="19" />
        </div>
        <div class="status-copy">
          <h2>Application service</h2>
          <p>Viacora API</p>
        </div>
        <span class="status-pill" :class="{ ready: apiHealth.isSuccess.value, failed: apiHealth.isError.value }">
          <LoaderCircle v-if="apiHealth.isPending.value" :size="13" class="spin" />
          <Check v-else-if="apiHealth.isSuccess.value" :size="13" />
          <CircleHelp v-else :size="13" />
          {{ apiHealth.isPending.value ? 'Checking' : apiHealth.isSuccess.value ? 'Connected' : 'Unavailable' }}
        </span>
      </article>
      <article class="status-card">
        <div class="status-icon" :class="{ ready: databaseHealth.isSuccess.value, failed: databaseHealth.isError.value }">
          <Database :size="19" />
        </div>
        <div class="status-copy">
          <h2>Database</h2>
          <p>PostgreSQL connection</p>
        </div>
        <span class="status-pill" :class="{ ready: databaseHealth.isSuccess.value, failed: databaseHealth.isError.value }">
          <LoaderCircle v-if="databaseHealth.isPending.value" :size="13" class="spin" />
          <Check v-else-if="databaseHealth.isSuccess.value" :size="13" />
          <CircleHelp v-else :size="13" />
          {{ databaseHealth.isPending.value ? 'Checking' : databaseHealth.isSuccess.value ? 'Connected' : 'Unavailable' }}
        </span>
      </article>
    </section>
  </AppShell>
</template>

<style scoped>
.spin {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}
</style>
