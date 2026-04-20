import React, { createContext, useContext, useEffect, useState } from 'react'
import { api } from '../api/client'

interface User {
  id: string
  email: string
}

interface AuthContextType {
  user: User | null
  token: string | null
  loading: boolean
  signOut: () => void
}

const AuthContext = createContext<AuthContextType>({
  user: null,
  token: null,
  loading: true,
  signOut: () => {},
})

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [token, setToken] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const stored = sessionStorage.getItem('access_token')
    if (stored) {
      setToken(stored)
      api.getMe()
        .then(setUser)
        .catch(() => {
          sessionStorage.removeItem('access_token')
          setToken(null)
        })
        .finally(() => setLoading(false))
    } else {
      setLoading(false)
    }
  }, [])

  const signOut = () => {
    sessionStorage.removeItem('access_token')
    setToken(null)
    setUser(null)
    window.location.href = '/'
  }

  return (
    <AuthContext.Provider value={{ user, token, loading, signOut }}>
      {children}
    </AuthContext.Provider>
  )
}

export const useAuth = () => useContext(AuthContext)
