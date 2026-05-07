import { Chip } from '../ui/Chip'
import { useStatusFilter, StatusFilter } from '../../lib/useStatusFilter'

export interface StatusCounts {
  pending: number
  applied: number
  dismissed: number
}

export interface StatusChipsProps {
  counts: StatusCounts
}

const ITEMS: { value: StatusFilter; label: string }[] = [
  { value: 'pending',   label: 'Pending' },
  { value: 'applied',   label: 'Applied' },
  { value: 'dismissed', label: 'Dismissed' },
]

export function StatusChips({ counts }: StatusChipsProps) {
  const { status, setStatus } = useStatusFilter()
  return (
    <div className="flex gap-2 flex-wrap">
      {ITEMS.map(({ value, label }) => (
        <Chip
          key={value}
          selected={status === value}
          count={counts[value]}
          onClick={() => setStatus(value)}
        >
          {label}
        </Chip>
      ))}
    </div>
  )
}
