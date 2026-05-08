import { useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Drawer } from '../ui/Drawer'
import { Chat } from './Chat'
import { track } from '../../lib/track'

const PROMPT_BY_SLUG: Record<string, string> = {
  set_resume:    'Help me upload or describe my resume.',
  set_roles:     'What roles am I targeting?',
  set_locations: 'Where am I open to working? Any locations or remote-only?',
  set_keywords:  'What technologies / keywords matter most for my search?',
  change_profile: 'I want to change something in my profile.',
}

export function ChatDrawer() {
  const [params, setParams] = useSearchParams()
  const open = params.get('chat') === '1'
  const slug = params.get('prompt')
  const initialPrompt = slug ? PROMPT_BY_SLUG[slug] : undefined

  useEffect(() => {
    if (open) {
      track('chat.opened', { source: 'deep_link', prompt_slug: slug ?? null })
    }
  }, [open, slug])

  function close() {
    setParams((prev) => {
      const next = new URLSearchParams(prev)
      next.delete('chat')
      next.delete('prompt')
      return next
    }, { replace: true })
  }

  return (
    <Drawer open={open} onClose={close} title="Chat">
      <Chat initialPrompt={initialPrompt} />
    </Drawer>
  )
}
