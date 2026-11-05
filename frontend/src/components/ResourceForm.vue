<script setup lang="ts">
import { ref } from 'vue'
import type { ResourceInput } from '../api/learningTypes'

type ResourceType = 'subject' | 'study-space' | 'topic'

const props = withDefaults(
  defineProps<{
    resourceType: ResourceType
    initialName?: string
    initialDescription?: string | null
    initialColor?: string | null
    initialIcon?: string | null
    initialPosition?: number
    busy?: boolean
  }>(),
  {
    initialName: '',
    initialDescription: '',
    initialColor: 'blue',
    initialIcon: 'book',
    initialPosition: 0,
    busy: false,
  },
)

const emit = defineEmits<{
  save: [input: ResourceInput]
  cancel: []
}>()

const name = ref(props.initialName)
const description = ref(props.initialDescription ?? '')
const color = ref(props.initialColor ?? 'blue')
const icon = ref(props.initialIcon ?? 'book')
const position = ref(props.initialPosition)

function submit(): void {
  const input: ResourceInput = {
    name: name.value.trim(),
    description: description.value.trim() || null,
  }
  if (props.resourceType === 'subject') {
    input.color = color.value
    input.icon = icon.value
  }
  if (props.resourceType === 'topic') {
    input.position = Number(position.value)
  }
  emit('save', input)
}
</script>

<template>
  <form class="resource-form" @submit.prevent="submit">
    <label>
      Name
      <input v-model="name" required minlength="1" maxlength="120" autofocus />
    </label>
    <label>
      Description
      <textarea v-model="description" rows="3" maxlength="5000" />
    </label>
    <template v-if="resourceType === 'subject'">
      <label>
        Color
        <select v-model="color">
          <option value="blue">Blue</option>
          <option value="violet">Violet</option>
          <option value="teal">Teal</option>
          <option value="green">Green</option>
          <option value="amber">Amber</option>
          <option value="rose">Rose</option>
        </select>
      </label>
      <label>
        Icon
        <select v-model="icon">
          <option value="book">Book</option>
          <option value="flask">Flask</option>
          <option value="calculator">Calculator</option>
          <option value="globe">Globe</option>
          <option value="music">Music</option>
          <option value="code">Code</option>
          <option value="atom">Atom</option>
          <option value="language">Language</option>
          <option value="history">History</option>
        </select>
      </label>
    </template>
    <label v-if="resourceType === 'topic'">
      Order
      <input v-model.number="position" type="number" min="0" required />
    </label>
    <div class="dialog-actions">
      <button class="secondary-button" type="button" :disabled="busy" @click="emit('cancel')">
        Cancel
      </button>
      <button class="primary-button" type="submit" :disabled="busy || !name.trim()">
        {{ busy ? 'Saving…' : 'Save' }}
      </button>
    </div>
  </form>
</template>
