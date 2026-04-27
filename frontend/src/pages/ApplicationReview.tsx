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

          {/* Meta row */}
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

          {/* Strengths / Gaps */}
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

          {/* Full description */}
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

function DocTab({
  label,
  active,
  onClick,
}: {
  label: string
  active: boolean
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
        active
          ? 'border-blue-600 text-blue-600'
          : 'border-transparent text-gray-500 hover:text-gray-700'
      }`}
    >
      {label}
    </button>
  )
}

function DocTypeLabel(type: string): string {
  return {
    tailored_resume: 'Resume',
    cover_letter: 'Cover Letter',
    custom_answers: 'Application Answers',
  }[type] ?? type
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
  // Track the last AI-generated content so we can reset when the doc is replaced
  // (e.g. after regeneration) without clobbering unsaved user edits.
  const baseContentRef = useRef(doc.content_md)
  useEffect(() => {
    if (doc.content_md !== baseContentRef.current) {
      // New AI content arrived — only reset if the user hasn't made edits
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

// Backend's /api/applications/{id}/status/stream gives up at ~300s
// (60 iterations × 5s sleep in app/api/applications.py::stream_generation_status).
// Frontend poll timeout is backend + 30s safety margin so we don't stop before
// the server does. If you bump one, bump the other in lockstep.
const GENERATION_POLL_TIMEOUT_MS = 330_000

export default function ApplicationReview() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [activeTab, setActiveTab] = useState(0)
  const [customAnswers, setCustomAnswers] = useState<Record<string, string>>({})
  const pollStartRef = useRef<number | null>(null)
  const [pollTimedOut, setPollTimedOut] = useState(false)

  const { data: app, isLoading } = useQuery({
    queryKey: ['application', id],
    queryFn: () => api.getApplication(id!),
    refetchInterval: (query) => {
      const status = query?.state?.data?.generation_status
      const isPolling = status === 'generating' || status === 'pending'
      if (!isPolling) {
        pollStartRef.current = null
        return false
      }
      if (pollStartRef.current === null) pollStartRef.current = Date.now()
      if (Date.now() - pollStartRef.current > GENERATION_POLL_TIMEOUT_MS) {
        setPollTimedOut(true)
        return false
      }
      return 3000
    },
  })

  // Initialise customAnswers from the custom_answers doc when it first arrives.
  // Track by doc ID (not object reference) so poll-cycle re-renders don't clobber edits.
  const customAnswersDoc = app?.documents?.find((d) => d.doc_type === 'custom_answers')
  const customAnswersDocRef = useRef<string | null>(null)
  useEffect(() => {
    const sc = customAnswersDoc?.structured_content
    if (customAnswersDoc?.id && customAnswersDoc.id !== customAnswersDocRef.current && sc && Object.keys(sc).length > 0) {
      customAnswersDocRef.current = customAnswersDoc.id
      setCustomAnswers(sc)
    }
  }, [customAnswersDoc?.id])

  const approve = useMutation({
    mutationFn: () => api.reviewApplication(id!, 'approved'),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['applications'] })
      qc.invalidateQueries({ queryKey: ['application', id] })
    },
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

  // In "awaiting_review" the graph is paused at an interrupt and the user must
  // approve or request regeneration. Approve drives the graph to END -> "ready"
  // (docs stay as-is); regenerate loops back through load_context and produces
  // fresh docs, pausing at the next review interrupt.
  const resumeApprove = useMutation({
    mutationFn: () => api.resumeApplication(id!, 'approve'),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['application', id] }),
  })
  const resumeRegenerate = useMutation({
    mutationFn: () => api.resumeApplication(id!, 'regenerate'),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['application', id] }),
  })

  // Full-reset regeneration used when the graph failed (no live checkpoint to resume).
  const regen = useMutation({
    mutationFn: () => api.regenerate(id!),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['application', id] }),
  })

  if (isLoading || !app) {
    return <div className="flex items-center justify-center h-48 text-gray-400">Loading...</div>
  }

  const job = app.job
  const docs = app.documents ?? []
  const isGenerating = app.generation_status === 'generating' || app.generation_status === 'pending'
  const isAwaitingReview = app.generation_status === 'awaiting_review'
  const isFailed = app.generation_status === 'failed'
  const hasCustomQuestions = Object.keys(customAnswers).length > 0
  // The regenerate path (either the awaiting-review resume or the hard reset) is
  // gated by the 3-attempt cap enforced server-side.
  const canRegenerate = app.generation_attempts < 3
  const regeneratePending = resumeRegenerate.isPending || regen.isPending

  return (
    <div className="max-w-4xl mx-auto">
      {/* Job header */}
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
            {app.status === 'pending_review' && (
              <button
                onClick={() => approve.mutate()}
                disabled={approve.isPending}
                className="px-3 py-1.5 text-sm font-medium bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:opacity-50"
              >
                {approve.isPending ? 'Approving...' : 'Approve'}
              </button>
            )}
            <div className="flex flex-col items-end gap-1">
              <div className="flex gap-2">
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
          </div>
        </div>

        {app.match_score != null && (
          <div className="mt-4 p-3 bg-gray-50 rounded-md text-sm text-gray-700">
            <span className="font-medium">{Math.round(app.match_score * 100)}% match: </span>
            {app.match_rationale}
          </div>
        )}
      </div>

      <JobDetails app={app} />

      {/* Approve prompt for pending_review with no docs */}
      {app.status === 'pending_review' && app.generation_status === 'none' && (
        <div className="mb-4 p-4 bg-gray-50 border border-gray-200 rounded-md text-sm text-gray-600">
          Click <strong>Approve</strong> to generate a tailored resume and cover letter for this job.
        </div>
      )}

      {/* Generation status */}
      {(isGenerating || app.generation_status === 'pending') && !pollTimedOut && (
        <div className="mb-4 p-3 bg-blue-50 text-blue-700 text-sm rounded-md animate-pulse">
          Generating tailored documents...
        </div>
      )}
      {pollTimedOut && isGenerating && (
        <div className="mb-4 p-3 bg-amber-50 text-amber-700 text-sm rounded-md flex items-center justify-between">
          <span>Generation is taking longer than expected. The server may still be working — refresh to check, or retry.</span>
          <button
            onClick={() => { setPollTimedOut(false); regen.mutate() }}
            disabled={regen.isPending || app.generation_attempts >= 3}
            className="text-sm font-medium underline disabled:opacity-50"
          >
            Retry
          </button>
        </div>
      )}
      {approve.isSuccess && app.generation_status === 'none' && (
        <div className="mb-4 p-3 bg-green-50 text-green-700 text-sm rounded-md">
          Approved. Generating documents now...
        </div>
      )}
      {isFailed && (
        <div className="mb-4 p-3 bg-red-50 text-red-700 text-sm rounded-md flex items-center justify-between">
          <span>Document generation failed</span>
          <button
            onClick={() => regen.mutate()}
            disabled={regen.isPending || !canRegenerate}
            className="text-sm font-medium underline disabled:opacity-50"
          >
            Retry
          </button>
        </div>
      )}
      {isAwaitingReview && (
        <div className="mb-4 p-3 bg-indigo-50 text-indigo-800 text-sm rounded-md flex items-center justify-between gap-3">
          <span>Documents generated — review and approve, or regenerate.</span>
          <div className="flex items-center gap-2">
            <button
              onClick={() => resumeRegenerate.mutate()}
              disabled={regeneratePending || !canRegenerate}
              className="px-3 py-1 text-xs font-medium bg-white border border-indigo-300 text-indigo-700 rounded hover:bg-indigo-100 disabled:opacity-50"
            >
              {resumeRegenerate.isPending ? 'Regenerating...' : 'Regenerate'}
            </button>
            <button
              onClick={() => resumeApprove.mutate()}
              disabled={resumeApprove.isPending}
              className="px-3 py-1 text-xs font-medium bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:opacity-50"
            >
              {resumeApprove.isPending ? 'Approving...' : 'Approve documents'}
            </button>
          </div>
        </div>
      )}

      {/* Document tabs */}
      {docs.length > 0 && (
        <div>
          <div className="flex border-b border-gray-200 mb-4">
            {docs.map((doc, i) => (
              <DocTab
                key={doc.id}
                label={DocTypeLabel(doc.doc_type)}
                active={activeTab === i}
                onClick={() => setActiveTab(i)}
              />
            ))}
          </div>
          {docs[activeTab] && (
            <DocumentEditor doc={docs[activeTab]} appId={id!} />
          )}
        </div>
      )}

      {/* Applied timestamp */}
      {app.applied_at && (
        <div className="mt-4 p-3 text-sm rounded-md bg-green-50 text-green-700">
          Applied {new Date(app.applied_at).toLocaleString()}
        </div>
      )}

      {/* Custom Questions */}
      {hasCustomQuestions && (
        <div className="mt-6">
          <h2 className="text-sm font-semibold text-gray-700 mb-3">Custom Questions</h2>
          <div className="space-y-4">
            {Object.entries(customAnswers).map(([question, answer]) => (
              <div key={question}>
                <div className="flex items-center gap-2 mb-1">
                  <label className="text-sm text-gray-600">{question}</label>
                  {!answer && (
                    <span className="px-1.5 py-0.5 text-xs font-medium bg-amber-100 text-amber-700 rounded">
                      Needs review
                    </span>
                  )}
                </div>
                <textarea
                  value={answer}
                  onChange={(e) => {
                    const updatedAnswers = { ...customAnswers, [question]: e.target.value }
                    setCustomAnswers(updatedAnswers)
                    if (customAnswersDoc) {
                      api.updateDocument(id!, customAnswersDoc.id, { structured_content: updatedAnswers })
                    }
                  }}
                  className="w-full h-24 text-sm border border-gray-200 rounded-md p-2 focus:outline-none focus:ring-2 focus:ring-blue-500 resize-y"
                  spellCheck
                />
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
