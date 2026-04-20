import { Routes, Route, NavLink } from 'react-router-dom'
import { useAuth, AuthProvider } from './context/AuthContext'
import BudgetBanner from './components/BudgetBanner'
import RequireAuth from './components/RequireAuth'
import Landing from './pages/Landing'
import AuthCallback from './pages/AuthCallback'
import Matches from './pages/Matches'
import ApplicationReview from './pages/ApplicationReview'
import Applied from './pages/Applied'
import Onboarding from './pages/Onboarding'

function NavItem({ to, label }: { to: string; label: string }) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        `px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${
          isActive
            ? 'bg-blue-100 text-blue-700'
            : 'text-gray-600 hover:text-gray-900 hover:bg-gray-100'
        }`
      }
    >
      {label}
    </NavLink>
  )
}

function AppShell() {
  const { user } = useAuth()
  return (
    <div className="min-h-screen bg-gray-50">
      <BudgetBanner />
      <nav className="bg-white border-b border-gray-200">
        <div className="max-w-5xl mx-auto px-4 py-3 flex items-center gap-6">
          <span className="font-bold text-gray-900 text-sm">Job Agent</span>
          {user && (
            <div className="flex items-center gap-1">
              <NavItem to="/matches" label="Matches" />
              <NavItem to="/applied" label="History" />
              <NavItem to="/profile" label="Profile" />
            </div>
          )}
        </div>
      </nav>
      <main className="max-w-5xl mx-auto px-4 py-6">
        <Routes>
          <Route path="/" element={<Landing />} />
          <Route path="/auth/callback" element={<AuthCallback />} />
          <Route path="/matches" element={<RequireAuth><Matches /></RequireAuth>} />
          <Route path="/matches/:id" element={<RequireAuth><ApplicationReview /></RequireAuth>} />
          <Route path="/applied" element={<RequireAuth><Applied /></RequireAuth>} />
          <Route path="/profile" element={<RequireAuth><Onboarding /></RequireAuth>} />
        </Routes>
      </main>
    </div>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <AppShell />
    </AuthProvider>
  )
}
