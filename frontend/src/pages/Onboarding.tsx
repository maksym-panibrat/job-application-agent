import { useState, useRef, useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

interface Message {
  role: 'user' | 'assistant'
  content: string
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
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: `Resume uploaded! I've parsed your resume. Tell me about your target roles and any specific preferences.`,
        },
      ])
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

      {/* Profile summary */}
      {profile && (profile.base_resume_md || profile.target_roles.length > 0) && (
        <div className="mb-4 p-3 bg-gray-50 rounded-md text-sm">
          <p className="font-medium text-gray-700">Current profile</p>
          <p className="text-gray-500 mt-0.5">
            {profile.full_name && <span>{profile.full_name} · </span>}
            {profile.target_roles.length > 0 && (
              <span>{profile.target_roles.join(', ')}</span>
            )}
            {profile.seniority && <span> · {profile.seniority}</span>}
            {profile.base_resume_md && <span> · Resume uploaded</span>}
          </p>
        </div>
      )}

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
