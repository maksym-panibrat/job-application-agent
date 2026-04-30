import { useState, useEffect, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api, ApplicationDetail, Document } from '../api/client'

function JobDetails({ app }: { app: ApplicationDetail }) {
  const [open, setOpen] = useState(true)
  const job = app.job
  if (!job) return null

  const hasDetails = job.description_md || job.salary || job.contract_type || job.posted_at || app.match_strengths?.length || app.match_gaps?.length

  if (!hasDetails) return null

  return (
    <div className="mb-6 border border-gray-200 rounded-md text-sm">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center justify-between px-3 py-2 text-left font-medium text-gray-700 hover:bg-gray-50 rounded-md"
      >
        <span>Job Details</span>
        <span className="text-gray-400 text-xs">{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <div className="px-3 pb-3 border-t border-gray-100 pt-2 space-y-3">

          {(job.salary || job.contract_type || job.posted_at) && (
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-gray-500">
              {job.salary && <span>{job.salary}</span>}
              {job.contract_type && <span>{job.contract_type}</span>}
              {job.posted_at && (
                <span>Posted {new Date(job.posted_at).toLocaleDateString()}</span>
              )}
              <a
                href={job.apply_url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-blue-600 hover:underline"
              >
                Original posting ↗
              </a>
            </div>
          )}

          {(app.match_strengths?.length > 0 || app.match_gaps?.length > 0) && (
            <div className="flex gap-6">
              {app.match_strengths?.length > 0 && (
                <div>
                  <p className="text-xs font-semibold text-green-700 mb-1">Strengths</p>
                  <ul className="space-y-0.5 text-gray-600">
                    {app.match_strengths.map((s, i) => <li key={i}>· {s}</li>)}
                  </ul>
                </div>
              )}
              {app.match_gaps?.length > 0 && (
                <div>
                  <p className="text-xs font-semibold text-amber-700 mb-1">Gaps</p>
                  <ul className="space-y-0.5 text-gray-600">
                    {app.match_gaps.map((g, i) => <li key={i}>· {g}</li>)}
                  </ul>
                </div>
              )}
            </div>
          )}

          {job.description_md && (
            <div>
              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Description</p>
              <pre className="whitespace-pre-wrap font-sans text-gray-700 text-sm leading-relaxed max-h-96 overflow-y-auto">
                {job.description_md}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function DocumentEditor({
  doc,
  appId,
}: {
  doc: Document
  appId: string
}) {
  const [content, setContent] = useState(doc.content_md)
  const [saved, setSaved] = useState(false)
  const qc = useQueryClient()
  const baseContentRef = useRef(doc.content_md)
  useEffect(() => {
    if (doc.content_md !== baseContentRef.current) {
      if (content === baseContentRef.current) {
        setContent(doc.content_md)
      }
      baseContentRef.current = doc.content_md
    }
  }, [doc.content_md])

  const save = useMutation({
    mutationFn: () => api.updateDocument(appId, doc.id, { user_edited_md: content }),
    onSuccess: () => {
      setSaved(true)
      qc.invalidateQueries({ queryKey: ['application', appId] })
      setTimeout(() => setSaved(false), 2000)
    },
  })

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="text-xs text-gray-400">
          {doc.has_edits ? 'Edited' : 'AI-generated'} · {doc.generation_model ?? ''}
        </div>
        <div className="flex items-center gap-2">
          {saved && <span className="text-xs text-green-600">Saved</span>}
          <button
            onClick={() => save.mutate()}
            disabled={save.isPending || content === doc.content_md}
            className="px-3 py-1 text-xs font-medium bg-gray-100 hover:bg-gray-200 rounded disabled:opacity-40 transition-colors"
          >
            Save edits
          </button>
          <a
            href={api.downloadPdf(doc.id)}
            target="_blank"
            rel="noopener noreferrer"
            className="px-3 py-1 text-xs font-medium bg-gray-100 hover:bg-gray-200 rounded transition-colors"
          >
            PDF ↓
          </a>
        </div>
      </div>
      <textarea
        value={content}
        onChange={(e) => setContent(e.target.value)}
        className="w-full h-96 font-mono text-sm border border-gray-200 rounded-md p-3 focus:outline-none focus:ring-2 focus:ring-blue-500 resize-y"
        spellCheck={false}
      />
    </div>
  )
}

export default function ApplicationReview() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()

  const { data: app, isLoading } = useQuery({
    queryKey: ['application', id],
    queryFn: () => api.getApplication(id!),
  })

  const dismiss = useMutation({
    mutationFn: () => api.reviewApplication(id!, 'dismissed'),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['applications'] })
      navigate('/matches')
    },
  })

  const markApplied = useMutation({
    mutationFn: () => api.markApplied(id!),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['application', id] }),
  })

  const generateCoverLetter = useMutation({
    mutationFn: () => api.generateCoverLetter(id!),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['application', id] }),
  })

  if (isLoading || !app) {
    return <div className="flex items-center justify-center h-48 text-gray-400">Loading...</div>
  }

  const job = app.job
  const cover = app.documents?.find((d) => d.doc_type === 'cover_letter')
  const isFailed = app.generation_status === 'failed'

  return (
    <div className="max-w-4xl mx-auto">
      <div className="mb-6">
        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-xl font-bold text-gray-900">{job?.title}</h1>
            <p className="text-gray-600">{job?.company_name}</p>
            {job?.location && (
              <p className="text-sm text-gray-400">
                {job.location}
                {job.workplace_type && ` · ${job.workplace_type}`}
              </p>
            )}
          </div>
          <div className="flex gap-2 flex-wrap">
            <button
              onClick={() => dismiss.mutate()}
              disabled={dismiss.isPending}
              className="px-3 py-1.5 text-sm border border-gray-300 rounded-md hover:bg-gray-50 disabled:opacity-50"
            >
              Dismiss
            </button>
            <a
              href={app.job?.apply_url}
              target="_blank"
              rel="noopener noreferrer"
              className="px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 text-sm font-medium"
            >
              Open application ↗
            </a>
            <button
              onClick={() => markApplied.mutate()}
              disabled={app.status === 'applied' || markApplied.isPending}
              className="px-4 py-2 bg-green-600 text-white rounded-md hover:bg-green-700 disabled:opacity-50 text-sm font-medium"
            >
              {app.status === 'applied' ? 'Applied ✓' : 'Mark as applied'}
            </button>
          </div>
        </div>

        {app.match_score != null && (
          <div className="mt-4 p-3 bg-gray-50 rounded-md text-sm text-gray-700">
            <span className="font-medium">{Math.round(app.match_score * 100)}% match: </span>
            {app.match_summary}
          </div>
        )}
      </div>

      <JobDetails app={app} />

      {cover ? (
        <DocumentEditor doc={cover} appId={id!} />
      ) : (
        <div className="space-y-3">
          <button
            onClick={() => generateCoverLetter.mutate()}
            disabled={generateCoverLetter.isPending}
            className="px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:opacity-50 text-sm font-medium"
          >
            {generateCoverLetter.isPending ? 'Generating cover letter…' : 'Generate cover letter'}
          </button>
          {isFailed && (
            <p className="text-sm text-red-600">
              Last generation failed. Try again.
            </p>
          )}
          {generateCoverLetter.isError && (
            <p className="text-sm text-red-600">
              {(generateCoverLetter.error as Error)?.message ?? 'Generation failed.'}
            </p>
          )}
        </div>
      )}

      {app.applied_at && (
        <div className="mt-4 p-3 text-sm rounded-md bg-green-50 text-green-700">
          Applied {new Date(app.applied_at).toLocaleString()}
        </div>
      )}
    </div>
  )
}
