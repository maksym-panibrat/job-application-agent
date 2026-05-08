import { useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Drawer } from '../ui/Drawer'
import { Coach } from './Coach'
import { track } from '../../lib/track'

const PROMPT_BY_SLUG: Record<string, string> = {
  set_resume:    'Help me upload or describe my resume.',
  set_roles:     'What roles am I targeting?',
  set_locations: 'Where am I open to working? Any locations or remote-only?',
  set_keywords:  'What technologies / keywords matter most for my search?',
  change_profile: 'I want to change something in my profile.',
}

export function CoachDrawer() {
  const [params, setParams] = useSearchParams()
  const open = params.get('coach') === '1'
  const slug = params.get('prompt')
  const initialPrompt = slug ? PROMPT_BY_SLUG[slug] : undefined

  useEffect(() => {
    if (open) {
      track('coach.opened', { source: 'deep_link', prompt_slug: slug ?? null })
    }
  }, [open, slug])

  function close() {
    setParams((prev) => {
      const next = new URLSearchParams(prev)
      next.delete('coach')
      next.delete('prompt')
      return next
    }, { replace: true })
  }

  return (
    <Drawer open={open} onClose={close} title="Coach">
      <Coach initialPrompt={initialPrompt} />
    </Drawer>
  )
}
