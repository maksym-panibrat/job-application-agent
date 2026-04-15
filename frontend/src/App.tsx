import { Routes, Route, NavLink, Navigate } from 'react-router-dom'
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

export default function App() {
  return (
    <div className="min-h-screen bg-gray-50">
      <nav className="bg-white border-b border-gray-200">
        <div className="max-w-5xl mx-auto px-4 py-3 flex items-center gap-6">
          <span className="font-bold text-gray-900 text-sm">Job Agent</span>
          <div className="flex items-center gap-1">
            <NavItem to="/matches" label="Matches" />
            <NavItem to="/applied" label="History" />
            <NavItem to="/profile" label="Profile" />
          </div>
        </div>
      </nav>
      <main className="max-w-5xl mx-auto px-4 py-6">
        <Routes>
          <Route path="/" element={<Navigate to="/matches" replace />} />
          <Route path="/matches" element={<Matches />} />
          <Route path="/matches/:id" element={<ApplicationReview />} />
          <Route path="/applied" element={<Applied />} />
          <Route path="/profile" element={<Onboarding />} />
        </Routes>
      </main>
    </div>
  )
}
