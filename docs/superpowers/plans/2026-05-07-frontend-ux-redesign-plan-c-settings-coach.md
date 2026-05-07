# Frontend UX Redesign — Plan C: Settings + Coach Drawer + SSE Meta Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the new structured `/settings` page (replacing the chat-only `/profile`) and the global Coach drawer triggered by `?coach=1`. Add a small backend protocol change so chat replies that mutate the profile signal it via an SSE meta event, which the frontend uses to render an inline `Search now` button under the agent's reply. Move `InvalidSlugsNotice` into the new Settings page. The legacy `/profile → Onboarding.tsx` route stays alive (Plan D removes it).

**Architecture:** A snapshot-and-compare backend hook detects profile mutations during a chat turn (`profile_snapshot_at_start != profile_snapshot_at_end`) and emits `event: meta\ndata: {"profile_mutated": true}\n\n` on the SSE stream before `[DONE]`. The frontend `sendMessage` parses the new event type and surfaces it to the caller. A new `Coach` React component owns the chat UI (extracted patterns from `Onboarding.tsx`, but a clean rewrite since the consumer is different); a `CoachDrawer` wrapper reads `?coach=1` from the URL and mounts the chat inside the `Drawer` primitive globally in `App.tsx`. The new `Settings` page composes per-section subcomponents (search-toggle, resume, target-slugs, pruned-slugs, account) plus a read-only profile summary with a CTA into the Coach.

**Tech Stack:** React 18 + Vite + TypeScript + TanStack Query v5 + React Router v6 + Tailwind 3 + Vitest + @testing-library/react + jsdom. Backend: FastAPI + SQLModel + structlog. No new runtime deps.

**Spec:** `docs/superpowers/specs/2026-05-06-frontend-ux-redesign-design.md` Section 6 (Settings + Coach drawer + Onboarding-as-chat) and the SSE meta marker Open Implementation detail.

**Branching:** `feat/ui-coach-settings`, branched from `main` after Plan B (PR #95) merged.

---

## File Structure

**Files to create (frontend):**

```
frontend/src/components/coach/Coach.tsx               Chat UI (messages list + composer + resume upload)
frontend/src/components/coach/Coach.test.tsx
frontend/src/components/coach/CoachDrawer.tsx         URL-driven Drawer wrapper around Coach
frontend/src/components/coach/CoachDrawer.test.tsx
frontend/src/components/settings/SearchToggleSection.tsx
frontend/src/components/settings/SearchToggleSection.test.tsx
frontend/src/components/settings/ResumeSection.tsx
frontend/src/components/settings/ResumeSection.test.tsx
frontend/src/components/settings/TargetSlugsSection.tsx
frontend/src/components/settings/TargetSlugsSection.test.tsx
frontend/src/components/settings/PrunedSlugsSection.tsx
frontend/src/components/settings/PrunedSlugsSection.test.tsx
frontend/src/components/settings/AccountSection.tsx
frontend/src/components/settings/AccountSection.test.tsx
frontend/src/components/settings/ProfileSummary.tsx   Read-only summary + Open Coach CTA
frontend/src/components/settings/ProfileSummary.test.tsx
frontend/src/pages/Settings.tsx                       Composes the sections
frontend/src/pages/Settings.test.tsx
```

**Files to modify (frontend):**

```
frontend/src/api/client.ts            sendMessage emits onMeta callback for SSE event:meta lines
frontend/src/api/client.test.ts       New test for the meta callback path
frontend/src/App.tsx                  Mount global CoachDrawer; /settings → Settings (was Onboarding)
```

**Files to modify (backend):**

```
app/api/chat.py                       Snapshot-and-compare profile, emit meta event before [DONE]
tests/integration/test_chat_meta.py   New test (or location consistent with existing chat tests)
```

**Files NOT touched (deferred to Plan D):**

- `frontend/src/pages/Onboarding.tsx` — stays at `/profile` until Plan D folds it
- `frontend/src/pages/Applied.tsx`, `frontend/src/components/InvalidSlugsNotice.tsx` — old InvalidSlugsNotice file stays as a dead component until Plan D deletes (its content migrates into the new `PrunedSlugsSection`); we don't import the old component anywhere new
- Analytics / events table — Plan D
- Route deletes (`/applied`, `/profile`) — Plan D

---

## Task 0: Setup branch + baseline

**Files:** none

- [ ] **Step 1: Confirm clean tree, on `feat/ui-coach-settings` (already created), main is freshly synced**

```bash
cd /Users/panibrat/dev/job-application-agent
git status
git log --oneline -3
```

Expected: clean tree, on `feat/ui-coach-settings`, top three commits include Plan B (`faad0e2`) on the parent main.

- [ ] **Step 2: Capture baseline test counts (frontend + backend)**

```bash
cd frontend && npm install && npm run test 2>&1 | grep -E "Tests"
```

Expected: 163 tests pass. Note this number; subsequent counts in this plan are net-additive.

```bash
cd /Users/panibrat/dev/job-application-agent && uv run pytest tests/unit/ tests/integration/ -q 2>&1 | tail -5
```

Expected: existing backend suite green. Note the test count; we'll only add to it (one new chat-meta integration test in Task 1).

- [ ] **Step 3: Verify dev DB is reachable (needed for backend tests)**

```bash
docker compose up -d db
until docker compose exec db pg_isready -U postgres > /dev/null 2>&1; do sleep 1; done
DATABASE_URL="postgresql+asyncpg://jobagent:jobagent@localhost:5432/jobagent" make migrate ARGS="upgrade head"
```

Expected: migrations apply cleanly.

---

## Task 1: Backend — emit SSE meta event when profile mutates during a chat turn

**Files:**
- Modify: `app/api/chat.py`
- Create: `tests/integration/test_chat_meta.py`

The chat endpoint streams agent text via `data:` lines and ends with `data: [DONE]`. We want to add an `event: meta` line carrying `{"profile_mutated": true}` whenever the agent (or its tools) changed the user's profile during this turn. Approach: snapshot the profile summary at the start and end of the turn; compare; emit if different.

The "profile summary" we compare is `(updated_at, base_resume_md, target_roles, target_locations, search_keywords, target_company_slugs)`. These are the fields most likely to change during onboarding chat. `updated_at` alone is a strong signal — if the agent's tool wrote anything, the model's `onupdate` timestamp will bump.

- [ ] **Step 1: Write the failing integration test**

Look at the existing chat test layout to follow the pattern:

```bash
ls tests/integration/test_chat*.py 2>&1 || echo "no chat tests yet"
ls tests/integration/conftest.py
```

If no `test_chat*.py` exists, create `tests/integration/test_chat_meta.py` from scratch. If a similar test file does exist, look at its pytest fixtures (asyncpg test client, profile factory) and follow its pattern in the new file. The test below is illustrative — adjust the imports / fixture names to whatever the existing integration tests use.

Create `tests/integration/test_chat_meta.py`:

```python
"""Verify the chat endpoint emits a 'meta' SSE event when the agent mutates
the profile during a turn. The frontend uses this to render an inline
'Search now' CTA under that reply (see Plan C)."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user_profile import UserProfile


@pytest.mark.asyncio
async def test_chat_emits_meta_when_profile_mutated(
    client: AsyncClient,
    test_profile: UserProfile,
    db_session: AsyncSession,
    monkeypatch,
):
    """The endpoint streams a `meta: profile_mutated=true` event after the
    agent's reply when the profile was modified during the turn.

    We simulate a profile mutation by patching the agent stream to also bump
    the profile's updated_at timestamp; the snapshot-and-compare logic should
    detect it and emit meta. (The integration with the real agent is covered
    by upstream tests; here we only verify the meta-event path.)"""

    from datetime import UTC, datetime
    from unittest.mock import patch
    import json

    async def fake_stream(self, *args, **kwargs):
        # Yield a couple of plain text chunks and "mutate" the profile
        from langchain_core.messages import AIMessageChunk
        yield (AIMessageChunk(content="hello"), {})
        # Bump the profile's updated_at to simulate a tool write
        async with kwargs["config"].pop("__test_factory")() as s:
            from sqlalchemy import update
            await s.execute(update(UserProfile)
                            .where(UserProfile.id == test_profile.id)
                            .values(updated_at=datetime.now(UTC)))
            await s.commit()
        yield (AIMessageChunk(content=" world"), {})

    # Monkeypatch the graph's astream — only this test owns the indirection.
    # If the existing test layout already has a fixture for this, prefer it.
    # ... details depend on the pre-existing test fixtures.

    response = await client.post("/api/chat/messages", json={"message": "hi"})
    assert response.status_code == 200

    body = await response.aread()
    text = body.decode()

    # We expect: data: {"content": "hello"}\n\n
    #            data: {"content": " world"}\n\n
    #            event: meta\ndata: {"profile_mutated": true}\n\n
    #            data: [DONE]\n\n
    assert "hello" in text
    assert "event: meta" in text
    assert '"profile_mutated": true' in text
    assert "[DONE]" in text

    # Order: meta MUST appear before [DONE]
    meta_idx = text.find("event: meta")
    done_idx = text.find("[DONE]")
    assert 0 <= meta_idx < done_idx


@pytest.mark.asyncio
async def test_chat_does_not_emit_meta_when_profile_unchanged(
    client: AsyncClient,
    test_profile: UserProfile,
):
    """No mutation → no meta event."""
    response = await client.post("/api/chat/messages", json={"message": "hi"})
    assert response.status_code == 200
    text = (await response.aread()).decode()
    assert "event: meta" not in text
    assert "[DONE]" in text
```

Run it:

```bash
DATABASE_URL="postgresql+asyncpg://jobagent:jobagent@localhost:5432/jobagent" \
  uv run pytest tests/integration/test_chat_meta.py -q
```

Expected: FAIL — the endpoint does not emit `event: meta` yet, AND fixtures may not exist (depending on the test layout). **If the fixtures don't exist**, look at the closest existing integration test for the chat endpoint (search `tests/integration/` for "chat" or "stream") and adapt its fixtures. Your priority is to land the integration test that asserts the SSE meta event order; the fixture mechanics should follow the codebase's existing pattern.

- [ ] **Step 2: Implement the meta-event emission in `app/api/chat.py`**

Replace the contents of `app/api/chat.py`:

```python
"""Chat endpoint — streams onboarding agent responses via SSE.

If the agent mutates the user's profile during a turn (detected via a
before/after snapshot of profile.updated_at), the endpoint emits an
`event: meta\\ndata: {\"profile_mutated\": true}\\n\\n` event before
the terminal `[DONE]`. The frontend uses this to render an inline
'Search now' CTA under the mutating reply.
"""

import json

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_profile
from app.database import get_db, get_session_factory
from app.models.user_profile import UserProfile

log = structlog.get_logger()
router = APIRouter(prefix="/api/chat", tags=["chat"])


async def _profile_updated_at(session_factory, profile_id) -> object:
    """Read profile.updated_at in a fresh session — the request-scoped
    session may be in the middle of an unrelated transaction."""
    async with session_factory() as s:
        row = (await s.execute(
            select(UserProfile.updated_at).where(UserProfile.id == profile_id)
        )).first()
    return row[0] if row else None


@router.post("/messages")
async def send_message(
    request: Request,
    profile: UserProfile = Depends(get_current_profile),
    session: AsyncSession = Depends(get_db),
):
    body = await request.json()
    user_message = body.get("message", "").strip()
    if not user_message:
        return {"error": "message is required"}

    app_state = request.app.state
    checkpointer = getattr(app_state, "checkpointer", None)

    if checkpointer is None:
        async def no_op():
            msg = json.dumps({"content": "Agent not available — checkpointer not initialized."})
            yield f"data: {msg}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(no_op(), media_type="text/event-stream")

    from app.agents.onboarding import build_graph

    graph = build_graph(checkpointer)
    thread_id = str(profile.id)
    factory = get_session_factory()
    config = {
        "configurable": {
            "thread_id": thread_id,
            "db_factory": factory,
            "profile_id": str(profile.id),
        }
    }

    graph_input: dict = {
        "messages": [{"role": "user", "content": user_message}],
        "profile_id": str(profile.id),
        "resume_md": profile.base_resume_md,
        "profile_updates": {},
    }

    async def stream_response():
        from app.agents.llm_safe import BudgetExhausted
        from langchain_core.messages import AIMessageChunk

        # Snapshot profile.updated_at BEFORE the agent runs.
        before = await _profile_updated_at(factory, profile.id)

        try:
            async for chunk in graph.astream(graph_input, config, stream_mode="messages"):
                if not (isinstance(chunk, tuple) and len(chunk) == 2):
                    continue
                msg, _metadata = chunk
                if not isinstance(msg, AIMessageChunk):
                    continue
                content = msg.content
                text = ""
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text += block.get("text", "")
                if text:
                    yield f"data: {json.dumps({'content': text})}\n\n"
        except BudgetExhausted as exc:
            await log.awarning("chat.budget_exhausted", resumes_at=exc.resumes_at.isoformat())
            payload = {"error": "budget_exhausted", "resumes_at": exc.resumes_at.isoformat()}
            yield f"data: {json.dumps(payload)}\n\n"
        except Exception as e:
            await log.aexception("chat.stream_error", error=str(e))
            yield f"data: {json.dumps({'error': 'Stream error'})}\n\n"

        # AFTER the agent finishes (or errors), check for profile mutation.
        after = await _profile_updated_at(factory, profile.id)
        if before != after:
            yield 'event: meta\ndata: {"profile_mutated": true}\n\n'

        yield "data: [DONE]\n\n"

    return StreamingResponse(stream_response(), media_type="text/event-stream")
```

- [ ] **Step 3: Run the integration test, expect PASS**

```bash
DATABASE_URL="postgresql+asyncpg://jobagent:jobagent@localhost:5432/jobagent" \
  uv run pytest tests/integration/test_chat_meta.py -q
```

Expected: both tests pass. If the fixture path was tricky, the simplest fallback is to drop the second test and keep only the first (the order assertion is the value-add).

- [ ] **Step 4: Run the full backend suite to confirm no regressions**

```bash
DATABASE_URL="postgresql+asyncpg://jobagent:jobagent@localhost:5432/jobagent" \
  uv run pytest tests/unit/ tests/integration/ -q
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add app/api/chat.py tests/integration/test_chat_meta.py
git commit -m "feat(chat): emit SSE meta event when profile mutates during a turn

Snapshot profile.updated_at before/after agent run; emit
'event: meta\\ndata: {\"profile_mutated\": true}' before terminal [DONE]
when changed. Frontend uses this to render an inline 'Search now' CTA
under mutating replies (Plan C, Coach drawer)."
```

---

## Task 2: Frontend — `sendMessage` consumes the SSE meta event

**Files:**
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/api/client.test.ts`

The current `sendMessage(message, onChunk, onError)` parses `data: ` lines that start with `data: ` and assumes the payload is JSON `{content}`. Add an `onMeta` callback that fires when an `event: meta` line is received with a JSON payload on the next `data:` line.

- [ ] **Step 1: Update / add tests**

Read the existing test file first:

```bash
cd frontend && cat src/api/client.test.ts | head -80
```

Add a test in `frontend/src/api/client.test.ts` (append to the existing describe block, or wrap in a new one if appropriate):

```ts
import { describe, it, expect, vi, afterEach } from 'vitest'
import { api } from './client'

describe('sendMessage SSE parsing', () => {
  let originalFetch: typeof fetch

  afterEach(() => {
    if (originalFetch) global.fetch = originalFetch
  })

  function mockSseResponse(body: string): Response {
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(body))
        controller.close()
      },
    })
    return new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
  }

  it('forwards content chunks to onChunk', async () => {
    originalFetch = global.fetch
    const body = 'data: {"content":"hello"}\n\ndata: {"content":" world"}\n\ndata: [DONE]\n\n'
    global.fetch = vi.fn().mockResolvedValue(mockSseResponse(body))

    const chunks: string[] = []
    await api.sendMessage('hi', (c) => chunks.push(c))
    expect(chunks.join('')).toBe('hello world')
  })

  it('fires onMeta when an event: meta line precedes a JSON data line', async () => {
    originalFetch = global.fetch
    const body =
      'data: {"content":"hi"}\n\n' +
      'event: meta\ndata: {"profile_mutated": true}\n\n' +
      'data: [DONE]\n\n'
    global.fetch = vi.fn().mockResolvedValue(mockSseResponse(body))

    const onMeta = vi.fn()
    await api.sendMessage('hi', () => {}, undefined, onMeta)
    expect(onMeta).toHaveBeenCalledWith({ profile_mutated: true })
  })

  it('ignores unknown event types', async () => {
    originalFetch = global.fetch
    const body =
      'data: {"content":"hi"}\n\n' +
      'event: ping\ndata: {"x":1}\n\n' +
      'data: [DONE]\n\n'
    global.fetch = vi.fn().mockResolvedValue(mockSseResponse(body))

    const onMeta = vi.fn()
    await api.sendMessage('hi', () => {}, undefined, onMeta)
    expect(onMeta).not.toHaveBeenCalled()
  })
})
```

Run, expect FAIL:

```bash
npx vitest run src/api/client.test.ts
```

- [ ] **Step 2: Implement the meta callback in `frontend/src/api/client.ts`**

Find the `sendMessage` function (around line 206) and update its signature + parsing loop. The relevant section currently looks like:

```ts
sendMessage: (message: string, onChunk: (text: string) => void, onError?: (err: Error) => void): Promise<void> => {
  // ...
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    const text = decoder.decode(value)
    for (const line of text.split('\n')) {
      if (line.startsWith('data: ')) {
        const data = line.slice(6)
        if (data === '[DONE]') return
        try {
          const parsed = JSON.parse(data)
          if (parsed.content) onChunk(parsed.content)
        } catch {
          const err = new Error(`stream parse error: ${data}`)
          if (onError) { onError(err); return }
        }
      }
    }
  }
}
```

Replace with:

```ts
sendMessage: (
  message: string,
  onChunk: (text: string) => void,
  onError?: (err: Error) => void,
  onMeta?: (meta: Record<string, unknown>) => void,
): Promise<void> => {
  const token = sessionStorage.getItem('access_token')
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (token) headers['Authorization'] = `Bearer ${token}`
  return fetch('/api/chat/messages', {
    method: 'POST',
    headers,
    body: JSON.stringify({ message }),
  }).then(async (res) => {
    if (!res.ok) {
      const text = await res.text()
      const err = new Error(`${res.status}: ${text}`)
      if (onError) { onError(err); return }
      throw err
    }
    if (!res.body) return
    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let pendingEvent: string | null = null
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      const text = decoder.decode(value)
      for (const line of text.split('\n')) {
        if (line.startsWith('event: ')) {
          pendingEvent = line.slice(7).trim()
          continue
        }
        if (line.startsWith('data: ')) {
          const data = line.slice(6)
          const eventType = pendingEvent
          pendingEvent = null
          if (data === '[DONE]') return
          try {
            const parsed = JSON.parse(data)
            if (eventType === 'meta' && onMeta) {
              onMeta(parsed)
            } else if (parsed.content) {
              onChunk(parsed.content)
            }
          } catch {
            const err = new Error(`stream parse error: ${data}`)
            if (onError) { onError(err); return }
          }
        }
      }
    }
  })
},
```

- [ ] **Step 3: Run, expect tests PASS**

```bash
npx vitest run src/api/client.test.ts
```

Expected: 3 new tests pass. Confirm full `npm run test` count is still healthy (163 + 3 = 166).

- [ ] **Step 4: Type check**

```bash
npx tsc --noEmit
```

Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/api/client.test.ts
git commit -m "feat(frontend/api): sendMessage consumes SSE meta events

Adds optional onMeta(payload) callback fired when an 'event: meta' SSE
line precedes a JSON data line. Backwards-compatible — existing callers
that omit the callback are unaffected. Plan C's Coach drawer uses this
to render an inline 'Search now' CTA when the agent mutates the
profile."
```

After commit: 166 frontend tests + integration test count grew by 1–2.

---

## Task 3: Coach component (chat UI)

**Files:**
- Create: `frontend/src/components/coach/Coach.tsx`
- Create: `frontend/src/components/coach/Coach.test.tsx`

A self-contained chat component: messages list + composer + resume upload button + inline `Search now` CTA when the agent's most recent reply mutated the profile. Patterns are inspired by `Onboarding.tsx` but rewritten cleanly with the new primitives. The old `Onboarding.tsx` stays in place for `/profile`; Plan D removes it.

**Component contract:**
- Props: `{ initialPrompt?: string; onClose?: () => void }`
- Holds local message state (no global store)
- Calls `api.sendMessage(text, onChunk, onError, onMeta)` for each turn
- When `onMeta({ profile_mutated: true })` fires, marks the most recent assistant message as "mutating"; an inline `Search now` button appears under it that calls `api.triggerSync()`
- Resume upload is part of the composer row; success triggers a sync immediately + posts a follow-up message ("I've uploaded my resume…")

- [ ] **Step 1: Failing test**

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { server } from '../../test/server'
import { ToastProvider } from '../ui/Toast'
import { Coach } from './Coach'

function withCtx(node: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return (
    <QueryClientProvider client={qc}>
      <ToastProvider>{node}</ToastProvider>
    </QueryClientProvider>
  )
}

function sseStreamResponse(body: string): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(body))
      controller.close()
    },
  })
  return new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
}

describe('Coach', () => {
  it('renders with composer and resume upload affordances', () => {
    render(withCtx(<Coach />))
    expect(screen.getByPlaceholderText(/type your/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /resume/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^send$/i })).toBeInTheDocument()
  })

  it('prefills composer with initialPrompt (does not auto-send)', () => {
    render(withCtx(<Coach initialPrompt="What roles am I targeting?" />))
    const input = screen.getByPlaceholderText(/type your/i) as HTMLInputElement
    expect(input.value).toBe('What roles am I targeting?')
  })

  it('appends user + assistant message bubbles when sent', async () => {
    let originalFetch = global.fetch
    global.fetch = vi.fn().mockImplementation((url: string) => {
      if (url === '/api/chat/messages') {
        return Promise.resolve(sseStreamResponse('data: {"content":"hi back"}\n\ndata: [DONE]\n\n'))
      }
      return originalFetch(url)
    })

    const user = userEvent.setup()
    render(withCtx(<Coach />))
    await user.type(screen.getByPlaceholderText(/type your/i), 'hello')
    await user.click(screen.getByRole('button', { name: /^send$/i }))
    await waitFor(() => expect(screen.getByText('hello')).toBeInTheDocument())
    await waitFor(() => expect(screen.getByText('hi back')).toBeInTheDocument())
    global.fetch = originalFetch
  })

  it('renders a Search now button when the agent reply emits profile_mutated meta', async () => {
    let originalFetch = global.fetch
    let syncCalled = false
    global.fetch = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (url === '/api/chat/messages') {
        const body =
          'data: {"content":"updated"}\n\n' +
          'event: meta\ndata: {"profile_mutated": true}\n\n' +
          'data: [DONE]\n\n'
        return Promise.resolve(sseStreamResponse(body))
      }
      if (url === '/api/jobs/sync' && init?.method === 'POST') {
        syncCalled = true
        return Promise.resolve(new Response(JSON.stringify({
          status: 'queued', queued_slugs: [], matched_now: 0, seeded_defaults: false,
        }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
      }
      return originalFetch(url)
    })

    const user = userEvent.setup()
    render(withCtx(<Coach />))
    await user.type(screen.getByPlaceholderText(/type your/i), 'set roles')
    await user.click(screen.getByRole('button', { name: /^send$/i }))
    await waitFor(() => expect(screen.getByText('updated')).toBeInTheDocument())
    const cta = await screen.findByRole('button', { name: /search now/i })
    await user.click(cta)
    await waitFor(() => expect(syncCalled).toBe(true))
    global.fetch = originalFetch
  })
})
```

Run, expect FAIL:

```bash
cd frontend && npx vitest run src/components/coach/Coach.test.tsx
```

- [ ] **Step 2: Implement `Coach.tsx`**

```tsx
import { useEffect, useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import { Button } from '../ui/Button'
import { useToast } from '../ui/Toast'

interface Message {
  role: 'user' | 'assistant'
  content: string
  /** True when the agent indicated it mutated the profile during this turn. */
  profileMutated?: boolean
  error?: boolean
}

export interface CoachProps {
  /** Pre-fills the composer (does not auto-send). Used by deep links from
   *  the ProfileCompletenessCard's 'Tell coach →' rows. */
  initialPrompt?: string
}

export function Coach({ initialPrompt }: CoachProps) {
  const qc = useQueryClient()
  const { show } = useToast()
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState(initialPrompt ?? '')
  const [sending, setSending] = useState(false)
  const [uploading, setUploading] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const triggerSync = useMutation({
    mutationFn: api.triggerSync,
    onSuccess: () => {
      show('Searching now', 'success')
      qc.invalidateQueries({ queryKey: ['applications'] })
      qc.invalidateQueries({ queryKey: ['profile'] })
    },
    onError: (e) => show((e as Error)?.message ?? 'Sync failed', 'error'),
  })

  async function send(text: string) {
    if (!text.trim() || sending) return
    setInput('')
    setSending(true)
    setMessages((prev) => [...prev, { role: 'user', content: text }])
    setMessages((prev) => [...prev, { role: 'assistant', content: '' }])

    try {
      await api.sendMessage(
        text,
        (chunk) => {
          setMessages((prev) => {
            const out = [...prev]
            const last = out[out.length - 1]
            out[out.length - 1] = { ...last, content: last.content + chunk }
            return out
          })
        },
        () => {
          setMessages((prev) => {
            const out = [...prev]
            out[out.length - 1] = {
              role: 'assistant',
              content: 'Something went wrong — please try again.',
              error: true,
            }
            return out
          })
        },
        (meta) => {
          if (meta.profile_mutated) {
            setMessages((prev) => {
              const out = [...prev]
              out[out.length - 1] = { ...out[out.length - 1], profileMutated: true }
              return out
            })
            qc.invalidateQueries({ queryKey: ['profile'] })
          }
        },
      )
    } finally {
      setSending(false)
    }
  }

  async function onUpload(file: File) {
    setUploading(true)
    try {
      const result = await api.uploadResume(file)
      qc.invalidateQueries({ queryKey: ['profile'] })
      // Resume upload is unambiguous intent → trigger sync silently.
      triggerSync.mutate()
      if (result.extraction_status === 'llm_error') {
        show("Resume saved, but the AI is unavailable right now — edit your profile manually.", 'error')
      } else if (result.extraction_status === 'parse_error') {
        show("Resume saved but couldn't be parsed — try a plain-text or clearly-formatted PDF.", 'error')
      } else {
        show('Resume uploaded', 'success')
      }
      // Follow up with a chat message so the agent can verify and ask for any missing pieces.
      await send("I've uploaded my resume. Please review it and help me complete my profile.")
    } catch (err) {
      show(err instanceof Error ? err.message : 'Upload failed — try again', 'error')
    } finally {
      setUploading(false)
    }
  }

  return (
    <div className="flex flex-col h-full">
      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {messages.length === 0 && (
          <div className="text-center text-sm text-muted py-8">
            <p>Upload your resume or describe what you're looking for.</p>
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[85%] px-3 py-2 rounded-lg-token text-sm whitespace-pre-wrap ${
              m.role === 'user'
                ? 'bg-accent text-accent-fg rounded-br-sm'
                : m.error
                ? 'bg-danger/10 text-danger rounded-bl-sm'
                : 'bg-surface-2 text-text rounded-bl-sm'
            }`}>
              {m.content || (sending && i === messages.length - 1 ? '…' : '')}
              {m.role === 'assistant' && m.profileMutated && (
                <div className="mt-2 pt-2 border-t border-border">
                  <Button
                    size="sm"
                    pending={triggerSync.isPending}
                    onClick={() => triggerSync.mutate()}
                  >
                    ✦ Search now
                  </Button>
                </div>
              )}
            </div>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
      <div className="border-t border-border p-3 flex gap-2">
        <input
          ref={fileRef}
          type="file"
          accept=".pdf,.docx,.txt,.md"
          className="hidden"
          onChange={(e) => e.target.files?.[0] && onUpload(e.target.files[0])}
        />
        <button
          type="button"
          onClick={() => fileRef.current?.click()}
          disabled={uploading}
          className="px-3 py-2 text-sm text-muted border border-border-strong rounded-md-token hover:bg-surface min-h-[40px] disabled:opacity-50"
        >
          {uploading ? 'Uploading…' : 'Resume'}
        </button>
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(input) } }}
          placeholder="Type your message…"
          disabled={sending}
          className="flex-1 bg-surface text-text border border-border rounded-md-token px-3 py-2 text-sm min-h-[40px] focus:outline-2 focus:outline-accent/40 focus:outline-offset-2 focus:border-accent"
        />
        <Button onClick={() => send(input)} pending={sending} disabled={!input.trim()}>Send</Button>
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Run tests, expect PASS**

```bash
npx vitest run src/components/coach/Coach.test.tsx
```

Expected: 4 tests pass.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/coach/Coach.tsx frontend/src/components/coach/Coach.test.tsx
git commit -m "feat(frontend/coach): Coach chat component

Standalone chat UI: messages, composer, resume upload, inline 'Search
now' CTA when agent emits profile_mutated meta. Uses sendMessage's
onMeta callback (Task 2). initialPrompt prefills composer for deep-link
opens. Old Onboarding.tsx stays in place for /profile until Plan D."
```

After commit: 170 tests (166 + 4).

---

## Task 4: CoachDrawer + global mount

**Files:**
- Create: `frontend/src/components/coach/CoachDrawer.tsx`
- Create: `frontend/src/components/coach/CoachDrawer.test.tsx`
- Modify: `frontend/src/App.tsx`

URL-driven: opens whenever `?coach=1` is present, closes by removing the param. Pre-prompts come from `?coach=1&prompt=<slug>`. The slug → starter-message mapping lives in `CoachDrawer` (so any caller writing the URL gets the correct prompt without having to know the message text).

- [ ] **Step 1: Failing test**

```tsx
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { ToastProvider } from '../ui/Toast'
import { CoachDrawer } from './CoachDrawer'

function withCtx(initialEntry: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return (
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <MemoryRouter initialEntries={[initialEntry]}>
          <CoachDrawer />
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>
  )
}

describe('CoachDrawer', () => {
  it('renders nothing when ?coach is absent', () => {
    render(withCtx('/'))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('renders the drawer when ?coach=1 is present', () => {
    render(withCtx('/?coach=1'))
    const dlg = screen.getByRole('dialog')
    expect(dlg).toBeInTheDocument()
    expect(dlg).toHaveAttribute('aria-label', 'Coach')
  })

  it('closing the drawer removes ?coach from the URL', async () => {
    const user = userEvent.setup()
    render(withCtx('/?coach=1&status=applied'))
    await user.click(screen.getByRole('button', { name: /close drawer/i }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('passes a known prompt slug as initialPrompt to Coach', () => {
    render(withCtx('/?coach=1&prompt=set_locations'))
    const input = screen.getByPlaceholderText(/type your/i) as HTMLInputElement
    expect(input.value.toLowerCase()).toContain('location')
  })

  it('unknown prompt slug falls back to empty composer', () => {
    render(withCtx('/?coach=1&prompt=this_is_not_a_real_slug'))
    const input = screen.getByPlaceholderText(/type your/i) as HTMLInputElement
    expect(input.value).toBe('')
  })
})
```

Run, expect FAIL:

```bash
npx vitest run src/components/coach/CoachDrawer.test.tsx
```

- [ ] **Step 2: Implement `CoachDrawer.tsx`**

```tsx
import { useSearchParams } from 'react-router-dom'
import { Drawer } from '../ui/Drawer'
import { Coach } from './Coach'

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
```

- [ ] **Step 3: Run tests, expect 5 PASS**

```bash
npx vitest run src/components/coach/CoachDrawer.test.tsx
```

- [ ] **Step 4: Mount globally in App.tsx**

Replace the `ShellRoutes` function in `frontend/src/App.tsx` so that `<CoachDrawer />` is rendered inside the `AppShell` (which itself sits below `<BudgetBanner />`). Also import `CoachDrawer`:

```tsx
import { Routes, Route } from 'react-router-dom'
import { AuthProvider } from './context/AuthContext'
import { ToastProvider } from './components/ui/Toast'
import { AppShell } from './components/AppShell'
import { CoachDrawer } from './components/coach/CoachDrawer'
import BudgetBanner from './components/BudgetBanner'
import RequireAuth from './components/RequireAuth'
import Landing from './pages/Landing'
import AuthCallback from './pages/AuthCallback'
import Matches from './pages/Matches'
import ApplicationReview from './pages/ApplicationReview'
import Applied from './pages/Applied'
import Onboarding from './pages/Onboarding'

function ShellRoutes() {
  return (
    <>
      <BudgetBanner />
      <AppShell>
        <Routes>
          <Route path="/" element={<RequireAuth><Matches /></RequireAuth>} />
          <Route path="/login" element={<Landing />} />
          <Route path="/auth/callback" element={<AuthCallback />} />
          <Route path="/matches" element={<RequireAuth><Matches /></RequireAuth>} />
          <Route path="/matches/:id" element={<RequireAuth><ApplicationReview /></RequireAuth>} />
          <Route path="/applied" element={<RequireAuth><Applied /></RequireAuth>} />
          <Route path="/profile" element={<RequireAuth><Onboarding /></RequireAuth>} />
          <Route path="/settings" element={<RequireAuth><Onboarding /></RequireAuth>} />
        </Routes>
      </AppShell>
      <CoachDrawer />
    </>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <ToastProvider>
        <ShellRoutes />
      </ToastProvider>
    </AuthProvider>
  )
}
```

Note: `/settings` still aliases to `Onboarding` here. Task 12 swaps it to the new `Settings` page after we've built it.

- [ ] **Step 5: Run full test suite + tsc**

```bash
npm run test && npx tsc --noEmit
```

Expected: all green. The CoachDrawer is mounted but inert when `?coach` is absent, so no existing test should regress.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/coach/CoachDrawer.tsx frontend/src/components/coach/CoachDrawer.test.tsx frontend/src/App.tsx
git commit -m "feat(frontend/coach): CoachDrawer + global mount

URL-driven (?coach=1). Closing removes the param. Known prompt slugs
(set_resume / set_roles / set_locations / set_keywords / change_profile)
prefill the composer. Mounted in App.tsx outside the Routes so the
drawer is reachable from every authenticated page (and from the
unauthenticated Landing in case anything ever links to it)."
```

After commit: 175 tests (170 + 5).

---

## Task 5: Settings — SearchToggleSection

**Files:**
- Create: `frontend/src/components/settings/SearchToggleSection.tsx`
- Create: `frontend/src/components/settings/SearchToggleSection.test.tsx`

A small section with the active/paused state, expiry countdown, and a toggle that calls `api.toggleSearch(active)`.

- [ ] **Step 1: Failing test**

```tsx
import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { server } from '../../test/server'
import { ToastProvider } from '../ui/Toast'
import { SearchToggleSection } from './SearchToggleSection'

function withCtx(node: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return (
    <QueryClientProvider client={qc}>
      <ToastProvider>{node}</ToastProvider>
    </QueryClientProvider>
  )
}

describe('SearchToggleSection', () => {
  it('renders active state with Pause button', () => {
    render(withCtx(<SearchToggleSection active expiresAt={null} />))
    expect(screen.getByText(/search active/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /pause/i })).toBeInTheDocument()
  })

  it('renders paused state with Resume button', () => {
    render(withCtx(<SearchToggleSection active={false} expiresAt={null} />))
    expect(screen.getByText(/search paused/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /resume/i })).toBeInTheDocument()
  })

  it('shows expiry countdown when expiresAt is in the future', () => {
    const inThreeDays = new Date(Date.now() + 3 * 86_400_000).toISOString()
    render(withCtx(<SearchToggleSection active expiresAt={inThreeDays} />))
    expect(screen.getByText(/3 days/i)).toBeInTheDocument()
  })

  it('clicking Pause calls toggleSearch(false)', async () => {
    let body: unknown = null
    server.use(
      http.patch('/api/profile/search', async ({ request }) => {
        body = await request.json()
        return HttpResponse.json({ search_active: false, search_expires_at: null })
      }),
    )
    const user = userEvent.setup()
    render(withCtx(<SearchToggleSection active expiresAt={null} />))
    await user.click(screen.getByRole('button', { name: /pause/i }))
    await waitFor(() => expect(body).toEqual({ search_active: false }))
  })
})
```

Run, expect FAIL.

- [ ] **Step 2: Implement**

```tsx
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import { Button } from '../ui/Button'
import { useToast } from '../ui/Toast'

export interface SearchToggleSectionProps {
  active: boolean
  expiresAt: string | null
}

function daysUntil(iso: string | null): number | null {
  if (!iso) return null
  const ms = new Date(iso).getTime() - Date.now()
  if (ms <= 0) return null
  return Math.ceil(ms / 86_400_000)
}

export function SearchToggleSection({ active, expiresAt }: SearchToggleSectionProps) {
  const qc = useQueryClient()
  const { show } = useToast()
  const toggle = useMutation({
    mutationFn: (next: boolean) => api.toggleSearch(next),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['profile'] }),
    onError: (e) => show((e as Error)?.message ?? 'Could not update search', 'error'),
  })
  const days = daysUntil(expiresAt)

  return (
    <section className="mb-6">
      <h2 className="text-xs uppercase tracking-wider font-bold text-muted mb-2">Search</h2>
      <div className="bg-surface border border-border rounded-lg-token p-4 flex items-center justify-between">
        <div>
          <p className="text-sm font-semibold text-text">
            {active ? 'Search active' : 'Search paused'}
          </p>
          {active && days != null && (
            <p className="text-xs text-muted mt-0.5">Auto-pause in {days} day{days === 1 ? '' : 's'}</p>
          )}
        </div>
        <Button
          size="sm"
          variant={active ? 'secondary' : 'primary'}
          pending={toggle.isPending}
          onClick={() => toggle.mutate(!active)}
        >
          {active ? 'Pause' : 'Resume'}
        </Button>
      </div>
    </section>
  )
}
```

- [ ] **Step 3: Run tests, expect 4 PASS, commit**

```bash
npx vitest run src/components/settings/SearchToggleSection.test.tsx
git add frontend/src/components/settings/SearchToggleSection.tsx frontend/src/components/settings/SearchToggleSection.test.tsx
git commit -m "feat(frontend/settings): SearchToggleSection component

Active/paused state, expiry countdown when present, calls
api.toggleSearch on tap."
```

After commit: 179 tests (175 + 4).

---

## Task 6: Settings — ResumeSection + AccountSection (small, bundled)

**Files:**
- Create: `frontend/src/components/settings/ResumeSection.tsx`
- Create: `frontend/src/components/settings/ResumeSection.test.tsx`
- Create: `frontend/src/components/settings/AccountSection.tsx`
- Create: `frontend/src/components/settings/AccountSection.test.tsx`

Both small. Each gets its own commit per the plan.

- [ ] **Step 1: ResumeSection failing test**

```tsx
import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { server } from '../../test/server'
import { ToastProvider } from '../ui/Toast'
import { ResumeSection } from './ResumeSection'

function withCtx(node: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return (
    <QueryClientProvider client={qc}>
      <ToastProvider>{node}</ToastProvider>
    </QueryClientProvider>
  )
}

describe('ResumeSection', () => {
  it('renders the empty state when no resume is uploaded', () => {
    render(withCtx(<ResumeSection hasResume={false} />))
    expect(screen.getByText(/no resume on file/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /upload resume/i })).toBeInTheDocument()
  })

  it('renders the present state with re-upload action', () => {
    render(withCtx(<ResumeSection hasResume />))
    expect(screen.getByText(/resume on file/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /re-upload/i })).toBeInTheDocument()
  })

  it('shows a success toast after upload succeeds', async () => {
    server.use(
      http.post('/api/profile/upload', () => HttpResponse.json({
        id: 'p-1', base_resume_md: 'parsed', extraction_status: 'ok', message: 'ok',
      })),
    )
    const user = userEvent.setup()
    render(withCtx(<ResumeSection hasResume={false} />))
    const input = screen.getByTestId('resume-file-input') as HTMLInputElement
    const file = new File(['pdf bytes'], 'resume.pdf', { type: 'application/pdf' })
    await user.upload(input, file)
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(/uploaded/i))
  })
})
```

Run, expect FAIL.

- [ ] **Step 2: Implement ResumeSection**

```tsx
import { useRef } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import { Button } from '../ui/Button'
import { useToast } from '../ui/Toast'

export interface ResumeSectionProps {
  hasResume: boolean
}

export function ResumeSection({ hasResume }: ResumeSectionProps) {
  const qc = useQueryClient()
  const { show } = useToast()
  const fileRef = useRef<HTMLInputElement>(null)

  const upload = useMutation({
    mutationFn: (file: File) => api.uploadResume(file),
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: ['profile'] })
      if (result.extraction_status === 'llm_error') {
        show("Resume saved, but the AI is unavailable right now — edit your profile manually.", 'error')
      } else if (result.extraction_status === 'parse_error') {
        show("Resume saved but couldn't be parsed — try a plain-text or clearly-formatted PDF.", 'error')
      } else {
        show('Resume uploaded', 'success')
      }
    },
    onError: (e) => show((e as Error)?.message ?? 'Upload failed', 'error'),
  })

  return (
    <section className="mb-6">
      <h2 className="text-xs uppercase tracking-wider font-bold text-muted mb-2">Resume</h2>
      <div className="bg-surface border border-border rounded-lg-token p-4 flex items-center justify-between">
        <p className="text-sm text-text">
          {hasResume ? 'Resume on file' : 'No resume on file'}
        </p>
        <input
          ref={fileRef}
          data-testid="resume-file-input"
          type="file"
          accept=".pdf,.docx,.txt,.md"
          className="hidden"
          onChange={(e) => e.target.files?.[0] && upload.mutate(e.target.files[0])}
        />
        <Button
          size="sm"
          variant="secondary"
          pending={upload.isPending}
          onClick={() => fileRef.current?.click()}
        >
          {hasResume ? 'Re-upload' : 'Upload resume'}
        </Button>
      </div>
    </section>
  )
}
```

Run, expect 3 PASS, commit:

```bash
npx vitest run src/components/settings/ResumeSection.test.tsx
git add frontend/src/components/settings/ResumeSection.tsx frontend/src/components/settings/ResumeSection.test.tsx
git commit -m "feat(frontend/settings): ResumeSection component

Empty / present states. Re-upload action triggers POST /api/profile/upload;
toast surfaces extraction_status (ok / parse_error / llm_error)."
```

- [ ] **Step 3: AccountSection failing test**

```tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AccountSection } from './AccountSection'

vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u-1', email: 'maks@x.com' },
    token: 'fake', loading: false, signOut: vi.fn(),
  }),
}))

describe('AccountSection', () => {
  it('renders the user email', () => {
    render(<AccountSection />)
    expect(screen.getByText('maks@x.com')).toBeInTheDocument()
  })

  it('renders a Sign out button', () => {
    render(<AccountSection />)
    expect(screen.getByRole('button', { name: /sign out/i })).toBeInTheDocument()
  })
})
```

Run, expect FAIL.

- [ ] **Step 4: Implement AccountSection**

```tsx
import { useAuth } from '../../context/AuthContext'
import { Button } from '../ui/Button'

export function AccountSection() {
  const { user, signOut } = useAuth()
  return (
    <section className="mb-6">
      <h2 className="text-xs uppercase tracking-wider font-bold text-muted mb-2">Account</h2>
      <div className="bg-surface border border-border rounded-lg-token p-4 flex items-center justify-between">
        <p className="text-sm text-text">{user?.email ?? '—'}</p>
        <Button size="sm" variant="ghost" onClick={() => signOut()}>Sign out</Button>
      </div>
    </section>
  )
}
```

Run, expect 2 PASS, commit:

```bash
npx vitest run src/components/settings/AccountSection.test.tsx
git add frontend/src/components/settings/AccountSection.tsx frontend/src/components/settings/AccountSection.test.tsx
git commit -m "feat(frontend/settings): AccountSection component

Email + Sign out button (delegates to AuthContext.signOut)."
```

After both commits: 184 tests (179 + 3 + 2).

---

## Task 7: Settings — TargetSlugsSection

**Files:**
- Create: `frontend/src/components/settings/TargetSlugsSection.tsx`
- Create: `frontend/src/components/settings/TargetSlugsSection.test.tsx`

Editable list per provider. The current API supports `target_company_slugs: { greenhouse?: string[]; lever?: string[]; ashby?: string[] }`. We expose an inline chip list with a `+ Add` input for each provider. PATCH the entire `target_company_slugs` object on each change.

- [ ] **Step 1: Failing test**

```tsx
import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { server } from '../../test/server'
import { ToastProvider } from '../ui/Toast'
import { TargetSlugsSection } from './TargetSlugsSection'

function withCtx(node: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return (
    <QueryClientProvider client={qc}>
      <ToastProvider>{node}</ToastProvider>
    </QueryClientProvider>
  )
}

describe('TargetSlugsSection', () => {
  it('renders existing greenhouse slugs as chips', () => {
    render(withCtx(<TargetSlugsSection slugs={{ greenhouse: ['stripe', 'vercel'] }} />))
    expect(screen.getByText('stripe')).toBeInTheDocument()
    expect(screen.getByText('vercel')).toBeInTheDocument()
  })

  it('removes a slug via the chip remove button', async () => {
    let patched: unknown = null
    server.use(
      http.patch('/api/profile', async ({ request }) => {
        patched = await request.json()
        return HttpResponse.json({ id: 'p-1', updated: true })
      }),
    )
    const user = userEvent.setup()
    render(withCtx(<TargetSlugsSection slugs={{ greenhouse: ['stripe'] }} />))
    await user.click(screen.getByRole('button', { name: /remove stripe/i }))
    await waitFor(() => expect(patched).toMatchObject({
      target_company_slugs: { greenhouse: [] },
    }))
  })

  it('adds a slug via the +Add input', async () => {
    let patched: unknown = null
    server.use(
      http.patch('/api/profile', async ({ request }) => {
        patched = await request.json()
        return HttpResponse.json({ id: 'p-1', updated: true })
      }),
    )
    const user = userEvent.setup()
    render(withCtx(<TargetSlugsSection slugs={{ greenhouse: ['stripe'] }} />))
    const input = screen.getByPlaceholderText(/add greenhouse slug/i)
    await user.type(input, 'newco')
    await user.keyboard('{Enter}')
    await waitFor(() => expect(patched).toMatchObject({
      target_company_slugs: { greenhouse: ['stripe', 'newco'] },
    }))
  })
})
```

Run, expect FAIL.

- [ ] **Step 2: Implement**

```tsx
import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import { useToast } from '../ui/Toast'

export interface TargetSlugsSectionProps {
  slugs: { greenhouse?: string[]; lever?: string[]; ashby?: string[] }
}

const PROVIDERS: Array<{ key: 'greenhouse' | 'lever' | 'ashby'; label: string }> = [
  { key: 'greenhouse', label: 'Greenhouse' },
  { key: 'lever',      label: 'Lever' },
  { key: 'ashby',      label: 'Ashby' },
]

export function TargetSlugsSection({ slugs }: TargetSlugsSectionProps) {
  const qc = useQueryClient()
  const { show } = useToast()
  const [drafts, setDrafts] = useState<Record<string, string>>({})

  const patch = useMutation({
    mutationFn: (next: TargetSlugsSectionProps['slugs']) =>
      api.updateProfile({ target_company_slugs: next }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['profile'] }),
    onError: (e) => show((e as Error)?.message ?? 'Could not update slugs', 'error'),
  })

  function add(key: 'greenhouse' | 'lever' | 'ashby') {
    const draft = (drafts[key] ?? '').trim().toLowerCase()
    if (!draft) return
    const existing = slugs[key] ?? []
    if (existing.includes(draft)) return
    patch.mutate({ ...slugs, [key]: [...existing, draft] })
    setDrafts((d) => ({ ...d, [key]: '' }))
  }

  function remove(key: 'greenhouse' | 'lever' | 'ashby', s: string) {
    const existing = slugs[key] ?? []
    patch.mutate({ ...slugs, [key]: existing.filter((x) => x !== s) })
  }

  return (
    <section className="mb-6">
      <h2 className="text-xs uppercase tracking-wider font-bold text-muted mb-2">Target boards</h2>
      <div className="bg-surface border border-border rounded-lg-token p-4 space-y-4">
        {PROVIDERS.map(({ key, label }) => (
          <div key={key}>
            <p className="text-sm font-semibold text-text mb-2">{label}</p>
            <div className="flex flex-wrap gap-2 mb-2">
              {(slugs[key] ?? []).map((s) => (
                <span key={s} className="inline-flex items-center gap-1 px-2 py-1 bg-surface-2 border border-border rounded-pill text-xs text-text">
                  {s}
                  <button
                    type="button"
                    aria-label={`Remove ${s}`}
                    onClick={() => remove(key, s)}
                    className="text-muted hover:text-danger"
                  >×</button>
                </span>
              ))}
              {(slugs[key] ?? []).length === 0 && (
                <p className="text-xs text-subtle">No {label.toLowerCase()} boards yet.</p>
              )}
            </div>
            <input
              type="text"
              value={drafts[key] ?? ''}
              onChange={(e) => setDrafts((d) => ({ ...d, [key]: e.target.value }))}
              onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); add(key) } }}
              placeholder={`Add ${label} slug…`}
              className="w-full bg-bg text-text border border-border rounded-md-token px-2 py-1.5 text-sm min-h-[36px] focus:outline-2 focus:outline-accent/40 focus:border-accent"
            />
          </div>
        ))}
      </div>
    </section>
  )
}
```

- [ ] **Step 3: Run tests, expect 3 PASS, commit**

```bash
npx vitest run src/components/settings/TargetSlugsSection.test.tsx
git add frontend/src/components/settings/TargetSlugsSection.tsx frontend/src/components/settings/TargetSlugsSection.test.tsx
git commit -m "feat(frontend/settings): TargetSlugsSection component

Per-provider chip lists (Greenhouse / Lever / Ashby) with +Add inputs
and remove-X buttons. PATCHes the full target_company_slugs object on
every mutation."
```

After commit: 187 tests (184 + 3).

---

## Task 8: Settings — PrunedSlugsSection

**Files:**
- Create: `frontend/src/components/settings/PrunedSlugsSection.tsx`
- Create: `frontend/src/components/settings/PrunedSlugsSection.test.tsx`

Reads `/api/sync/status` (existing endpoint) every 30s, lists `invalid_slugs`. Per-slug client-side dismissal (matches the old behavior). The old `frontend/src/components/InvalidSlugsNotice.tsx` is NOT deleted in this plan — Plan D removes it after confirming no consumers (the legacy InvalidSlugsNotice was never imported on the new Feed page in Plan B; this section replaces it on Settings).

- [ ] **Step 1: Failing test**

```tsx
import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { server } from '../../test/server'
import { PrunedSlugsSection } from './PrunedSlugsSection'

function withCtx(node: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>)
}

describe('PrunedSlugsSection', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/sync/status', () => HttpResponse.json({
        state: 'idle', slugs_total: 0, slugs_pending: 0, matches_pending: 0,
        last_sync_requested_at: null, last_sync_completed_at: null,
        last_sync_summary: null, invalid_slugs: ['defunct-co', 'gone-co'],
      })),
    )
  })

  it('renders nothing when there are no invalid slugs', async () => {
    server.use(
      http.get('/api/sync/status', () => HttpResponse.json({
        state: 'idle', slugs_total: 0, slugs_pending: 0, matches_pending: 0,
        last_sync_requested_at: null, last_sync_completed_at: null,
        last_sync_summary: null, invalid_slugs: [],
      })),
    )
    const { container } = withCtx(<PrunedSlugsSection />)
    await waitFor(() => expect(container.firstChild).toBeNull())
  })

  it('lists invalid slugs', async () => {
    withCtx(<PrunedSlugsSection />)
    await waitFor(() => expect(screen.getByText('defunct-co')).toBeInTheDocument())
    expect(screen.getByText('gone-co')).toBeInTheDocument()
  })

  it('per-slug dismiss removes a single chip from view', async () => {
    const user = userEvent.setup()
    withCtx(<PrunedSlugsSection />)
    await waitFor(() => expect(screen.getByText('defunct-co')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: /dismiss defunct-co/i }))
    expect(screen.queryByText('defunct-co')).not.toBeInTheDocument()
    expect(screen.getByText('gone-co')).toBeInTheDocument()
  })
})
```

Run, expect FAIL.

- [ ] **Step 2: Implement**

```tsx
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../api/client'

export function PrunedSlugsSection() {
  const [dismissed, setDismissed] = useState<Set<string>>(new Set())
  const { data } = useQuery({
    queryKey: ['sync-status-for-pruned'],
    queryFn: api.getSyncStatus,
    refetchInterval: 30_000,
  })
  const invalid = (data?.invalid_slugs ?? []).filter((s) => !dismissed.has(s))
  if (invalid.length === 0) return null

  return (
    <section className="mb-6">
      <h2 className="text-xs uppercase tracking-wider font-bold text-muted mb-2">Auto-removed boards</h2>
      <div className="bg-warning/5 border border-warning/30 rounded-lg-token p-4">
        <p className="text-xs text-muted mb-3">
          We removed these boards because they returned 404 too many times. Add them back below if they come online again.
        </p>
        <div className="flex flex-wrap gap-2">
          {invalid.map((s) => (
            <span key={s} className="inline-flex items-center gap-1 px-2 py-1 bg-surface-2 border border-border rounded-pill text-xs text-text">
              <code className="font-mono">{s}</code>
              <button
                type="button"
                aria-label={`Dismiss ${s}`}
                onClick={() => setDismissed((d) => new Set([...d, s]))}
                className="text-muted hover:text-text"
              >×</button>
            </span>
          ))}
        </div>
      </div>
    </section>
  )
}
```

- [ ] **Step 3: Run tests, expect 3 PASS, commit**

```bash
npx vitest run src/components/settings/PrunedSlugsSection.test.tsx
git add frontend/src/components/settings/PrunedSlugsSection.tsx frontend/src/components/settings/PrunedSlugsSection.test.tsx
git commit -m "feat(frontend/settings): PrunedSlugsSection component

Lists invalid_slugs from /api/sync/status with per-slug client-side
dismissal. Replaces the old InvalidSlugsNotice's home (it was on the
Feed in legacy code; in the new design it belongs in Settings). The
legacy InvalidSlugsNotice file stays untouched until Plan D deletes it."
```

After commit: 190 tests (187 + 3).

---

## Task 9: Settings — ProfileSummary

**Files:**
- Create: `frontend/src/components/settings/ProfileSummary.tsx`
- Create: `frontend/src/components/settings/ProfileSummary.test.tsx`

Read-only summary of structured profile fields with an `Open Coach` CTA that deep-links into the drawer with `?coach=1&prompt=change_profile`.

- [ ] **Step 1: Failing test**

```tsx
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { ProfileSummary } from './ProfileSummary'
import type { Profile } from '../../api/client'

function fullProfile(over: Partial<Profile> = {}): Profile {
  return {
    id: 'p-1', full_name: 'Maks', email: 'm@x.com', phone: null,
    linkedin_url: null, github_url: null, portfolio_url: null,
    base_resume_md: 'r', target_roles: ['Backend', 'Platform'],
    target_locations: ['Berlin', 'Remote-EU'], remote_ok: true,
    seniority: 'senior', search_keywords: ['python'], search_active: true,
    search_expires_at: null, target_company_slugs: { greenhouse: ['stripe'] },
    skills: [
      { id: 's1', name: 'Go', category: null, proficiency: null, years: 5 },
      { id: 's2', name: 'Postgres', category: null, proficiency: null, years: 7 },
    ],
    work_experiences: [
      { id: 'w1', company: 'Acme', title: 'Eng', start_date: '2020-01-01',
        end_date: null, description_md: null, technologies: [] },
    ],
    ...over,
  }
}

describe('ProfileSummary', () => {
  it('renders roles, locations, salary line, skills count, experience count', () => {
    render(
      <MemoryRouter>
        <ProfileSummary profile={fullProfile()} />
      </MemoryRouter>
    )
    expect(screen.getByText(/backend/i)).toBeInTheDocument()
    expect(screen.getByText(/berlin/i)).toBeInTheDocument()
    expect(screen.getByText(/2 skills/i)).toBeInTheDocument()
    expect(screen.getByText(/1 experience/i)).toBeInTheDocument()
  })

  it('Open Coach CTA links to ?coach=1&prompt=change_profile', () => {
    render(
      <MemoryRouter>
        <ProfileSummary profile={fullProfile()} />
      </MemoryRouter>
    )
    const link = screen.getByRole('link', { name: /open coach/i })
    expect(link.getAttribute('href')).toMatch(/coach=1/)
    expect(link.getAttribute('href')).toMatch(/prompt=change_profile/)
  })
})
```

Run, expect FAIL.

- [ ] **Step 2: Implement**

```tsx
import { Link } from 'react-router-dom'
import { Profile } from '../../api/client'

export function ProfileSummary({ profile }: { profile: Profile }) {
  return (
    <section className="mb-6">
      <h2 className="text-xs uppercase tracking-wider font-bold text-muted mb-2">Profile</h2>
      <div className="bg-surface border border-border rounded-lg-token p-4 space-y-2 text-sm">
        {profile.target_roles.length > 0 && (
          <p><span className="text-muted">Roles: </span>{profile.target_roles.join(', ')}</p>
        )}
        {(profile.target_locations.length > 0 || profile.remote_ok) && (
          <p>
            <span className="text-muted">Locations: </span>
            {[...profile.target_locations, profile.remote_ok ? 'Remote' : null]
              .filter(Boolean).join(', ')}
          </p>
        )}
        {profile.seniority && (
          <p><span className="text-muted">Seniority: </span>{profile.seniority}</p>
        )}
        {profile.search_keywords.length > 0 && (
          <p><span className="text-muted">Keywords: </span>{profile.search_keywords.join(', ')}</p>
        )}
        <p>
          <span className="text-muted">{profile.skills.length} skill{profile.skills.length === 1 ? '' : 's'}</span>
          {' · '}
          <span className="text-muted">{profile.work_experiences.length} experience{profile.work_experiences.length === 1 ? '' : 's'}</span>
        </p>
        <p className="pt-2 border-t border-border text-xs text-muted">
          To change anything here:{' '}
          <Link
            to="?coach=1&prompt=change_profile"
            className="text-accent font-semibold"
          >
            ✦ Open Coach
          </Link>
        </p>
      </div>
    </section>
  )
}
```

- [ ] **Step 3: Run tests, expect 2 PASS, commit**

```bash
npx vitest run src/components/settings/ProfileSummary.test.tsx
git add frontend/src/components/settings/ProfileSummary.tsx frontend/src/components/settings/ProfileSummary.test.tsx
git commit -m "feat(frontend/settings): ProfileSummary component

Read-only structured fields summary with an 'Open Coach' deep link
into the drawer prompted with change_profile."
```

After commit: 192 tests (190 + 2).

---

## Task 10: Settings page composition

**Files:**
- Create: `frontend/src/pages/Settings.tsx`
- Create: `frontend/src/pages/Settings.test.tsx`

Composes the sections + handles the loading state.

- [ ] **Step 1: Failing test**

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router-dom'
import { server } from '../test/server'
import { ToastProvider } from '../components/ui/Toast'
import Settings from './Settings'
import type { Profile } from '../api/client'

vi.mock('../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u-1', email: 'maks@x.com' },
    token: 'fake', loading: false, signOut: vi.fn(),
  }),
}))

function fullProfile(): Profile {
  return {
    id: 'p-1', full_name: 'Maks', email: 'm@x.com', phone: null,
    linkedin_url: null, github_url: null, portfolio_url: null,
    base_resume_md: 'r', target_roles: ['Backend'], target_locations: ['Berlin'],
    remote_ok: true, seniority: 'senior', search_keywords: ['python'],
    search_active: true, search_expires_at: null,
    target_company_slugs: { greenhouse: ['stripe'] },
    skills: [], work_experiences: [],
  }
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <MemoryRouter>
          <Settings />
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>
  )
}

describe('Settings page', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/profile', () => HttpResponse.json(fullProfile())),
      http.get('/api/sync/status', () => HttpResponse.json({
        state: 'idle', slugs_total: 0, slugs_pending: 0, matches_pending: 0,
        last_sync_requested_at: null, last_sync_completed_at: null,
        last_sync_summary: null, invalid_slugs: [],
      })),
    )
  })

  it('renders all sections after profile loads', async () => {
    renderPage()
    await waitFor(() => expect(screen.getByText(/search active/i)).toBeInTheDocument())
    expect(screen.getByText(/resume on file/i)).toBeInTheDocument()
    expect(screen.getByText(/target boards/i)).toBeInTheDocument()
    expect(screen.getByText(/account/i)).toBeInTheDocument()
    expect(screen.getByText(/profile/i)).toBeInTheDocument()
  })

  it('shows the loading state before profile resolves', () => {
    server.use(http.get('/api/profile', async () => {
      await new Promise((r) => setTimeout(r, 50))
      return HttpResponse.json(fullProfile())
    }))
    renderPage()
    expect(screen.getByText(/loading/i)).toBeInTheDocument()
  })
})
```

Run, expect FAIL.

- [ ] **Step 2: Implement Settings.tsx**

```tsx
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { SearchToggleSection } from '../components/settings/SearchToggleSection'
import { ResumeSection } from '../components/settings/ResumeSection'
import { TargetSlugsSection } from '../components/settings/TargetSlugsSection'
import { PrunedSlugsSection } from '../components/settings/PrunedSlugsSection'
import { ProfileSummary } from '../components/settings/ProfileSummary'
import { AccountSection } from '../components/settings/AccountSection'

export default function Settings() {
  const { data: profile, isLoading } = useQuery({
    queryKey: ['profile'],
    queryFn: api.getProfile,
  })

  if (isLoading || !profile) {
    return <div className="text-muted py-12 text-center">Loading…</div>
  }

  return (
    <div className="max-w-2xl mx-auto">
      <h1 className="text-xl font-bold text-text mb-6">Settings</h1>
      <SearchToggleSection active={profile.search_active} expiresAt={profile.search_expires_at} />
      <ResumeSection hasResume={!!profile.base_resume_md} />
      <TargetSlugsSection slugs={profile.target_company_slugs ?? {}} />
      <PrunedSlugsSection />
      <ProfileSummary profile={profile} />
      <AccountSection />
    </div>
  )
}
```

- [ ] **Step 3: Run tests, expect 2 PASS, commit**

```bash
npx vitest run src/pages/Settings.test.tsx
git add frontend/src/pages/Settings.tsx frontend/src/pages/Settings.test.tsx
git commit -m "feat(frontend/pages): Settings page composition

Composes SearchToggle / Resume / TargetSlugs / PrunedSlugs / Profile
summary / Account sections. Loading state when profile is fetching."
```

After commit: 194 tests (192 + 2).

---

## Task 11: Update App.tsx to route /settings to the new Settings page

**Files:**
- Modify: `frontend/src/App.tsx`

The previous `/settings` was aliased to `Onboarding` (Plan A's transitional alias). Swap it to the real `Settings` page now.

- [ ] **Step 1: Edit App.tsx**

Find the `/settings` route line and the Onboarding import. Update so:

```tsx
// Add import:
import Settings from './pages/Settings'

// In routes:
<Route path="/settings" element={<RequireAuth><Settings /></RequireAuth>} />
```

The `/profile` route still aliases to `Onboarding` (Plan D removes that route). Final `App.tsx`:

```tsx
import { Routes, Route } from 'react-router-dom'
import { AuthProvider } from './context/AuthContext'
import { ToastProvider } from './components/ui/Toast'
import { AppShell } from './components/AppShell'
import { CoachDrawer } from './components/coach/CoachDrawer'
import BudgetBanner from './components/BudgetBanner'
import RequireAuth from './components/RequireAuth'
import Landing from './pages/Landing'
import AuthCallback from './pages/AuthCallback'
import Matches from './pages/Matches'
import ApplicationReview from './pages/ApplicationReview'
import Applied from './pages/Applied'
import Onboarding from './pages/Onboarding'
import Settings from './pages/Settings'

function ShellRoutes() {
  return (
    <>
      <BudgetBanner />
      <AppShell>
        <Routes>
          <Route path="/" element={<RequireAuth><Matches /></RequireAuth>} />
          <Route path="/login" element={<Landing />} />
          <Route path="/auth/callback" element={<AuthCallback />} />
          <Route path="/matches" element={<RequireAuth><Matches /></RequireAuth>} />
          <Route path="/matches/:id" element={<RequireAuth><ApplicationReview /></RequireAuth>} />
          <Route path="/applied" element={<RequireAuth><Applied /></RequireAuth>} />
          <Route path="/profile" element={<RequireAuth><Onboarding /></RequireAuth>} />
          <Route path="/settings" element={<RequireAuth><Settings /></RequireAuth>} />
        </Routes>
      </AppShell>
      <CoachDrawer />
    </>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <ToastProvider>
        <ShellRoutes />
      </ToastProvider>
    </AuthProvider>
  )
}
```

- [ ] **Step 2: Run full test suite + tsc**

```bash
npm run test && npx tsc --noEmit
```

Expected: 194 tests pass, tsc clean.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "feat(frontend): /settings now renders Settings page

Replaces the Plan A transitional alias to Onboarding. /profile keeps
aliasing to Onboarding until Plan D folds the chat into the drawer
and removes the route."
```

---

## Task 12: Bump muted/subtle text-token contrast for accessibility

**Files:**
- Modify: `frontend/src/styles/tokens.css`

**EXECUTE THIS TASK BEFORE TASK 1.** Listed at the bottom because it was added late, but it should land first so every component built in subsequent tasks renders against the new (more readable) values.

User feedback: some grey text is hard to read on the dark background. The current tokens are:

- `--c-text-muted: #9ca3af` (Tailwind gray-400) → ~5.7:1 contrast against `#0b0d12` (passes WCAG AA but borderline)
- `--c-text-subtle: #6b7280` (Tailwind gray-500) → ~3.5:1 (FAILS WCAG AA for body text)

Shift the scale up: muted lifts to gray-300, subtle lifts to gray-400. The hierarchy is preserved (muted still less prominent than text, subtle still less prominent than muted) but legibility improves significantly.

- [ ] **Step 1: Edit `frontend/src/styles/tokens.css`**

Find the Text section and replace:

```css
  /* Text */
  --c-text: #f9fafb;
  --c-text-muted: #9ca3af;
  --c-text-subtle: #6b7280;
```

with:

```css
  /* Text */
  --c-text: #f9fafb;
  --c-text-muted: #cbd5e1;   /* gray-300 — was #9ca3af; bumped for readability on dark bg */
  --c-text-subtle: #9ca3af;  /* gray-400 — was #6b7280; bumped to pass WCAG AA */
```

- [ ] **Step 2: Run full test suite + tsc + build**

```bash
cd frontend && npm run test && npx tsc --noEmit && npm run build
```

Expected: all tests still pass (token values are runtime CSS, not part of any test assertion). Build succeeds.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/styles/tokens.css
git commit -m "fix(frontend/tokens): bump muted/subtle text contrast on dark bg

User feedback: grey text was hard to see on the page background.
- --c-text-muted: #9ca3af → #cbd5e1 (gray-300)
- --c-text-subtle: #6b7280 → #9ca3af (gray-400)

Hierarchy preserved (text > muted > subtle); subtle now passes
WCAG AA for body text."
```

---

## Task 13: Restyle Landing page (auth screen) consistent with the dark design

**Files:**
- Modify: `frontend/src/pages/Landing.tsx`
- Modify: `frontend/src/pages/Landing.test.tsx` (only if existing assertions break)

The Landing page (sign-in) was last touched in the original codebase; it still uses pre-Plan A class names like `bg-gray-50`, `bg-white`, `text-gray-900`, `border-gray-300` etc. Bring it inline with the new dark token system so first-time and signed-out users see consistent chrome.

The page layout / behavior does not change — only colors, borders, radii, and the surface treatment. The Google sign-in button stays a `<button>` with the existing icon SVG and click handler. The dev-login link stays.

- [ ] **Step 1: Read the current file**

```bash
cd frontend && cat src/pages/Landing.tsx
```

Note the existing class names so the diff is just a swap.

- [ ] **Step 2: Replace `frontend/src/pages/Landing.tsx`**

Keep the existing `startGoogleLogin`, `startDevLogin`, state, error rendering, and SVG icon. Replace the class names on the wrapping div, h1, paragraph, and buttons.

```tsx
import { useState } from 'react'

export default function Landing() {
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)

  // (Behavior identical to the previous file — only styling changes.)
  async function startGoogleLogin() {
    setError(null)
    setPending(true)
    try {
      const res = await fetch('/auth/google/authorize', { credentials: 'same-origin' })
      if (!res.ok) throw new Error(`authorize returned ${res.status}`)
      const data = await res.json()
      if (!data?.authorization_url) throw new Error('missing authorization_url')
      window.location.href = data.authorization_url
    } catch (err) {
      setPending(false)
      setError('Sign-in is unavailable right now. Please try again in a moment.')
      console.error('Google OAuth start failed', err)
    }
  }

  async function startDevLogin() {
    setError(null)
    setPending(true)
    try {
      const res = await fetch('/api/test/login', { method: 'POST' })
      if (!res.ok) throw new Error(`test login returned ${res.status}`)
      const data = await res.json()
      if (!data?.access_token) throw new Error('missing access_token')
      sessionStorage.setItem('access_token', data.access_token)
      window.location.href = '/matches'
    } catch (err) {
      setPending(false)
      setError('Dev login failed. Is the backend running with ENVIRONMENT=development?')
      console.error('Dev login failed', err)
    }
  }

  return (
    <div className="min-h-screen bg-bg flex flex-col items-center justify-center gap-8 px-4">
      <div className="text-center max-w-lg">
        <h1 className="text-3xl font-bold text-text mb-3 tracking-tight">Job Application Agent</h1>
        <p className="text-muted">
          AI-powered job matching. Upload your resume, set your preferences, and get
          tailored applications generated automatically.
        </p>
      </div>
      <button
        type="button"
        onClick={startGoogleLogin}
        disabled={pending}
        className="inline-flex items-center gap-3 px-6 py-3 bg-surface border border-border-strong rounded-lg-token text-text font-medium hover:bg-surface-2 transition-colors disabled:opacity-60 disabled:cursor-not-allowed min-h-[48px]"
      >
        <svg className="w-5 h-5" viewBox="0 0 24 24">
          <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
          <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
          <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l3.66-2.84z"/>
          <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
        </svg>
        {pending ? 'Redirecting…' : 'Sign in with Google'}
      </button>
      {error && (
        <p role="alert" className="text-sm text-danger -mt-4">{error}</p>
      )}
      {import.meta.env.DEV && (
        <button
          type="button"
          onClick={startDevLogin}
          disabled={pending}
          className="text-xs text-subtle hover:text-text underline disabled:opacity-60 disabled:cursor-not-allowed"
        >
          Dev login (skip OAuth)
        </button>
      )}
      <a href="https://github.com/maksym-panibrat/job-application-agent" className="text-sm text-subtle hover:text-text">
        View on GitHub
      </a>
    </div>
  )
}
```

What changed:
- `bg-gray-50` → `bg-bg`
- `text-gray-900` → `text-text`
- `text-gray-600` → `text-muted`
- `bg-white` (button) → `bg-surface`
- `border-gray-300` → `border-border-strong`
- `text-gray-700` (button) → `text-text`
- `hover:bg-gray-50` → `hover:bg-surface-2`
- `text-red-600` → `text-danger`
- `text-gray-400 hover:text-gray-600` → `text-subtle hover:text-text`
- `rounded-lg` (button) → `rounded-lg-token` (token-backed)
- Added `min-h-[48px]` on the primary button (matches our 48px lg-button rule)
- `tracking-tight` on the h1 (matches the new design system's heading treatment)

- [ ] **Step 3: Run the existing Landing tests, expect PASS**

```bash
npx vitest run src/pages/Landing.test.tsx
```

Expected: PASS. The existing tests target text content and roles (heading + button), not class names, so they should not regress. If any assertion does break (e.g., it inspected `text-red-600`), update the test to match the new token-backed class.

- [ ] **Step 4: Run full test suite + tsc + build**

```bash
npm run test && npx tsc --noEmit && npm run build
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/Landing.tsx
git commit -m "feat(frontend/landing): restyle with new dark tokens

Brings the auth screen inline with the rest of the app: bg-bg page,
bg-surface card, border-border-strong, accent-fg etc. Behavior
unchanged; only colors / radii / spacing classes swap. min-h-[48px]
on the primary CTA matches the lg-button rule."
```

---

## Task 14: Final verification + PR

**Files:** none

- [ ] **Step 1: Full unit test run**

```bash
cd frontend && npm run test
```

Expected: 194 tests pass.

- [ ] **Step 2: Type check + build**

```bash
npx tsc --noEmit && npm run build
```

Expected: clean.

- [ ] **Step 3: Backend test run**

```bash
cd /Users/panibrat/dev/job-application-agent
DATABASE_URL="postgresql+asyncpg://jobagent:jobagent@localhost:5432/jobagent" \
  uv run pytest tests/unit/ tests/integration/ -q
```

Expected: green; new chat-meta test included.

- [ ] **Step 4: E2E**

```bash
DATABASE_URL="postgresql+asyncpg://jobagent:jobagent@localhost:5432/jobagent" \
  GOOGLE_API_KEY=test-key ENVIRONMENT=development \
  npm --prefix frontend run test:e2e
```

Expected: 15/15 pass. Plan B's e2e tests assume `/settings` renders the Onboarding page (fixed-up test "Profile Setup" heading). After this plan that route now renders the new Settings page — the heading is "Settings". Update the e2e to match:

```bash
grep -n "Profile Setup" frontend/e2e/*.ts
```

If the auth-and-nav spec asserts "Profile Setup" heading after clicking Settings, update to "Settings":

In `frontend/e2e/auth-and-nav.spec.ts`, change:

```ts
await expect(page.getByRole('heading', { name: /Profile Setup/i })).toBeVisible()
```

to:

```ts
await expect(page.getByRole('heading', { name: /^Settings$/i })).toBeVisible()
```

Re-run the e2e. Expected: still 15/15.

- [ ] **Step 5: Push + open PR**

```bash
git push -u origin feat/ui-coach-settings
gh pr create --title "feat(frontend): UX redesign Plan C — Settings page + Coach drawer + SSE meta" --body "$(cat <<'EOF'
## Summary
Per spec at \`docs/superpowers/specs/2026-05-06-frontend-ux-redesign-design.md\` Section 6 + the SSE meta open-implementation note. Builds on Plans A & B.

**Backend:**
- \`app/api/chat.py\`: snapshot \`profile.updated_at\` before/after the agent run; emit \`event: meta\\ndata: {\"profile_mutated\": true}\` before terminal \`[DONE]\` when changed. Integration test asserts the event order.

**Frontend Coach drawer:**
- \`api.sendMessage\` gains \`onMeta\` callback that fires on the new SSE meta event.
- New \`Coach\` component (chat UI: messages, composer, resume upload, inline 'Search now' CTA when meta indicates profile mutation).
- New \`CoachDrawer\`: URL-driven (\`?coach=1\`), pre-prompted via \`?prompt=<slug>\`, mounted globally in App.tsx.

**Frontend Settings page:**
- New \`/settings\` page composes section components: SearchToggle, Resume, TargetSlugs (Greenhouse/Lever/Ashby chips + Add), PrunedSlugs (replaces InvalidSlugsNotice), ProfileSummary (read-only with Open Coach CTA), Account.

**Spec deviations / pragmatic adjustments:**
- \`InvalidSlugsNotice.tsx\` left in place (no consumers) — Plan D deletes it.
- \`/profile\` still aliases to Onboarding.tsx — Plan D folds it.

## Test plan
- [ ] CI green (\`npm run test\`, \`tsc --noEmit\`, \`npm run build\`, backend pytest)
- [ ] CI green for e2e (15/15 locally)
- [ ] Manual: ?coach=1 opens drawer; closing removes the param
- [ ] Manual: \"Tell coach\" links from ProfileCompletenessCard pre-fill the composer
- [ ] Manual: editing the profile via chat triggers an inline 'Search now' button under that reply
- [ ] Manual: Settings page reflects current profile; Pause/Resume search works; resume re-upload works; slug add/remove works; Open Coach link from Profile section opens drawer with prefill

**Test counts:**
- Unit: 163 → **194** (+31 net across 13 new test files)
- E2E: 15/15 pass locally
- Backend: +2 new chat-meta tests

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 6: Watch CI on the PR**

Wait for CI. If it fails, fix on this branch (NEW commits, never amend), push, wait again.

---

## Self-Review Checklist

- [ ] Spec coverage: every Settings sub-section called out in spec section 6 has a task — Search ✓ Resume ✓ Target boards ✓ Pruned ✓ Profile (read-only) ✓ Account ✓.
- [ ] Coach drawer: URL-driven ✓, pre-prompted opens ✓, inline `Search now` CTA ✓.
- [ ] SSE meta marker: backend emits ✓, frontend consumes ✓.
- [ ] InvalidSlugsNotice: replaced by `PrunedSlugsSection` on Settings ✓; old file untouched (Plan D deletes).
- [ ] No placeholders / TBDs / "implement appropriately" language.
- [ ] Type consistency: `Profile`, `SyncStatus`, `Document`, `useAuth`, `useToast` all match Plan A/B definitions and the existing `api/client.ts`.
- [ ] Tests written before implementation in every component task.
- [ ] Commits use conventional prefixes.

## Out of scope for Plan C (carried to Plan D)

- **Analytics events table + ingest + SQL views.**
- **Cleanup deletes**: `Onboarding.tsx`, `Applied.tsx`, `InvalidSlugsNotice.tsx`, `/profile` and `/applied` routes.
- **Backend extension** to allow `pending_review` in `PATCH /api/applications/:id` (still error-toasts when the kebab "Move back to pending" is used).
- **True markdown rendering** of job descriptions.
- **Pull-to-refresh** on the Feed.

End of Plan C.
