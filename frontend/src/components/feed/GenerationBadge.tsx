import { Badge } from '../ui/Badge'

export function GenerationBadge({ status }: { status: string }) {
  switch (status) {
    case 'ready':       return <Badge intent="success">Documents ready</Badge>
    case 'generating':
    case 'pending':     return <Badge intent="warning">Generating…</Badge>
    case 'failed':      return <Badge intent="danger">Generation failed</Badge>
    default:            return null
  }
}
