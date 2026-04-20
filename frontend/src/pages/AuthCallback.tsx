import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'

export default function AuthCallback() {
  const navigate = useNavigate()

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const token = params.get('access_token')
    if (token) {
      sessionStorage.setItem('access_token', token)
      navigate('/matches', { replace: true })
    } else {
      navigate('/', { replace: true })
    }
  }, [navigate])

  return <div className="flex items-center justify-center min-h-screen text-gray-500">Signing in...</div>
}
