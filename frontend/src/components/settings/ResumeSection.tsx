import { useRef } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import { Button } from '../ui/Button'
import { useToast } from '../ui/Toast'

export interface ResumeSectionProps {
  hasResume: boolean
}

export function ResumeSection({ hasResume }: ResumeSectionProps) {
  const qc = useQueryClient()
  const { show } = useToast()
  const fileRef = useRef<HTMLInputElement>(null)

  const upload = useMutation({
    mutationFn: (file: File) => api.uploadResume(file),
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: ['profile'] })
      if (result.extraction_status === 'llm_error') {
        show("Resume saved, but the AI is unavailable right now — edit your profile manually.", 'error')
      } else if (result.extraction_status === 'parse_error') {
        show("Resume saved but couldn't be parsed — try a plain-text or clearly-formatted PDF.", 'error')
      } else {
        show('Resume uploaded', 'success')
      }
    },
    onError: (e) => show((e as Error)?.message ?? 'Upload failed', 'error'),
  })

  return (
    <section className="mb-6">
      <h2 className="text-xs uppercase tracking-wider font-bold text-muted mb-2">Resume</h2>
      <div className="bg-surface border border-border rounded-lg-token p-4 flex items-center justify-between">
        <p className="text-sm text-text">
          {hasResume ? 'Resume on file' : 'No resume on file'}
        </p>
        <input
          ref={fileRef}
          data-testid="resume-file-input"
          type="file"
          accept=".pdf,.docx,.txt,.md"
          className="hidden"
          onChange={(e) => e.target.files?.[0] && upload.mutate(e.target.files[0])}
        />
        <Button
          size="sm"
          variant="secondary"
          pending={upload.isPending}
          onClick={() => fileRef.current?.click()}
        >
          {hasResume ? 'Re-upload' : 'Upload resume'}
        </Button>
      </div>
    </section>
  )
}
