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
    mutationFn: () => api.updateDocument(appId, doc.id, content),
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

function submissionMethodLabel(method: string | null): string {
  if (!method) return ''
  return {
    greenhouse_api: 'Submitted via Greenhouse API',
    lever_api: 'Submitted via Lever API',
    manual: 'Applied manually',
  }[method] ?? `Submitted (${method})`
}

export default function ApplicationReview() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [activeTab, setActiveTab] = useState(0)
  const [customAnswers, setCustomAnswers] = useState<Record<string, string>>({})

  const { data: app, isLoading } = useQuery({
    queryKey: ['application', id],
    queryFn: () => api.getApplication(id!),
    refetchInterval: (query) => {
      const status = query?.state?.data?.generation_status
      return (status === 'generating' || status === 'pending') ? 3000 : false
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

  const submit = useMutation({
    mutationFn: () => api.submitApplication(id!),
    onSuccess: (result) => {
      if (result.method === 'needs_review') return
      if (result.method === 'manual' && result.apply_url) {
        window.open(result.apply_url, '_blank')
        api.reviewApplication(id!, 'applied')
      }
      qc.invalidateQueries({ queryKey: ['applications'] })
      qc.invalidateQueries({ queryKey: ['application', id] })
    },
  })

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
  const isFailed = app.generation_status === 'failed'
  const hasCustomQuestions = Object.keys(customAnswers).length > 0
  const hasUnansweredCustomQuestions = hasCustomQuestions && Object.values(customAnswers).some((a) => !a)

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
              <button
                onClick={() => submit.mutate()}
                disabled={submit.isPending || isGenerating || !docs.length || hasUnansweredCustomQuestions || submit.data?.method === 'needs_review'}
                className="px-3 py-1.5 text-sm font-medium bg-green-600 text-white rounded-md hover:bg-green-700 disabled:opacity-50"
              >
                {submit.isPending ? 'Submitting...' : 'Apply'}
              </button>
              {hasUnansweredCustomQuestions && (
                <span className="text-xs text-amber-600">Answer all custom questions before applying</span>
              )}
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
      {(isGenerating || app.generation_status === 'pending') && (
        <div className="mb-4 p-3 bg-blue-50 text-blue-700 text-sm rounded-md animate-pulse">
          Generating tailored documents...
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
            disabled={regen.isPending || app.generation_attempts >= 3}
            className="text-sm font-medium underline disabled:opacity-50"
          >
            Retry
          </button>
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

      {/* Submit result */}
      {submit.data?.method === 'needs_review' && (
        <div className="mt-4 p-3 text-sm rounded-md bg-amber-50 text-amber-700">
          <p className="font-medium mb-1">Some questions need your answers before submitting:</p>
          <ul className="list-disc list-inside space-y-0.5">
            {submit.data.unanswered_questions?.map((q) => (
              <li key={q}>{q}</li>
            ))}
          </ul>
        </div>
      )}
      {submit.data && submit.data.method !== 'needs_review' && (
        <div className={`mt-4 p-3 text-sm rounded-md ${
          submit.data.success ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'
        }`}>
          {submit.data.success
            ? (submit.data.method === 'manual'
                ? 'Opened apply URL in new tab (manual application required)'
                : 'Application submitted successfully!')
            : `Submit failed: ${submit.data.error}`}
        </div>
      )}

      {/* Post-submit audit info */}
      {app.submitted_at && (
        <div className="mt-4 p-3 text-sm rounded-md bg-gray-50 text-gray-600">
          <span>{submissionMethodLabel(app.submission_method)}</span>
          {' · '}
          <span>{new Date(app.submitted_at).toLocaleString()}</span>
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
                  onChange={(e) =>
                    setCustomAnswers((prev) => ({ ...prev, [question]: e.target.value }))
                  }
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
