import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'

export default function AuthCallback() {
  const navigate = useNavigate()

  useEffect(() => {
    // Token is delivered in the URL fragment by the OAuth redirect transport.
    // Fragments aren't sent over HTTP, so the JWT stays out of access logs and
    // Referer headers. Fall back to query string for backward compatibility.
    const fragment = window.location.hash.startsWith('#') ? window.location.hash.slice(1) : ''
    const fragParams = new URLSearchParams(fragment)
    const queryParams = new URLSearchParams(window.location.search)
    const token = fragParams.get('access_token') ?? queryParams.get('access_token')
    if (token) {
      sessionStorage.setItem('access_token', token)
      navigate('/matches', { replace: true })
    } else {
      navigate('/', { replace: true })
    }
  }, [navigate])

  return <div className="flex items-center justify-center min-h-screen text-gray-500">Signing in...</div>
}
