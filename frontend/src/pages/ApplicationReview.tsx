import { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api, Document } from '../api/client'

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

export default function ApplicationReview() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [activeTab, setActiveTab] = useState(0)

  const { data: app, isLoading } = useQuery({
    queryKey: ['application', id],
    queryFn: () => api.getApplication(id!),
    refetchInterval: (data) =>
      data?.state?.data?.generation_status === 'generating' ? 3000 : false,
  })

  const approve = useMutation({
    mutationFn: () => api.reviewApplication(id!, 'approved'),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['applications'] })
      navigate('/applied')
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
      if (result.method === 'manual' && result.apply_url) {
        window.open(result.apply_url, '_blank')
        api.reviewApplication(id!, 'applied')
      }
      qc.invalidateQueries({ queryKey: ['applications'] })
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
  const isGenerating = app.generation_status === 'generating'
  const isFailed = app.generation_status === 'failed'

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
            <button
              onClick={() => approve.mutate()}
              disabled={approve.isPending || isGenerating}
              className="px-3 py-1.5 text-sm font-medium bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:opacity-50"
            >
              Approve
            </button>
            <button
              onClick={() => submit.mutate()}
              disabled={submit.isPending || isGenerating || !docs.length}
              className="px-3 py-1.5 text-sm font-medium bg-green-600 text-white rounded-md hover:bg-green-700 disabled:opacity-50"
            >
              {submit.isPending ? 'Submitting...' : 'Apply'}
            </button>
          </div>
        </div>

        {app.match_score != null && (
          <div className="mt-4 p-3 bg-gray-50 rounded-md text-sm text-gray-700">
            <span className="font-medium">{Math.round(app.match_score * 100)}% match: </span>
            {app.match_rationale}
          </div>
        )}
      </div>

      {/* Generation status */}
      {isGenerating && (
        <div className="mb-4 p-3 bg-blue-50 text-blue-700 text-sm rounded-md animate-pulse">
          Generating tailored documents...
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

      {!isGenerating && !docs.length && (
        <div className="text-center py-12 text-gray-400">
          <p>No documents generated yet</p>
          <button
            onClick={() => regen.mutate()}
            className="mt-2 text-sm text-blue-600 hover:underline"
          >
            Generate now
          </button>
        </div>
      )}

      {/* Submit result */}
      {submit.data && (
        <div className={`mt-4 p-3 text-sm rounded-md ${
          submit.data.success ? 'bg-green-50 text-green-700' : 'bg-amber-50 text-amber-700'
        }`}>
          {submit.data.success
            ? 'Application submitted successfully!'
            : submit.data.method === 'manual'
            ? 'Opened apply URL in new tab (manual application required)'
            : `Submit failed: ${submit.data.error}`}
        </div>
      )}
    </div>
  )
}
