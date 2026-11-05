import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { api, type User } from '../api/client'

type SessionResponse = {
  authenticated: boolean
  user: User | null
}

type LoginResponse = {
  user: User
}

export const useAuthStore = defineStore('auth', () => {
  const user = ref<User | null>(null)
  const initialized = ref(false)
  const initializing = ref(false)
  const initializationFailed = ref(false)
  const isAuthenticated = computed(() => user.value !== null)
  let initialization: Promise<void> | null = null

  async function initialize(): Promise<void> {
    if (initialized.value) return
    if (initialization) return initialization
    initializing.value = true
    initialization = api
      .get<SessionResponse>('/auth/session')
      .then((session) => {
        user.value = session.authenticated ? session.user : null
        initialized.value = true
      })
      .catch((error: unknown) => {
        initializationFailed.value = true
        throw error
      })
      .finally(() => {
        initializing.value = false
        initialization = null
      })
    return initialization
  }

  async function login(email: string, password: string): Promise<void> {
    const response = await api.post<LoginResponse>('/auth/login', { email, password })
    user.value = response.user
    initialized.value = true
  }

  async function register(
    email: string,
    password: string,
    displayName: string,
  ): Promise<void> {
    await api.post('/auth/register', {
      email,
      password,
      display_name: displayName,
    })
  }

  async function logout(): Promise<void> {
    await api.post('/auth/logout')
    user.value = null
    initialized.value = true
  }

  async function updateDisplayName(displayName: string): Promise<void> {
    const response = await api.patch<User>('/users/me', {
      display_name: displayName,
    })
    user.value = response
  }

  return {
    user,
    initialized,
    initializing,
    initializationFailed,
    isAuthenticated,
    initialize,
    login,
    register,
    logout,
    updateDisplayName,
  }
})
