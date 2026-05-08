import { useApplyAction } from './useApplyAction'

export interface HeaderApplyButtonProps {
  appId: string
  status: string
  applyUrl: string
}

export function HeaderApplyButton({ appId, status, applyUrl }: HeaderApplyButtonProps) {
  const { onOpen, isApplied } = useApplyAction({ appId, status, applyUrl })

  if (status === 'dismissed') return null

  const label = isApplied ? 'Open posting again ↗' : 'Open posting ↗'
  const intentClass = isApplied
    ? 'bg-success/10 text-success border border-success/30'
    : 'bg-accent text-accent-fg'

  return (
    <a
      href={applyUrl}
      onClick={(e) => { e.preventDefault(); onOpen() }}
      className={`hidden md:inline-flex items-center justify-center font-semibold rounded-md-token px-3 py-1.5 text-sm min-h-[36px] ${intentClass}`}
    >
      {label}
    </a>
  )
}
