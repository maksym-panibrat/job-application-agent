import { useState, useEffect } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import type { Document } from '../../api/client'
import { Button } from '../ui/Button'
import { TextArea } from '../ui/TextArea'
import { useToast } from '../ui/Toast'
import { track } from '../../lib/track'

export interface CoverLetterEditorProps {
  appId: string
  doc: Document | null
  status: string
}

export function CoverLetterEditor({ appId, doc, status }: CoverLetterEditorProps) {
  const qc = useQueryClient()
  const { show } = useToast()
  const [content, setContent] = useState(doc?.content_md ?? '')
  const [editedTracked, setEditedTracked] = useState(false)

  // Reset when the upstream doc changes (e.g. after generation succeeds).
  useEffect(() => { setContent(doc?.content_md ?? '') }, [doc?.content_md])

  const generate = useMutation({
    mutationFn: () => api.generateCoverLetter(appId),
    onSuccess: (data) => {
      track('cover_letter.generation_succeeded', {
        application_id: appId, model: (data as { generation_model?: string | null } | undefined)?.generation_model ?? null,
      })
      qc.invalidateQueries({ queryKey: ['application', appId] })
    },
    onError: (e) => {
      track('cover_letter.generation_failed', { application_id: appId, reason: String(e) })
      show((e as Error)?.message ?? 'Generation failed', 'error')
    },
  })

  const save = useMutation({
    mutationFn: () => api.updateDocument(appId, doc!.id, { user_edited_md: content }),
    onSuccess: () => {
      track('cover_letter.saved', { application_id: appId, content_length: content.length })
      show('Saved', 'success')
      qc.invalidateQueries({ queryKey: ['application', appId] })
    },
    onError: (e) => show((e as Error)?.message ?? 'Could not save edits', 'error'),
  })

  if (!doc) {
    return (
      <section className="mb-6">
        <div className="flex items-center gap-3 mb-2">
          <span className="flex-1 h-px bg-border" />
          <span className="text-xs uppercase tracking-wider font-bold text-muted">Cover letter</span>
          <span className="flex-1 h-px bg-border" />
        </div>
        <Button
          pending={generate.isPending}
          onClick={() => { track('cover_letter.generation_clicked', { application_id: appId }); generate.mutate() }}
        >
          {generate.isPending ? 'Generating cover letter…' : 'Generate cover letter'}
        </Button>
        <p className="text-xs text-subtle mt-2">Takes about 30 seconds.</p>
        {status === 'failed' && !generate.isPending && (
          <p className="text-xs text-danger mt-2">Last attempt failed. Tap to try again.</p>
        )}
      </section>
    )
  }

  const dirty = content !== doc.content_md
  return (
    <section className="mb-6">
      <div className="flex items-center gap-3 mb-2">
        <span className="flex-1 h-px bg-border" />
        <span className="text-xs uppercase tracking-wider font-bold text-muted">Cover letter</span>
        <span className="flex-1 h-px bg-border" />
      </div>
      <div className="flex items-center justify-between mb-2 text-xs text-subtle">
        <span>{doc.has_edits ? 'Edited' : 'AI-generated'} · {doc.generation_model ?? ''}</span>
        <div className="flex gap-2">
          <Button size="sm" variant="ghost" disabled={!dirty || save.isPending} onClick={() => save.mutate()}>
            {save.isPending ? 'Saving…' : 'Save edits'}
          </Button>
          <a
            href={api.downloadPdf(doc.id)} target="_blank" rel="noopener noreferrer"
            onClick={() => track('cover_letter.pdf_downloaded', { application_id: appId })}
            className="inline-flex items-center px-3 py-1.5 rounded-md-token text-sm text-muted hover:text-text hover:bg-surface min-h-[32px]"
          >
            PDF ↓
          </a>
        </div>
      </div>
      <TextArea
        label="Cover letter"
        value={content}
        rows={12}
        onChange={(e) => {
          setContent(e.target.value)
          if (!editedTracked) {
            setEditedTracked(true)
            track('cover_letter.edited', { application_id: appId })
          }
        }}
        spellCheck={false}
      />
    </section>
  )
}
