import { Routes, Route } from 'react-router-dom'
import { AuthProvider } from './context/AuthContext'
import { ToastProvider } from './components/ui/Toast'
import { AppShell } from './components/AppShell'
import BudgetBanner from './components/BudgetBanner'
import RequireAuth from './components/RequireAuth'
import Landing from './pages/Landing'
import AuthCallback from './pages/AuthCallback'
import Matches from './pages/Matches'
import ApplicationReview from './pages/ApplicationReview'
import Applied from './pages/Applied'
import Onboarding from './pages/Onboarding'

function ShellRoutes() {
  return (
    <>
      <BudgetBanner />
      <AppShell>
        <Routes>
          <Route path="/" element={<Landing />} />
          <Route path="/auth/callback" element={<AuthCallback />} />
          <Route path="/matches" element={<RequireAuth><Matches /></RequireAuth>} />
          <Route path="/matches/:id" element={<RequireAuth><ApplicationReview /></RequireAuth>} />
          <Route path="/applied" element={<RequireAuth><Applied /></RequireAuth>} />
          <Route path="/profile" element={<RequireAuth><Onboarding /></RequireAuth>} />
          <Route path="/login" element={<Landing />} />
        </Routes>
      </AppShell>
    </>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <ToastProvider>
        <ShellRoutes />
      </ToastProvider>
    </AuthProvider>
  )
}
