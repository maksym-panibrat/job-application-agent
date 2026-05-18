import ReactMarkdown from 'react-markdown'

export function JobDescription({ content }: { content: string | null }) {
  if (!content || !content.trim()) return null
  return (
    <section className="mb-6">
      <div className="flex items-center gap-3 mb-2">
        <span className="flex-1 h-px bg-border" />
        <span className="text-xs uppercase tracking-wider font-bold text-muted">Job description</span>
        <span className="flex-1 h-px bg-border" />
      </div>
      <div className="text-sm text-text leading-relaxed">
        <ReactMarkdown
          components={{
            h1: ({ children }) => <h1 className="mt-5 mb-2 text-xl font-bold text-text first:mt-0">{children}</h1>,
            h2: ({ children }) => <h2 className="mt-5 mb-2 text-lg font-bold text-text first:mt-0">{children}</h2>,
            h3: ({ children }) => <h3 className="mt-4 mb-2 text-base font-bold text-text first:mt-0">{children}</h3>,
            p: ({ children }) => <p className="mb-3 last:mb-0">{children}</p>,
            ul: ({ children }) => <ul className="mb-3 ml-5 list-disc space-y-1 last:mb-0">{children}</ul>,
            ol: ({ children }) => <ol className="mb-3 ml-5 list-decimal space-y-1 last:mb-0">{children}</ol>,
            li: ({ children }) => <li className="pl-1">{children}</li>,
            strong: ({ children }) => <strong className="font-semibold text-text">{children}</strong>,
            a: ({ children, href }) => (
              <a className="font-semibold text-accent underline underline-offset-2" href={href} rel="noreferrer" target="_blank">
                {children}
              </a>
            ),
          }}
        >
          {content}
        </ReactMarkdown>
      </div>
    </section>
  )
}
