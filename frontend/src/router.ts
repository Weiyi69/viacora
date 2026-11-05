import { createRouter, createWebHistory } from 'vue-router'
import ForgotPasswordView from './views/ForgotPasswordView.vue'
import HomeView from './views/HomeView.vue'
import LoginView from './views/LoginView.vue'
import RegisterView from './views/RegisterView.vue'
import ResetPasswordView from './views/ResetPasswordView.vue'
import VerifyEmailView from './views/VerifyEmailView.vue'
import { useAuthStore } from './stores/auth'

declare module 'vue-router' {
  interface RouteMeta {
    requiresAuth?: boolean
    guestOnly?: boolean
  }
}

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: HomeView, meta: { requiresAuth: true } },
    { path: '/login', name: 'login', component: LoginView, meta: { guestOnly: true } },
    { path: '/register', name: 'register', component: RegisterView, meta: { guestOnly: true } },
    { path: '/forgot-password', name: 'forgot-password', component: ForgotPasswordView },
    { path: '/reset-password', name: 'reset-password', component: ResetPasswordView },
    { path: '/verify-email', name: 'verify-email', component: VerifyEmailView },
  ],
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  if (to.meta.requiresAuth || to.meta.guestOnly) {
    try {
      await auth.initialize()
    } catch {
      if (to.meta.requiresAuth) return { name: 'login', query: { serviceUnavailable: '1' } }
      return true
    }
  }
  if (to.meta.requiresAuth && !auth.isAuthenticated) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (to.meta.requiresAuth && auth.user && !auth.user.email_verified) {
    return { name: 'verify-email' }
  }
  if (to.meta.guestOnly && auth.user?.email_verified) {
    return { name: 'home' }
  }
  return true
})
