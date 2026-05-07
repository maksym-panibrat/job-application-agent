export function JobDescription({ content }: { content: string | null }) {
  if (!content || !content.trim()) return null
  return (
    <section className="mb-6">
      <div className="flex items-center gap-3 mb-2">
        <span className="flex-1 h-px bg-border" />
        <span className="text-xs uppercase tracking-wider font-bold text-muted">Job description</span>
        <span className="flex-1 h-px bg-border" />
      </div>
      <pre className="whitespace-pre-wrap font-sans text-sm text-text leading-relaxed">{content}</pre>
    </section>
  )
}
