import { useEffect, useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import { Button } from '../ui/Button'
import { useToast } from '../ui/Toast'
import { track } from '../../lib/track'

interface Message {
  role: 'user' | 'assistant'
  content: string
  /** True when the agent indicated it mutated the profile during this turn. */
  profileMutated?: boolean
  error?: boolean
}

export interface ChatProps {
  /** Pre-fills the composer (does not auto-send). Used by deep links from
   *  the ProfileCompletenessCard's 'Open chat →' rows. */
  initialPrompt?: string
}

export function Chat({ initialPrompt }: ChatProps) {
  const qc = useQueryClient()
  const { show } = useToast()
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState(initialPrompt ?? '')
  const [sending, setSending] = useState(false)
  const [uploading, setUploading] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const triggerSync = useMutation({
    mutationFn: api.triggerSync,
    onSuccess: () => {
      show('Searching now', 'success')
      qc.invalidateQueries({ queryKey: ['applications'] })
      qc.invalidateQueries({ queryKey: ['profile'] })
    },
    onError: (e) => show((e as Error)?.message ?? 'Sync failed', 'error'),
  })

  async function send(text: string) {
    if (!text.trim() || sending) return
    track('chat.message_sent', { length: text.length })
    setInput('')
    setSending(true)
    setMessages((prev) => [...prev, { role: 'user', content: text }])
    setMessages((prev) => [...prev, { role: 'assistant', content: '' }])

    try {
      await api.sendMessage(
        text,
        (chunk) => {
          setMessages((prev) => {
            const out = [...prev]
            const last = out[out.length - 1]
            out[out.length - 1] = { ...last, content: last.content + chunk }
            return out
          })
        },
        () => {
          track('chat.message_failed', { reason: 'stream_error' })
          setMessages((prev) => {
            const out = [...prev]
            out[out.length - 1] = {
              role: 'assistant',
              content: 'Something went wrong — please try again.',
              error: true,
            }
            return out
          })
        },
        (meta) => {
          if (meta.profile_mutated) {
            setMessages((prev) => {
              const out = [...prev]
              out[out.length - 1] = { ...out[out.length - 1], profileMutated: true }
              return out
            })
            qc.invalidateQueries({ queryKey: ['profile'] })
          }
        },
      )
    } finally {
      setSending(false)
    }
  }

  async function onUpload(file: File) {
    setUploading(true)
    try {
      const result = await api.uploadResume(file)
      qc.invalidateQueries({ queryKey: ['profile'] })
      // Resume upload is unambiguous intent → trigger sync silently.
      triggerSync.mutate()
      if (result.extraction_status === 'llm_error') {
        show("Resume saved, but the AI is unavailable right now — edit your profile manually.", 'error')
      } else if (result.extraction_status === 'parse_error') {
        show("Resume saved but couldn't be parsed — try a plain-text or clearly-formatted PDF.", 'error')
      } else {
        show('Resume uploaded', 'success')
      }
      // Follow up with a chat message so the agent can verify and ask for any missing pieces.
      await send("I've uploaded my resume. Please review it and help me complete my profile.")
    } catch (err) {
      show(err instanceof Error ? err.message : 'Upload failed — try again', 'error')
    } finally {
      setUploading(false)
    }
  }

  return (
    <div className="flex flex-col h-full">
      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {messages.length === 0 && (
          <div className="text-center text-sm text-muted py-8">
            <p>Upload your resume or describe what you're looking for.</p>
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[85%] px-3 py-2 rounded-lg-token text-sm whitespace-pre-wrap ${
              m.role === 'user'
                ? 'bg-accent text-accent-fg rounded-br-sm'
                : m.error
                ? 'bg-danger/10 text-danger rounded-bl-sm'
                : 'bg-surface-2 text-text rounded-bl-sm'
            }`}>
              {m.content || (sending && i === messages.length - 1 ? '…' : '')}
              {m.role === 'assistant' && m.profileMutated && (
                <div className="mt-2 pt-2 border-t border-border">
                  <Button
                    size="sm"
                    pending={triggerSync.isPending}
                    onClick={() => { track('chat.search_now_clicked'); triggerSync.mutate() }}
                  >
                    ✦ Search now
                  </Button>
                </div>
              )}
            </div>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
      <div className="border-t border-border p-3 flex gap-2">
        <input
          ref={fileRef}
          type="file"
          accept=".pdf,.docx,.txt,.md"
          className="hidden"
          onChange={(e) => e.target.files?.[0] && onUpload(e.target.files[0])}
        />
        <button
          type="button"
          onClick={() => fileRef.current?.click()}
          disabled={uploading}
          className="px-3 py-2 text-sm text-muted border border-border-strong rounded-md-token hover:bg-surface min-h-[40px] disabled:opacity-50"
        >
          {uploading ? 'Uploading…' : 'Resume'}
        </button>
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(input) } }}
          placeholder="Type your message…"
          disabled={sending}
          className="flex-1 bg-surface text-text border border-border rounded-md-token px-3 py-2 text-sm min-h-[40px] focus:outline-2 focus:outline-accent/40 focus:outline-offset-2 focus:border-accent"
        />
        <Button onClick={() => send(input)} pending={sending} disabled={!input.trim()}>Send</Button>
      </div>
    </div>
  )
}
