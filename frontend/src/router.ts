import { createRouter, createWebHistory } from 'vue-router'
import ForgotPasswordView from './views/ForgotPasswordView.vue'
import DocumentDetailView from './views/DocumentDetailView.vue'
import DocumentsView from './views/DocumentsView.vue'
import HomeView from './views/HomeView.vue'
import LoginView from './views/LoginView.vue'
import RegisterView from './views/RegisterView.vue'
import ResetPasswordView from './views/ResetPasswordView.vue'
import StudySpaceDetailView from './views/StudySpaceDetailView.vue'
import SubjectDetailView from './views/SubjectDetailView.vue'
import SubjectsView from './views/SubjectsView.vue'
import SystemStatusView from './views/SystemStatusView.vue'
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
    { path: '/subjects', name: 'subjects', component: SubjectsView, meta: { requiresAuth: true } },
    { path: '/documents', name: 'documents', component: DocumentsView, meta: { requiresAuth: true } },
    {
      path: '/documents/:documentId',
      name: 'document-detail',
      component: DocumentDetailView,
      meta: { requiresAuth: true },
    },
    {
      path: '/subjects/:subjectId',
      name: 'subject-detail',
      component: SubjectDetailView,
      meta: { requiresAuth: true },
    },
    {
      path: '/study-spaces/:studySpaceId',
      name: 'study-space-detail',
      component: StudySpaceDetailView,
      meta: { requiresAuth: true },
    },
    {
      path: '/system-status',
      name: 'system-status',
      component: SystemStatusView,
      meta: { requiresAuth: true },
    },
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
