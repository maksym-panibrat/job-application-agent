import { useState, useRef, useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api, Profile } from '../api/client'

interface Message {
  role: 'user' | 'assistant'
  content: string
}

function ProfileCard({ profile }: { profile: Profile }) {
  const [open, setOpen] = useState(false)

  const hasContact = profile.email || profile.phone || profile.linkedin_url || profile.github_url || profile.portfolio_url
  const hasPrefs = profile.target_roles.length > 0 || profile.target_locations.length > 0 || profile.seniority || profile.search_keywords.length > 0
  const hasSkills = profile.skills.length > 0
  const hasExperience = profile.work_experiences.length > 0

  if (!hasContact && !hasPrefs && !hasSkills && !hasExperience && !profile.base_resume_md) {
    return null
  }

  return (
    <div className="mb-4 border border-gray-200 rounded-md text-sm">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center justify-between px-3 py-2 text-left font-medium text-gray-700 hover:bg-gray-50 rounded-md"
      >
        <span>Current profile{profile.full_name ? ` · ${profile.full_name}` : ''}</span>
        <span className="text-gray-400 text-xs">{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <div className="px-3 pb-3 space-y-3 border-t border-gray-100 pt-2 max-h-60 overflow-y-auto">

          {/* Contact */}
          {hasContact && (
            <div>
              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Contact</p>
              <div className="space-y-0.5 text-gray-600">
                {profile.email && <p>{profile.email}</p>}
                {profile.phone && <p>{profile.phone}</p>}
                {profile.linkedin_url && (
                  <p><a href={profile.linkedin_url} target="_blank" rel="noopener noreferrer" className="text-blue-600 hover:underline">LinkedIn</a></p>
                )}
                {profile.github_url && (
                  <p><a href={profile.github_url} target="_blank" rel="noopener noreferrer" className="text-blue-600 hover:underline">GitHub</a></p>
                )}
                {profile.portfolio_url && (
                  <p><a href={profile.portfolio_url} target="_blank" rel="noopener noreferrer" className="text-blue-600 hover:underline">Portfolio</a></p>
                )}
              </div>
            </div>
          )}

          {/* Preferences */}
          {hasPrefs && (
            <div>
              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Preferences</p>
              <div className="space-y-0.5 text-gray-600">
                {profile.target_roles.length > 0 && (
                  <p><span className="text-gray-400">Roles: </span>{profile.target_roles.join(', ')}</p>
                )}
                {profile.seniority && (
                  <p><span className="text-gray-400">Level: </span>{profile.seniority}</p>
                )}
                {profile.target_locations.length > 0 && (
                  <p><span className="text-gray-400">Locations: </span>{profile.target_locations.join(', ')}</p>
                )}
                {(profile.remote_ok !== undefined) && (
                  <p><span className="text-gray-400">Remote: </span>{profile.remote_ok ? 'Yes' : 'No'}</p>
                )}
                {profile.search_keywords.length > 0 && (
                  <p><span className="text-gray-400">Keywords: </span>{profile.search_keywords.join(', ')}</p>
                )}
              </div>
            </div>
          )}

          {/* Search status */}
          {(profile.search_active !== undefined) && (
            <div>
              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Search</p>
              <p className="text-gray-600">
                {profile.search_active ? 'Active' : 'Paused'}
                {profile.search_expires_at && (
                  <span className="text-gray-400"> · expires {new Date(profile.search_expires_at).toLocaleDateString()}</span>
                )}
              </p>
            </div>
          )}

          {/* Resume */}
          {profile.base_resume_md && (
            <div>
              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Resume</p>
              <p className="text-gray-600">Uploaded</p>
            </div>
          )}

          {/* Skills */}
          {hasSkills && (
            <div>
              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Skills</p>
              <div className="flex flex-wrap gap-1">
                {profile.skills.map((s) => (
                  <span key={s.id} className="px-2 py-0.5 bg-gray-100 rounded text-gray-700 text-xs">
                    {s.name}
                    {s.proficiency && <span className="text-gray-400"> · {s.proficiency}</span>}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Work experience */}
          {hasExperience && (
            <div>
              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Experience</p>
              <div className="space-y-1.5">
                {profile.work_experiences.map((w) => (
                  <div key={w.id}>
                    <p className="font-medium text-gray-700">{w.title} <span className="font-normal text-gray-500">at {w.company}</span></p>
                    <p className="text-gray-400">
                      {w.start_date}–{w.end_date ?? 'present'}
                      {w.technologies.length > 0 && <span> · {w.technologies.slice(0, 5).join(', ')}</span>}
                    </p>
                  </div>
                ))}
              </div>
            </div>
          )}

        </div>
      )}
    </div>
  )
}

export default function Onboarding() {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [uploading, setUploading] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const { data: profile, refetch: refetchProfile } = useQuery({
    queryKey: ['profile'],
    queryFn: api.getProfile,
  })

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const sendMessage = async () => {
    if (!input.trim() || sending) return
    const userMsg = input.trim()
    setInput('')
    setSending(true)

    setMessages((prev) => [...prev, { role: 'user', content: userMsg }])
    setMessages((prev) => [...prev, { role: 'assistant', content: '' }])

    await api.sendMessage(userMsg, (chunk) => {
      setMessages((prev) => {
        const updated = [...prev]
        updated[updated.length - 1] = {
          ...updated[updated.length - 1],
          content: updated[updated.length - 1].content + chunk,
        }
        return updated
      })
    })

    setSending(false)
    refetchProfile()
  }

  const handleUpload = async (file: File) => {
    setUploading(true)
    try {
      await api.uploadResume(file)
      refetchProfile()
      // Send resume upload notification to the agent so it can read and reference the resume
      const userMsg = "I've uploaded my resume. Please review it and help me complete my profile."
      setInput('')
      setSending(true)
      setMessages((prev) => [
        ...prev,
        { role: 'user', content: userMsg },
        { role: 'assistant', content: '' },
      ])
      await api.sendMessage(userMsg, (chunk) => {
        setMessages((prev) => {
          const updated = [...prev]
          updated[updated.length - 1] = {
            ...updated[updated.length - 1],
            content: updated[updated.length - 1].content + chunk,
          }
          return updated
        })
      })
      setSending(false)
      refetchProfile()
    } finally {
      setUploading(false)
    }
  }

  return (
    <div className="max-w-2xl mx-auto flex flex-col h-[calc(100vh-8rem)]">
      <div className="mb-4">
        <h1 className="text-xl font-bold text-gray-900">Profile Setup</h1>
        <p className="text-sm text-gray-500 mt-0.5">
          Chat to set your job preferences or upload your resume to get started.
        </p>
      </div>

      {profile && <ProfileCard profile={profile} />}

      {/* Messages */}
      <div className="flex-1 overflow-y-auto space-y-3 pb-4">
        {messages.length === 0 && (
          <div className="text-center py-8 text-gray-400 text-sm">
            <p>Upload your resume or start chatting to build your profile.</p>
          </div>
        )}
        {messages.map((msg, i) => (
          <div
            key={i}
            className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
          >
            <div
              className={`max-w-[85%] px-4 py-2.5 rounded-2xl text-sm whitespace-pre-wrap ${
                msg.role === 'user'
                  ? 'bg-blue-600 text-white rounded-br-sm'
                  : 'bg-gray-100 text-gray-800 rounded-bl-sm'
              }`}
            >
              {msg.content || (sending && i === messages.length - 1 ? '...' : '')}
            </div>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      {/* Input area */}
      <div className="border-t pt-3">
        <div className="flex gap-2">
          <input
            ref={fileRef}
            type="file"
            accept=".pdf,.docx,.txt,.md"
            className="hidden"
            onChange={(e) => e.target.files?.[0] && handleUpload(e.target.files[0])}
          />
          <button
            onClick={() => fileRef.current?.click()}
            disabled={uploading}
            className="px-3 py-2 text-sm border border-gray-300 rounded-md hover:bg-gray-50 disabled:opacity-50 whitespace-nowrap"
          >
            {uploading ? 'Uploading...' : 'Resume'}
          </button>
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && sendMessage()}
            placeholder="Type your preferences..."
            className="flex-1 px-3 py-2 text-sm border border-gray-200 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
            disabled={sending}
          />
          <button
            onClick={sendMessage}
            disabled={sending || !input.trim()}
            className="px-4 py-2 text-sm font-medium bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:opacity-50"
          >
            Send
          </button>
        </div>
      </div>
    </div>
  )
}
