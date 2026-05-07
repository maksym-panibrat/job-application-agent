import { useState, useEffect } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import type { Document } from '../../api/client'
import { Button } from '../ui/Button'
import { TextArea } from '../ui/TextArea'
import { useToast } from '../ui/Toast'

export interface CoverLetterEditorProps {
  appId: string
  doc: Document | null
  status: string
}

export function CoverLetterEditor({ appId, doc, status }: CoverLetterEditorProps) {
  const qc = useQueryClient()
  const { show } = useToast()
  const [content, setContent] = useState(doc?.content_md ?? '')

  // Reset when the upstream doc changes (e.g. after generation succeeds).
  useEffect(() => { setContent(doc?.content_md ?? '') }, [doc?.content_md])

  const generate = useMutation({
    mutationFn: () => api.generateCoverLetter(appId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['application', appId] }),
    onError: (e) => show((e as Error)?.message ?? 'Generation failed', 'error'),
  })

  const save = useMutation({
    mutationFn: () => api.updateDocument(appId, doc!.id, { user_edited_md: content }),
    onSuccess: () => {
      show('Saved', 'success')
      qc.invalidateQueries({ queryKey: ['application', appId] })
    },
    onError: (e) => show((e as Error)?.message ?? 'Could not save edits', 'error'),
  })

  // Browser navigation via <a href> would 401 in production because the
  // PDF endpoint requires the JWT in the Authorization header. Fetch the
  // blob with auth, then trigger a client-side download.
  async function downloadPdf() {
    if (!doc) return
    try {
      const blob = await api.downloadPdfBlob(doc.id)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${doc.doc_type}.pdf`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
    } catch (e) {
      show((e as Error)?.message ?? 'Download failed', 'error')
    }
  }

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
          onClick={() => generate.mutate()}
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
          <Button size="sm" variant="ghost" onClick={downloadPdf}>PDF ↓</Button>
        </div>
      </div>
      <TextArea
        label="Cover letter"
        value={content}
        rows={12}
        onChange={(e) => setContent(e.target.value)}
        spellCheck={false}
      />
    </section>
  )
}
