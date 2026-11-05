<script setup lang="ts">
import AppShell from '../components/AppShell.vue'
import DocumentList from '../components/DocumentList.vue'
</script>

<template>
  <AppShell>
    <section class="page-heading">
      <div>
        <p class="eyebrow">Learning materials</p>
        <h1 id="documents-heading">Documents</h1>
        <p class="page-description">Manage your private PDFs and their extracted text.</p>
      </div>
    </section>
    <DocumentList />
  </AppShell>
</template>
