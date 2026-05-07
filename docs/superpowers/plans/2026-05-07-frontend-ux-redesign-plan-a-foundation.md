# Frontend UX Redesign — Plan A: Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the foundational design layer — color tokens, typography/radius/motion utilities, the primitive component library in `src/components/ui/`, and the redesigned `AppShell` (header + mobile hamburger). After this plan, every page in the app renders with the new dark Modern Product look-and-feel through the new shell, even though the page bodies themselves still use the legacy components. Plan B handles per-page migrations and analytics.

**Architecture:** A single `tokens.css` injected through the existing `src/index.css` provides CSS variables; `tailwind.config.js` is extended to expose those variables as utilities (e.g., `bg-surface`, `text-muted`, `border-strong`). Each primitive is a self-contained TypeScript React component in `src/components/ui/` with a colocated `.test.tsx`. The redesigned `AppShell` wraps `<Routes>` in `App.tsx`, replacing the inline nav.

**Tech Stack:** React 18 + Vite + TypeScript + TanStack Query + React Router v6 + Tailwind 3 + Vitest + @testing-library/react + jsdom. No new runtime dependencies.

**Spec:** `docs/superpowers/specs/2026-05-06-frontend-ux-redesign-design.md` (sections 1, 2, 3 — design system, primitives, IA shell). Plan B will cover sections 4–7 (pages + analytics).

**Branching:** Implementation lives on `feat/ui-foundation`, branched from `main` once the spec branch (`docs/frontend-ux-redesign-spec`) has merged. If the spec branch is not yet merged at execution time, branch from `docs/frontend-ux-redesign-spec` instead so the engineer has the spec checked out for reference.

---

## File Structure

**Files to be created:**

```
frontend/src/styles/tokens.css                    Color/spacing/radius/motion CSS variables
frontend/src/lib/cn.ts                            Tiny className concatenator (no clsx dep)
frontend/src/lib/cn.test.ts                       Tests for cn()
frontend/src/components/ui/Button.tsx             <Button variant size pending>
frontend/src/components/ui/Button.test.tsx
frontend/src/components/ui/IconButton.tsx         <IconButton aria-label>
frontend/src/components/ui/IconButton.test.tsx
frontend/src/components/ui/Chip.tsx               <Chip selected count onClick>
frontend/src/components/ui/Chip.test.tsx
frontend/src/components/ui/Badge.tsx              <Badge intent>
frontend/src/components/ui/Badge.test.tsx
frontend/src/components/ui/Card.tsx               <Card interactive as>
frontend/src/components/ui/Card.test.tsx
frontend/src/components/ui/ActionSheet.tsx        Bottom sheet / centered popover
frontend/src/components/ui/ActionSheet.test.tsx
frontend/src/components/ui/Drawer.tsx             Side drawer (right rail / mobile takeover)
frontend/src/components/ui/Drawer.test.tsx
frontend/src/components/ui/Toast.tsx              ToastProvider + useToast() + <Toast>
frontend/src/components/ui/Toast.test.tsx
frontend/src/components/ui/EmptyState.tsx         Title + desc + CTA layout
frontend/src/components/ui/EmptyState.test.tsx
frontend/src/components/ui/TextField.tsx          <TextField label error>
frontend/src/components/ui/TextField.test.tsx
frontend/src/components/ui/TextArea.tsx           Auto-resizing <TextArea label>
frontend/src/components/ui/TextArea.test.tsx
frontend/src/components/ui/Skeleton.tsx           <SkeletonLine> + <SkeletonCard>
frontend/src/components/ui/Skeleton.test.tsx
frontend/src/components/ui/SwipeableCard.tsx      Pointer-event swipe with reveal
frontend/src/components/ui/SwipeableCard.test.tsx
frontend/src/components/ui/icons/Settings.tsx     Inline SVG
frontend/src/components/ui/icons/Coach.tsx
frontend/src/components/ui/icons/Hamburger.tsx
frontend/src/components/ui/icons/Close.tsx
frontend/src/components/ui/icons/Kebab.tsx
frontend/src/components/ui/icons/index.ts         Re-exports
frontend/src/components/AppShell.tsx              Header + mobile hamburger sheet + Outlet
frontend/src/components/AppShell.test.tsx
```

**Files to be modified:**

```
frontend/src/index.css                  Import tokens.css
frontend/tailwind.config.js             Extend theme.colors, fontFamily, borderRadius
frontend/src/App.tsx                    Replace inline nav with <AppShell><Outlet/></AppShell>
frontend/src/test/setup.ts              Add jsdom stubs for IntersectionObserver, matchMedia
```

**Files NOT touched in this plan** (deferred to Plan B): `MatchCard.tsx`, `Matches.tsx`, `ApplicationReview.tsx`, `Onboarding.tsx`, `Applied.tsx`, `Landing.tsx`, `BudgetBanner.tsx`, `SyncStatusChip.tsx`, `InvalidSlugsNotice.tsx`. These continue to render with their existing styles inside the new `AppShell`. Plan B replaces them.

---

## Task 0: Setup branch and baseline

**Files:** none

- [ ] **Step 1: Confirm clean working tree, on main, up to date**

```bash
cd /Users/panibrat/dev/job-application-agent
git status
git fetch origin main
git log --oneline -1 origin/main
```

Expected: clean working tree, on a branch that's up to date with `origin/main`. If `docs/frontend-ux-redesign-spec` has merged, `origin/main` HEAD should be the spec commit.

- [ ] **Step 2: Create feature branch**

```bash
git switch --create feat/ui-foundation origin/main
```

If `docs/frontend-ux-redesign-spec` has NOT yet merged, run instead:

```bash
git fetch origin docs/frontend-ux-redesign-spec
git switch --create feat/ui-foundation origin/docs/frontend-ux-redesign-spec
```

- [ ] **Step 3: Run baseline test + lint**

```bash
cd frontend
npm install   # in case deps drifted
npm run test
```

Expected: all existing tests pass (this is the green baseline). Note the pass count and copy it into your scratchpad — every subsequent test run must show pass count strictly increasing.

```bash
npx tsc --noEmit
```

Expected: no type errors.

- [ ] **Step 4: Confirm dev server boots**

```bash
npm run dev
```

Expected: server prints `VITE v5.x ready in ...ms` and binds to `:5173`. Visit `http://localhost:5173` in a browser; the existing landing page should render. Stop the server with `Ctrl-C` once confirmed.

---

## Task 1: Add design tokens CSS

**Files:**
- Create: `frontend/src/styles/tokens.css`
- Modify: `frontend/src/index.css`

- [ ] **Step 1: Create tokens file**

Create `frontend/src/styles/tokens.css`:

```css
/* Design tokens — single source of truth for colors, typography, spacing rhythm.
   Consumed by tailwind.config.js (extend.colors etc.) so utilities like
   `bg-surface` and `text-muted` resolve to these variables.

   Dark-only for now. Light-mode tokens would live under a `:root[data-theme="light"]`
   block but are deliberately deferred (see spec — out of scope). */
:root {
  /* Surfaces */
  --c-bg: #0b0d12;
  --c-surface: #11141b;
  --c-surface-2: #1a1e28;

  /* Borders */
  --c-border: #1f2330;
  --c-border-strong: #2a2f3d;

  /* Text */
  --c-text: #f9fafb;
  --c-text-muted: #9ca3af;
  --c-text-subtle: #6b7280;

  /* Accent (purple) */
  --c-accent: #a78bfa;
  --c-accent-fg: #0b0d12;

  /* Semantic */
  --c-success: #4ade80;
  --c-warning: #fbbf24;
  --c-danger: #ef4444;

  /* Radius */
  --r-sm: 6px;
  --r-md: 10px;
  --r-lg: 14px;
  --r-pill: 999px;

  /* Motion */
  --t-fast: 150ms;
  --t-slow: 250ms;
  --ease: cubic-bezier(0.2, 0.8, 0.2, 1);
}

/* Page baseline */
html, body {
  background: var(--c-bg);
  color: var(--c-text);
  font-family: 'Inter', ui-sans-serif, system-ui, -apple-system, sans-serif;
  -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility;
}

/* Reduce motion respect */
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    transition-duration: 0.01ms !important;
    animation-duration: 0.01ms !important;
  }
}
```

- [ ] **Step 2: Wire into the global stylesheet**

Edit `frontend/src/index.css` to:

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

@import './styles/tokens.css';
```

- [ ] **Step 3: Boot dev server, confirm no break**

```bash
npm run dev
```

Visit `http://localhost:5173`. The landing page should render with the dark page background now showing through (since tokens.css sets `body { background: var(--c-bg) }`). Existing inline `bg-gray-50` on `<div className="min-h-screen ...">` will still paint over it locally — that's fine; the foundation is in place. Stop with `Ctrl-C`.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/styles/tokens.css frontend/src/index.css
git commit -m "feat(frontend): add design tokens CSS layer

Dark-mode color/radius/motion variables plus body baseline. Light mode
tokens deferred per spec. Existing pages still paint over the body bg
with their inline gray-50; per-page migration in Plan B."
```

---

## Task 2: Extend Tailwind config with token-backed utilities

**Files:**
- Modify: `frontend/tailwind.config.js`

- [ ] **Step 1: Replace tailwind.config.js**

```js
/** Tailwind extends with the design tokens defined in src/styles/tokens.css.
    Utilities like `bg-surface`, `text-muted`, `border-strong`, `rounded-lg-token`
    pull through CSS variables so the theme is single-sourced. */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        bg:           'var(--c-bg)',
        surface:      'var(--c-surface)',
        'surface-2':  'var(--c-surface-2)',
        border:       'var(--c-border)',
        'border-strong': 'var(--c-border-strong)',
        text:         'var(--c-text)',
        muted:        'var(--c-text-muted)',
        subtle:       'var(--c-text-subtle)',
        accent:       'var(--c-accent)',
        'accent-fg':  'var(--c-accent-fg)',
        success:      'var(--c-success)',
        warning:      'var(--c-warning)',
        danger:       'var(--c-danger)',
      },
      borderRadius: {
        'sm-token':  'var(--r-sm)',
        'md-token':  'var(--r-md)',
        'lg-token':  'var(--r-lg)',
        pill:        'var(--r-pill)',
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'SF Mono', 'Consolas', 'monospace'],
      },
      transitionTimingFunction: {
        'token-ease': 'cubic-bezier(0.2, 0.8, 0.2, 1)',
      },
    },
  },
  plugins: [],
}
```

Why suffixed names like `lg-token`: Tailwind already ships `rounded-lg` (8px). Coexisting with `rounded-lg-token` (14px) is intentional — components opt into the token explicitly. Avoids surprise on legacy code.

- [ ] **Step 2: Confirm dev build still compiles**

```bash
npm run build
```

Expected: Vite + tsc both succeed; output written to `app/static/`.

- [ ] **Step 3: Quick smoke — utility resolves**

Run dev server briefly and inspect a div using new utilities. Add a temporary div to `App.tsx` (will revert):

Open `frontend/src/App.tsx`, in the `<main>` block, add as the FIRST child:

```tsx
<div data-token-smoke className="bg-surface text-muted border border-border rounded-lg-token p-4">token smoke</div>
```

Run `npm run dev`, open `http://localhost:5173`, inspect the element. Expected: background `#11141b`, text `#9ca3af`, border `#1f2330`, 14px corner radius. If any value is wrong, the Tailwind config didn't pick up the variable — re-check.

- [ ] **Step 4: Revert the smoke div**

Remove the smoke div from `App.tsx`. Confirm via `git diff frontend/src/App.tsx` that nothing is left.

- [ ] **Step 5: Commit**

```bash
git add frontend/tailwind.config.js
git commit -m "feat(frontend): extend tailwind theme with token-backed utilities

Adds bg-surface, text-muted, rounded-lg-token, etc. Token-suffixed names
on radii so legacy rounded-lg (8px) keeps working unchanged."
```

---

## Task 3: Add cn() utility for className composition

**Files:**
- Create: `frontend/src/lib/cn.ts`
- Create: `frontend/src/lib/cn.test.ts`

A tiny `cn()` helper concatenates className strings, dropping falsy values. Prevents pulling in `clsx` (no new dep). Used by every primitive.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/lib/cn.test.ts`:

```ts
import { describe, it, expect } from 'vitest'
import { cn } from './cn'

describe('cn', () => {
  it('joins truthy strings with single spaces', () => {
    expect(cn('a', 'b', 'c')).toBe('a b c')
  })

  it('drops falsy values (false, null, undefined, "")', () => {
    expect(cn('a', false, null, undefined, '', 'b')).toBe('a b')
  })

  it('returns empty string when nothing truthy', () => {
    expect(cn(false, null, undefined)).toBe('')
  })

  it('handles a single argument', () => {
    expect(cn('only')).toBe('only')
  })
})
```

- [ ] **Step 2: Run test, expect failure**

```bash
npx vitest run src/lib/cn.test.ts
```

Expected: FAIL with `Cannot find module './cn'`.

- [ ] **Step 3: Implement cn()**

Create `frontend/src/lib/cn.ts`:

```ts
export type ClassValue = string | false | null | undefined

export function cn(...values: ClassValue[]): string {
  return values.filter(Boolean).join(' ')
}
```

- [ ] **Step 4: Run test, expect pass**

```bash
npx vitest run src/lib/cn.test.ts
```

Expected: 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/cn.ts frontend/src/lib/cn.test.ts
git commit -m "feat(frontend): add cn() className helper

Tiny zero-dep concatenator used by every ui/ primitive."
```

---

## Task 4: Add jsdom stubs for primitives that need them

**Files:**
- Modify: `frontend/src/test/setup.ts`

Several primitives use `IntersectionObserver`, `ResizeObserver`, and `matchMedia`. jsdom doesn't ship them. Stub once.

- [ ] **Step 1: Replace test setup file**

```ts
import '@testing-library/jest-dom'
import { server } from './server'

beforeAll(() => server.listen({ onUnhandledRequest: 'warn' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

window.HTMLElement.prototype.scrollIntoView = () => {}

// jsdom does not implement these — primitives need them mocked at the
// global level so component tests don't have to redo this per file.
class _MockObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
  takeRecords() { return [] }
}

// @ts-expect-error — assigning to the global typed as undefined-able
window.IntersectionObserver = _MockObserver
// @ts-expect-error
window.ResizeObserver = _MockObserver

if (!window.matchMedia) {
  window.matchMedia = (query: string): MediaQueryList => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  } as MediaQueryList)
}
```

- [ ] **Step 2: Run existing tests, confirm nothing broke**

```bash
npm run test
```

Expected: same pass count as Task 0 baseline. (Test count unchanged — we added stubs, no tests.)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/test/setup.ts
git commit -m "test(frontend): stub IntersectionObserver / ResizeObserver / matchMedia in jsdom

Foundation for the ui/ primitives that depend on them (Drawer focus
management, ActionSheet, Toast viewport detection)."
```

---

## Task 5: Button primitive

**Files:**
- Create: `frontend/src/components/ui/Button.tsx`
- Create: `frontend/src/components/ui/Button.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Button } from './Button'

describe('Button', () => {
  it('renders children', () => {
    render(<Button>Sync now</Button>)
    expect(screen.getByRole('button', { name: 'Sync now' })).toBeInTheDocument()
  })

  it('defaults to type="button" (not submit)', () => {
    render(<Button>Click</Button>)
    expect(screen.getByRole('button')).toHaveAttribute('type', 'button')
  })

  it('calls onClick when clicked', async () => {
    const fn = vi.fn()
    const user = userEvent.setup()
    render(<Button onClick={fn}>Hit me</Button>)
    await user.click(screen.getByRole('button'))
    expect(fn).toHaveBeenCalledTimes(1)
  })

  it('disables interaction and shows pending label when pending', async () => {
    const fn = vi.fn()
    const user = userEvent.setup()
    render(<Button pending onClick={fn}>Sync</Button>)
    const btn = screen.getByRole('button')
    expect(btn).toBeDisabled()
    expect(btn).toHaveAttribute('aria-busy', 'true')
    await user.click(btn)
    expect(fn).not.toHaveBeenCalled()
  })

  it('applies variant-specific class for primary (default)', () => {
    render(<Button>Default</Button>)
    expect(screen.getByRole('button').className).toMatch(/bg-accent/)
  })

  it('applies variant-specific class for secondary', () => {
    render(<Button variant="secondary">Sec</Button>)
    expect(screen.getByRole('button').className).toMatch(/border-border-strong/)
  })

  it('applies variant-specific class for ghost', () => {
    render(<Button variant="ghost">G</Button>)
    expect(screen.getByRole('button').className).toMatch(/text-muted/)
  })

  it('applies variant-specific class for destructive', () => {
    render(<Button variant="destructive">D</Button>)
    expect(screen.getByRole('button').className).toMatch(/bg-danger/)
  })

  it('size sm has shorter min-height than md', () => {
    render(
      <>
        <Button size="sm" data-testid="b-sm">sm</Button>
        <Button size="md" data-testid="b-md">md</Button>
      </>
    )
    expect(screen.getByTestId('b-sm').className).toMatch(/min-h-\[32px\]/)
    expect(screen.getByTestId('b-md').className).toMatch(/min-h-\[40px\]/)
  })

  it('size lg has larger min-height than md', () => {
    render(<Button size="lg" data-testid="b-lg">lg</Button>)
    expect(screen.getByTestId('b-lg').className).toMatch(/min-h-\[48px\]/)
  })

  it('forwards extra className', () => {
    render(<Button className="extra-thing">x</Button>)
    expect(screen.getByRole('button').className).toMatch(/extra-thing/)
  })
})
```

- [ ] **Step 2: Run test, expect failure**

```bash
npx vitest run src/components/ui/Button.test.tsx
```

Expected: FAIL with `Cannot find module './Button'`.

- [ ] **Step 3: Implement Button**

Create `frontend/src/components/ui/Button.tsx`:

```tsx
import { ButtonHTMLAttributes, forwardRef } from 'react'
import { cn } from '../../lib/cn'

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'destructive'
export type ButtonSize = 'sm' | 'md' | 'lg'

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  pending?: boolean
}

const variantClass: Record<ButtonVariant, string> = {
  primary:     'bg-accent text-accent-fg hover:brightness-110',
  secondary:   'bg-transparent text-text border border-border-strong hover:bg-surface',
  ghost:       'bg-transparent text-muted hover:bg-surface hover:text-text',
  destructive: 'bg-danger text-white hover:brightness-110',
}

const sizeClass: Record<ButtonSize, string> = {
  sm: 'px-3 py-1.5 text-sm min-h-[32px]',
  md: 'px-4 py-2.5 text-sm min-h-[40px]',
  lg: 'px-5 py-3 text-base min-h-[48px]',
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'primary', size = 'md', pending = false, className, type, children, disabled, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type ?? 'button'}
      disabled={disabled || pending}
      aria-busy={pending || undefined}
      className={cn(
        'inline-flex items-center justify-center gap-2 font-semibold rounded-md-token transition-colors duration-[var(--t-fast)] disabled:opacity-50 disabled:cursor-not-allowed',
        variantClass[variant],
        sizeClass[size],
        className,
      )}
      {...rest}
    >
      {pending && (
        <span aria-hidden="true" className="inline-block w-3 h-3 rounded-full border-2 border-current border-r-transparent animate-spin" />
      )}
      {children}
    </button>
  )
})
```

- [ ] **Step 4: Run test, expect all pass**

```bash
npx vitest run src/components/ui/Button.test.tsx
```

Expected: 11 tests pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ui/Button.tsx frontend/src/components/ui/Button.test.tsx
git commit -m "feat(frontend/ui): Button primitive

Variants: primary, secondary, ghost, destructive. Sizes sm/md/lg with
min-h ≥ 32/40/48 (44px tap including padding on md). Pending disables
interaction and sets aria-busy."
```

---

## Task 6: IconButton primitive

**Files:**
- Create: `frontend/src/components/ui/IconButton.tsx`
- Create: `frontend/src/components/ui/IconButton.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { IconButton } from './IconButton'

describe('IconButton', () => {
  it('renders the icon child and exposes the aria-label', () => {
    render(<IconButton aria-label="Close"><span>X</span></IconButton>)
    expect(screen.getByRole('button', { name: 'Close' })).toBeInTheDocument()
  })

  it('forwards onClick', async () => {
    const fn = vi.fn()
    const user = userEvent.setup()
    render(<IconButton aria-label="More" onClick={fn}><span>⋯</span></IconButton>)
    await user.click(screen.getByRole('button'))
    expect(fn).toHaveBeenCalled()
  })

  it('renders with at-least-44px tap target', () => {
    render(<IconButton aria-label="X"><span>x</span></IconButton>)
    expect(screen.getByRole('button').className).toMatch(/w-11/)
    expect(screen.getByRole('button').className).toMatch(/h-11/)
  })

  it('forwards extra className', () => {
    render(<IconButton aria-label="X" className="custom"><span>x</span></IconButton>)
    expect(screen.getByRole('button').className).toMatch(/custom/)
  })
})
```

- [ ] **Step 2: Run test, expect failure**

```bash
npx vitest run src/components/ui/IconButton.test.tsx
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement IconButton**

```tsx
import { ButtonHTMLAttributes, forwardRef, ReactNode } from 'react'
import { cn } from '../../lib/cn'

export interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** Required for accessibility — IconButton has no visible text. */
  'aria-label': string
  children: ReactNode
}

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { className, children, type, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type ?? 'button'}
      className={cn(
        'inline-flex items-center justify-center w-11 h-11 rounded-md-token text-muted hover:bg-surface hover:text-text transition-colors duration-[var(--t-fast)] disabled:opacity-50 disabled:cursor-not-allowed',
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  )
})
```

- [ ] **Step 4: Run test, expect pass**

```bash
npx vitest run src/components/ui/IconButton.test.tsx
```

Expected: 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ui/IconButton.tsx frontend/src/components/ui/IconButton.test.tsx
git commit -m "feat(frontend/ui): IconButton primitive

44×44 tap target, aria-label TS-required."
```

---

## Task 7: Chip primitive

**Files:**
- Create: `frontend/src/components/ui/Chip.tsx`
- Create: `frontend/src/components/ui/Chip.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Chip } from './Chip'

describe('Chip', () => {
  it('renders label and count', () => {
    render(<Chip count={12}>Pending</Chip>)
    expect(screen.getByText('Pending')).toBeInTheDocument()
    expect(screen.getByText('12')).toBeInTheDocument()
  })

  it('renders without count when omitted', () => {
    render(<Chip>Applied</Chip>)
    expect(screen.getByText('Applied')).toBeInTheDocument()
    expect(screen.queryByText(/^\d+$/)).not.toBeInTheDocument()
  })

  it('signals selected state via aria-pressed and accent class', () => {
    render(<Chip selected>Sel</Chip>)
    const btn = screen.getByRole('button', { name: 'Sel' })
    expect(btn).toHaveAttribute('aria-pressed', 'true')
    expect(btn.className).toMatch(/bg-accent/)
  })

  it('non-selected reports aria-pressed=false', () => {
    render(<Chip>Off</Chip>)
    expect(screen.getByRole('button', { name: 'Off' })).toHaveAttribute('aria-pressed', 'false')
  })

  it('calls onClick', async () => {
    const fn = vi.fn()
    const user = userEvent.setup()
    render(<Chip onClick={fn}>Hit</Chip>)
    await user.click(screen.getByRole('button'))
    expect(fn).toHaveBeenCalled()
  })
})
```

- [ ] **Step 2: Run test, expect failure**

```bash
npx vitest run src/components/ui/Chip.test.tsx
```

Expected: FAIL.

- [ ] **Step 3: Implement Chip**

```tsx
import { ButtonHTMLAttributes, forwardRef, ReactNode } from 'react'
import { cn } from '../../lib/cn'

export interface ChipProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children'> {
  selected?: boolean
  count?: number
  children: ReactNode
}

export const Chip = forwardRef<HTMLButtonElement, ChipProps>(function Chip(
  { selected = false, count, className, children, type, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type ?? 'button'}
      aria-pressed={selected}
      className={cn(
        'inline-flex items-center gap-1.5 px-3 py-1.5 rounded-pill border text-sm transition-colors duration-[var(--t-fast)] min-h-[32px]',
        selected
          ? 'bg-accent/15 text-accent border-accent/40'
          : 'bg-surface text-muted border-border hover:text-text',
        className,
      )}
      {...rest}
    >
      <span>{children}</span>
      {count !== undefined && (
        <span
          className={cn(
            'px-1.5 rounded-pill text-xs',
            selected ? 'bg-accent/30 text-accent' : 'bg-bg text-subtle',
          )}
        >
          {count}
        </span>
      )}
    </button>
  )
})
```

- [ ] **Step 4: Run test, expect pass**

```bash
npx vitest run src/components/ui/Chip.test.tsx
```

Expected: 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ui/Chip.tsx frontend/src/components/ui/Chip.test.tsx
git commit -m "feat(frontend/ui): Chip primitive

Toggle look with optional baked-in count, accent treatment when selected."
```

---

## Task 8: Badge primitive

**Files:**
- Create: `frontend/src/components/ui/Badge.tsx`
- Create: `frontend/src/components/ui/Badge.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { Badge } from './Badge'

describe('Badge', () => {
  it('renders children', () => {
    render(<Badge>87% match</Badge>)
    expect(screen.getByText('87% match')).toBeInTheDocument()
  })

  it('uses success colors by default', () => {
    const { container } = render(<Badge>x</Badge>)
    expect(container.firstChild).toHaveClass('text-success')
  })

  it('renders warning intent', () => {
    const { container } = render(<Badge intent="warning">x</Badge>)
    expect(container.firstChild).toHaveClass('text-warning')
  })

  it('renders danger intent', () => {
    const { container } = render(<Badge intent="danger">x</Badge>)
    expect(container.firstChild).toHaveClass('text-danger')
  })

  it('renders muted intent', () => {
    const { container } = render(<Badge intent="muted">x</Badge>)
    expect(container.firstChild).toHaveClass('text-muted')
  })
})
```

- [ ] **Step 2: Run test, expect failure**

```bash
npx vitest run src/components/ui/Badge.test.tsx
```

Expected: FAIL.

- [ ] **Step 3: Implement Badge**

```tsx
import { HTMLAttributes, ReactNode } from 'react'
import { cn } from '../../lib/cn'

export type BadgeIntent = 'success' | 'warning' | 'danger' | 'muted'

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  intent?: BadgeIntent
  children: ReactNode
}

const intentClass: Record<BadgeIntent, string> = {
  success: 'bg-success/10 text-success',
  warning: 'bg-warning/10 text-warning',
  danger:  'bg-danger/10 text-danger',
  muted:   'bg-surface-2 text-muted',
}

export function Badge({ intent = 'success', className, children, ...rest }: BadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center px-2 py-0.5 rounded-sm-token text-xs font-semibold',
        intentClass[intent],
        className,
      )}
      {...rest}
    >
      {children}
    </span>
  )
}
```

- [ ] **Step 4: Run test, expect pass**

```bash
npx vitest run src/components/ui/Badge.test.tsx
```

Expected: 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ui/Badge.tsx frontend/src/components/ui/Badge.test.tsx
git commit -m "feat(frontend/ui): Badge primitive

Non-interactive label with semantic color intents."
```

---

## Task 9: Card primitive

**Files:**
- Create: `frontend/src/components/ui/Card.tsx`
- Create: `frontend/src/components/ui/Card.test.tsx`

`Card` supports both inert (`<div>`) and interactive (`<a>` for real link semantics) renderings. Browser features that depend on real anchors — long-press preview on iOS, middle-click open-in-new-tab, right-click context menu — only work when the underlying element is `<a href>`. We honor that.

- [ ] **Step 1: Write the failing test**

```tsx
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { Card } from './Card'

describe('Card', () => {
  it('renders as a div by default', () => {
    render(<Card data-testid="c">child</Card>)
    expect(screen.getByTestId('c').tagName).toBe('DIV')
  })

  it('renders as an anchor when as="a" is provided', () => {
    render(<Card data-testid="c" as="a" href="/path">link</Card>)
    const el = screen.getByTestId('c')
    expect(el.tagName).toBe('A')
    expect(el).toHaveAttribute('href', '/path')
  })

  it('renders as a react-router Link when as="rrlink" is provided', () => {
    render(
      <MemoryRouter>
        <Card data-testid="c" as="rrlink" to="/route">link</Card>
      </MemoryRouter>
    )
    const el = screen.getByTestId('c')
    expect(el.tagName).toBe('A')
    expect(el).toHaveAttribute('href', '/route')
  })

  it('applies surface + border by default', () => {
    render(<Card data-testid="c">x</Card>)
    expect(screen.getByTestId('c').className).toMatch(/bg-surface/)
    expect(screen.getByTestId('c').className).toMatch(/border-border/)
  })

  it('adds interactive hover class when interactive', () => {
    render(<Card data-testid="c" interactive>x</Card>)
    expect(screen.getByTestId('c').className).toMatch(/hover:border-border-strong/)
    expect(screen.getByTestId('c').className).toMatch(/cursor-pointer/)
  })
})
```

- [ ] **Step 2: Run test, expect failure**

```bash
npx vitest run src/components/ui/Card.test.tsx
```

Expected: FAIL.

- [ ] **Step 3: Implement Card**

```tsx
import { AnchorHTMLAttributes, HTMLAttributes, ReactNode } from 'react'
import { Link, LinkProps } from 'react-router-dom'
import { cn } from '../../lib/cn'

type Common = {
  interactive?: boolean
  className?: string
  children: ReactNode
}

type DivCardProps = Common & HTMLAttributes<HTMLDivElement> & { as?: 'div' }
type AnchorCardProps = Common & AnchorHTMLAttributes<HTMLAnchorElement> & { as: 'a' }
type RRLinkCardProps = Common & LinkProps & { as: 'rrlink' }

export type CardProps = DivCardProps | AnchorCardProps | RRLinkCardProps

const baseClass =
  'block bg-surface border border-border rounded-lg-token p-4 transition-colors duration-[var(--t-fast)]'

const interactiveClass = 'cursor-pointer hover:border-border-strong'

export function Card(props: CardProps) {
  if (props.as === 'a') {
    const { as: _as, interactive, className, children, ...rest } = props
    return (
      <a
        className={cn(baseClass, interactive && interactiveClass, className)}
        {...rest}
      >
        {children}
      </a>
    )
  }
  if (props.as === 'rrlink') {
    const { as: _as, interactive, className, children, ...rest } = props
    return (
      <Link
        className={cn(baseClass, interactive && interactiveClass, className)}
        {...rest}
      >
        {children}
      </Link>
    )
  }
  // default: div
  const { as: _as, interactive, className, children, ...rest } = props as DivCardProps
  return (
    <div
      className={cn(baseClass, interactive && interactiveClass, className)}
      {...rest}
    >
      {children}
    </div>
  )
}
```

- [ ] **Step 4: Run test, expect pass**

```bash
npx vitest run src/components/ui/Card.test.tsx
```

Expected: 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ui/Card.tsx frontend/src/components/ui/Card.test.tsx
git commit -m "feat(frontend/ui): Card primitive

div / anchor / react-router Link renderings via discriminated as prop.
Real anchor preserves long-press / middle-click / right-click."
```

---

## Task 10: ActionSheet primitive

**Files:**
- Create: `frontend/src/components/ui/ActionSheet.tsx`
- Create: `frontend/src/components/ui/ActionSheet.test.tsx`

ActionSheet is a controlled overlay: parent owns `open` state and `onClose`. On mobile (≤md) it's a bottom sheet; on desktop it's a centered popover. Backdrop tap closes; Escape closes; focus is trapped.

- [ ] **Step 1: Write the failing test**

```tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ActionSheet } from './ActionSheet'

describe('ActionSheet', () => {
  it('renders nothing when closed', () => {
    render(
      <ActionSheet open={false} onClose={() => {}} title="Choose">
        <button>Item</button>
      </ActionSheet>
    )
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('renders dialog with title and content when open', () => {
    render(
      <ActionSheet open onClose={() => {}} title="Choose">
        <button>Item A</button>
      </ActionSheet>
    )
    const dlg = screen.getByRole('dialog')
    expect(dlg).toBeInTheDocument()
    expect(dlg).toHaveAttribute('aria-label', 'Choose')
    expect(screen.getByText('Item A')).toBeInTheDocument()
  })

  it('calls onClose when Escape is pressed', async () => {
    const onClose = vi.fn()
    const user = userEvent.setup()
    render(
      <ActionSheet open onClose={onClose} title="x">
        <button>Item</button>
      </ActionSheet>
    )
    await user.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalled()
  })

  it('calls onClose when backdrop is clicked', async () => {
    const onClose = vi.fn()
    const user = userEvent.setup()
    render(
      <ActionSheet open onClose={onClose} title="x">
        <button>Item</button>
      </ActionSheet>
    )
    await user.click(screen.getByTestId('actionsheet-backdrop'))
    expect(onClose).toHaveBeenCalled()
  })

  it('does NOT call onClose when sheet content is clicked', async () => {
    const onClose = vi.fn()
    const user = userEvent.setup()
    render(
      <ActionSheet open onClose={onClose} title="x">
        <button>Item</button>
      </ActionSheet>
    )
    await user.click(screen.getByText('Item'))
    expect(onClose).not.toHaveBeenCalled()
  })
})
```

- [ ] **Step 2: Run test, expect failure**

```bash
npx vitest run src/components/ui/ActionSheet.test.tsx
```

Expected: FAIL.

- [ ] **Step 3: Implement ActionSheet**

```tsx
import { ReactNode, useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import { cn } from '../../lib/cn'

export interface ActionSheetProps {
  open: boolean
  onClose: () => void
  /** Accessible label for the dialog. Not necessarily rendered. */
  title: string
  /** Optional visible heading rendered at the top of the sheet. */
  heading?: ReactNode
  children: ReactNode
}

export function ActionSheet({ open, onClose, title, heading, children }: ActionSheetProps) {
  const sheetRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    // Move initial focus into the sheet so Tab cycles within
    sheetRef.current?.focus()
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-end md:items-center md:justify-center"
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <div
        data-testid="actionsheet-backdrop"
        className="absolute inset-0 bg-black/60"
        onClick={onClose}
      />
      <div
        ref={sheetRef}
        tabIndex={-1}
        className={cn(
          'relative bg-surface-2 border border-border w-full max-w-md',
          // Mobile: bottom sheet with rounded top
          'rounded-t-lg-token md:rounded-lg-token',
          // Desktop: centered popover
          'md:max-w-sm',
          'p-2 outline-none',
        )}
      >
        {/* Drag handle (decorative on mobile) */}
        <div className="md:hidden flex justify-center pt-2 pb-3">
          <span className="block w-10 h-1 rounded-pill bg-border-strong" />
        </div>
        {heading && (
          <div className="px-3 pb-2 text-sm font-semibold text-text">{heading}</div>
        )}
        <div className="flex flex-col">{children}</div>
      </div>
    </div>,
    document.body,
  )
}

/** Convenience item used inside an ActionSheet. */
export interface ActionSheetItemProps {
  onClick?: () => void
  intent?: 'default' | 'danger'
  children: ReactNode
}

export function ActionSheetItem({ onClick, intent = 'default', children }: ActionSheetItemProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'text-left px-4 py-3 text-sm border-b border-border last:border-b-0 hover:bg-surface',
        intent === 'danger' ? 'text-danger' : 'text-text',
      )}
    >
      {children}
    </button>
  )
}
```

- [ ] **Step 4: Run test, expect pass**

```bash
npx vitest run src/components/ui/ActionSheet.test.tsx
```

Expected: 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ui/ActionSheet.tsx frontend/src/components/ui/ActionSheet.test.tsx
git commit -m "feat(frontend/ui): ActionSheet + ActionSheetItem primitive

Bottom sheet on mobile, centered popover on desktop. Backdrop and
Escape close; portal-rendered into document.body."
```

---

## Task 11: Drawer primitive

**Files:**
- Create: `frontend/src/components/ui/Drawer.tsx`
- Create: `frontend/src/components/ui/Drawer.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Drawer } from './Drawer'

describe('Drawer', () => {
  it('renders nothing when closed', () => {
    render(
      <Drawer open={false} onClose={() => {}} title="Coach">
        <p>chat</p>
      </Drawer>
    )
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('renders dialog with title and close button when open', () => {
    render(
      <Drawer open onClose={() => {}} title="Coach">
        <p>chat content</p>
      </Drawer>
    )
    const dlg = screen.getByRole('dialog')
    expect(dlg).toHaveAttribute('aria-label', 'Coach')
    expect(screen.getByRole('button', { name: 'Close drawer' })).toBeInTheDocument()
    expect(screen.getByText('chat content')).toBeInTheDocument()
  })

  it('calls onClose on close button', async () => {
    const onClose = vi.fn()
    const user = userEvent.setup()
    render(
      <Drawer open onClose={onClose} title="x">
        <p>x</p>
      </Drawer>
    )
    await user.click(screen.getByRole('button', { name: 'Close drawer' }))
    expect(onClose).toHaveBeenCalled()
  })

  it('calls onClose on Escape', async () => {
    const onClose = vi.fn()
    const user = userEvent.setup()
    render(
      <Drawer open onClose={onClose} title="x">
        <p>x</p>
      </Drawer>
    )
    await user.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalled()
  })

  it('calls onClose on backdrop click', async () => {
    const onClose = vi.fn()
    const user = userEvent.setup()
    render(
      <Drawer open onClose={onClose} title="x">
        <p>x</p>
      </Drawer>
    )
    await user.click(screen.getByTestId('drawer-backdrop'))
    expect(onClose).toHaveBeenCalled()
  })
})
```

- [ ] **Step 2: Run test, expect failure**

```bash
npx vitest run src/components/ui/Drawer.test.tsx
```

Expected: FAIL.

- [ ] **Step 3: Implement Drawer**

```tsx
import { ReactNode, useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import { IconButton } from './IconButton'
import { Close } from './icons'
import { cn } from '../../lib/cn'

export interface DrawerProps {
  open: boolean
  onClose: () => void
  /** Accessible name for the dialog and visible header. */
  title: string
  children: ReactNode
  /** Optional class added to the inner panel — e.g. for layout overrides. */
  className?: string
}

export function Drawer({ open, onClose, title, children, className }: DrawerProps) {
  const panelRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    panelRef.current?.focus()
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  return createPortal(
    <div className="fixed inset-0 z-50" role="dialog" aria-modal="true" aria-label={title}>
      <div
        data-testid="drawer-backdrop"
        className="absolute inset-0 bg-black/60"
        onClick={onClose}
      />
      <div
        ref={panelRef}
        tabIndex={-1}
        className={cn(
          'absolute top-0 bottom-0 right-0 bg-surface border-l border-border',
          // Mobile: full takeover. Desktop: 420px right rail.
          'w-full md:w-[420px]',
          'flex flex-col outline-none',
          className,
        )}
      >
        <header className="flex items-center justify-between p-3 border-b border-border">
          <h2 className="text-base font-semibold text-text">{title}</h2>
          <IconButton aria-label="Close drawer" onClick={onClose}>
            <Close className="w-4 h-4" />
          </IconButton>
        </header>
        <div className="flex-1 overflow-y-auto">{children}</div>
      </div>
    </div>,
    document.body,
  )
}
```

This file imports `./icons` — that's Task 18, not yet done. Add a placeholder Close icon now so the file compiles:

Create `frontend/src/components/ui/icons/Close.tsx`:

```tsx
import { SVGAttributes } from 'react'
export function Close(props: SVGAttributes<SVGElement>) {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" {...props}>
      <path d="M3 3l10 10M13 3L3 13" strokeLinecap="round" />
    </svg>
  )
}
```

Create `frontend/src/components/ui/icons/index.ts` (will gain more entries in Task 18):

```ts
export { Close } from './Close'
```

- [ ] **Step 4: Run test, expect pass**

```bash
npx vitest run src/components/ui/Drawer.test.tsx
```

Expected: 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ui/Drawer.tsx frontend/src/components/ui/Drawer.test.tsx frontend/src/components/ui/icons/Close.tsx frontend/src/components/ui/icons/index.ts
git commit -m "feat(frontend/ui): Drawer primitive + Close icon

Right rail on desktop (420px), full viewport on mobile. Backdrop /
Escape / X close. Close icon is the first inline-SVG entry in
ui/icons/."
```

---

## Task 12: Toast primitive (Provider + hook + component)

**Files:**
- Create: `frontend/src/components/ui/Toast.tsx`
- Create: `frontend/src/components/ui/Toast.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ToastProvider, useToast } from './Toast'

function Probe() {
  const toast = useToast()
  return (
    <div>
      <button onClick={() => toast.show('Saved', 'success')}>say-success</button>
      <button onClick={() => toast.show('Boom', 'error')}>say-error</button>
    </div>
  )
}

describe('Toast', () => {
  beforeEach(() => { vi.useFakeTimers() })
  afterEach(() => { vi.useRealTimers() })

  it('renders nothing when no toasts queued', () => {
    render(
      <ToastProvider>
        <Probe />
      </ToastProvider>
    )
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('shows a success toast on demand', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    render(
      <ToastProvider>
        <Probe />
      </ToastProvider>
    )
    await user.click(screen.getByText('say-success'))
    const t = screen.getByRole('status')
    expect(t).toHaveTextContent('Saved')
    expect(t.className).toMatch(/border-success/)
  })

  it('shows an error toast with the danger border', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    render(
      <ToastProvider>
        <Probe />
      </ToastProvider>
    )
    await user.click(screen.getByText('say-error'))
    expect(screen.getByRole('status').className).toMatch(/border-danger/)
  })

  it('auto-dismisses after 5s', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    render(
      <ToastProvider>
        <Probe />
      </ToastProvider>
    )
    await user.click(screen.getByText('say-success'))
    expect(screen.getByRole('status')).toBeInTheDocument()
    act(() => { vi.advanceTimersByTime(5_000) })
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('throws useful error if useToast() called outside provider', () => {
    function Bare() {
      // eslint-disable-next-line @typescript-eslint/no-unused-vars
      const _t = useToast()
      return null
    }
    expect(() => render(<Bare />)).toThrow(/ToastProvider/)
  })
})
```

- [ ] **Step 2: Run test, expect failure**

```bash
npx vitest run src/components/ui/Toast.test.tsx
```

Expected: FAIL.

- [ ] **Step 3: Implement Toast**

```tsx
import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { cn } from '../../lib/cn'

export type ToastIntent = 'success' | 'error' | 'info'

interface ToastEntry {
  id: number
  message: string
  intent: ToastIntent
}

interface ToastContextValue {
  show: (message: string, intent?: ToastIntent) => void
}

const ToastContext = createContext<ToastContextValue | null>(null)

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext)
  if (!ctx) throw new Error('useToast() must be used inside <ToastProvider>')
  return ctx
}

const AUTO_DISMISS_MS = 5_000

const intentClass: Record<ToastIntent, string> = {
  success: 'border-l-success',
  error:   'border-l-danger',
  info:    'border-l-accent',
}

let nextId = 1

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastEntry[]>([])

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id))
  }, [])

  const show = useCallback((message: string, intent: ToastIntent = 'info') => {
    const id = nextId++
    setToasts((prev) => [...prev, { id, message, intent }])
    setTimeout(() => dismiss(id), AUTO_DISMISS_MS)
  }, [dismiss])

  const value = useMemo(() => ({ show }), [show])

  return (
    <ToastContext.Provider value={value}>
      {children}
      {createPortal(
        <div className="fixed z-50 flex flex-col gap-2 pointer-events-none
                        bottom-4 left-1/2 -translate-x-1/2
                        md:left-auto md:right-4 md:translate-x-0">
          {toasts.map((t) => (
            <div
              key={t.id}
              role="status"
              className={cn(
                'pointer-events-auto bg-surface-2 border border-border border-l-4 px-4 py-3',
                'rounded-md-token text-sm text-text max-w-xs shadow-lg',
                intentClass[t.intent],
              )}
            >
              {t.message}
            </div>
          ))}
        </div>,
        document.body,
      )}
    </ToastContext.Provider>
  )
}

// Suppress unused-import warning for `useEffect` placeholder if we don't need it elsewhere.
void useEffect
```

(The `void useEffect` line is there to keep the import — it's used by the auto-dismiss; remove if you find it unused after implementing.) Actually delete the `void useEffect` line; no `useEffect` is needed here since `setTimeout` runs on mount.

Final file (clean):

```tsx
import { createContext, ReactNode, useCallback, useContext, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { cn } from '../../lib/cn'

export type ToastIntent = 'success' | 'error' | 'info'

interface ToastEntry { id: number; message: string; intent: ToastIntent }

interface ToastContextValue { show: (message: string, intent?: ToastIntent) => void }

const ToastContext = createContext<ToastContextValue | null>(null)

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext)
  if (!ctx) throw new Error('useToast() must be used inside <ToastProvider>')
  return ctx
}

const AUTO_DISMISS_MS = 5_000

const intentClass: Record<ToastIntent, string> = {
  success: 'border-l-success',
  error:   'border-l-danger',
  info:    'border-l-accent',
}

let nextId = 1

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastEntry[]>([])

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id))
  }, [])

  const show = useCallback((message: string, intent: ToastIntent = 'info') => {
    const id = nextId++
    setToasts((prev) => [...prev, { id, message, intent }])
    setTimeout(() => dismiss(id), AUTO_DISMISS_MS)
  }, [dismiss])

  const value = useMemo(() => ({ show }), [show])

  return (
    <ToastContext.Provider value={value}>
      {children}
      {createPortal(
        <div className="fixed z-50 flex flex-col gap-2 pointer-events-none
                        bottom-4 left-1/2 -translate-x-1/2
                        md:left-auto md:right-4 md:translate-x-0">
          {toasts.map((t) => (
            <div
              key={t.id}
              role="status"
              className={cn(
                'pointer-events-auto bg-surface-2 border border-border border-l-4 px-4 py-3',
                'rounded-md-token text-sm text-text max-w-xs shadow-lg',
                intentClass[t.intent],
              )}
            >
              {t.message}
            </div>
          ))}
        </div>,
        document.body,
      )}
    </ToastContext.Provider>
  )
}
```

- [ ] **Step 4: Run test, expect pass**

```bash
npx vitest run src/components/ui/Toast.test.tsx
```

Expected: 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ui/Toast.tsx frontend/src/components/ui/Toast.test.tsx
git commit -m "feat(frontend/ui): Toast primitive + ToastProvider + useToast()

Auto-dismiss 5s, success/error/info intents, bottom-center on mobile,
bottom-right on desktop, portal-rendered."
```

---

## Task 13: EmptyState primitive

**Files:**
- Create: `frontend/src/components/ui/EmptyState.tsx`
- Create: `frontend/src/components/ui/EmptyState.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { EmptyState } from './EmptyState'

describe('EmptyState', () => {
  it('renders title and description', () => {
    render(<EmptyState title="Caught up" description="No new matches" />)
    expect(screen.getByText('Caught up')).toBeInTheDocument()
    expect(screen.getByText('No new matches')).toBeInTheDocument()
  })

  it('renders an icon when provided', () => {
    render(<EmptyState title="x" description="y" icon={<span data-testid="ic">○</span>} />)
    expect(screen.getByTestId('ic')).toBeInTheDocument()
  })

  it('renders an action node when provided', () => {
    render(
      <EmptyState
        title="x"
        description="y"
        action={<button>Sync now</button>}
      />
    )
    expect(screen.getByText('Sync now')).toBeInTheDocument()
  })

  it('renders without action / icon if absent', () => {
    render(<EmptyState title="x" description="y" />)
    expect(screen.getByText('x')).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test, expect failure**

```bash
npx vitest run src/components/ui/EmptyState.test.tsx
```

Expected: FAIL.

- [ ] **Step 3: Implement EmptyState**

```tsx
import { ReactNode } from 'react'
import { cn } from '../../lib/cn'

export interface EmptyStateProps {
  title: string
  description: string
  icon?: ReactNode
  action?: ReactNode
  className?: string
}

export function EmptyState({ title, description, icon, action, className }: EmptyStateProps) {
  return (
    <div className={cn('text-center py-10 px-6 text-muted', className)}>
      {icon && <div className="text-2xl text-subtle mb-2">{icon}</div>}
      <p className="text-base font-semibold text-text mb-1">{title}</p>
      <p className="text-sm mb-4">{description}</p>
      {action}
    </div>
  )
}
```

- [ ] **Step 4: Run test, expect pass**

```bash
npx vitest run src/components/ui/EmptyState.test.tsx
```

Expected: 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ui/EmptyState.tsx frontend/src/components/ui/EmptyState.test.tsx
git commit -m "feat(frontend/ui): EmptyState primitive

Title, description, optional icon, optional action."
```

---

## Task 14: TextField primitive

**Files:**
- Create: `frontend/src/components/ui/TextField.tsx`
- Create: `frontend/src/components/ui/TextField.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { TextField } from './TextField'

describe('TextField', () => {
  it('renders with label and associates it via htmlFor/id', () => {
    render(<TextField label="Email" />)
    const input = screen.getByLabelText('Email')
    expect(input).toBeInTheDocument()
    expect(input.tagName).toBe('INPUT')
  })

  it('reflects the typed value', async () => {
    const user = userEvent.setup()
    render(<TextField label="Name" />)
    const input = screen.getByLabelText('Name')
    await user.type(input, 'Maks')
    expect(input).toHaveValue('Maks')
  })

  it('marks aria-invalid when error is provided', () => {
    render(<TextField label="X" error="bad" />)
    expect(screen.getByLabelText('X')).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByText('bad')).toBeInTheDocument()
  })

  it('does not set aria-invalid when error is absent', () => {
    render(<TextField label="X" />)
    expect(screen.getByLabelText('X')).not.toHaveAttribute('aria-invalid')
  })
})
```

- [ ] **Step 2: Run test, expect failure**

```bash
npx vitest run src/components/ui/TextField.test.tsx
```

Expected: FAIL.

- [ ] **Step 3: Implement TextField**

```tsx
import { forwardRef, InputHTMLAttributes, useId } from 'react'
import { cn } from '../../lib/cn'

export interface TextFieldProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string
  error?: string
}

export const TextField = forwardRef<HTMLInputElement, TextFieldProps>(function TextField(
  { label, error, id, className, ...rest },
  ref,
) {
  const auto = useId()
  const inputId = id ?? auto

  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={inputId} className="text-xs text-muted">{label}</label>
      <input
        ref={ref}
        id={inputId}
        aria-invalid={error ? true : undefined}
        className={cn(
          'bg-surface border border-border rounded-md-token px-3 py-2.5 text-sm text-text',
          'min-h-[44px] focus:outline-2 focus:outline-accent/40 focus:outline-offset-2 focus:border-accent',
          error && 'border-danger',
          className,
        )}
        {...rest}
      />
      {error && <span className="text-xs text-danger">{error}</span>}
    </div>
  )
})
```

- [ ] **Step 4: Run test, expect pass**

```bash
npx vitest run src/components/ui/TextField.test.tsx
```

Expected: 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ui/TextField.tsx frontend/src/components/ui/TextField.test.tsx
git commit -m "feat(frontend/ui): TextField primitive

Always-present label via useId binding, aria-invalid on error,
44px min height for tap."
```

---

## Task 15: TextArea primitive (auto-resize)

**Files:**
- Create: `frontend/src/components/ui/TextArea.tsx`
- Create: `frontend/src/components/ui/TextArea.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { TextArea } from './TextArea'

describe('TextArea', () => {
  it('renders with label', () => {
    render(<TextArea label="Notes" />)
    expect(screen.getByLabelText('Notes')).toBeInTheDocument()
  })

  it('reflects typed value', async () => {
    const user = userEvent.setup()
    render(<TextArea label="x" />)
    const ta = screen.getByLabelText('x')
    await user.type(ta, 'hi')
    expect(ta).toHaveValue('hi')
  })

  it('shows error and sets aria-invalid', () => {
    render(<TextArea label="x" error="too long" />)
    expect(screen.getByLabelText('x')).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByText('too long')).toBeInTheDocument()
  })

  it('respects passed rows when set', () => {
    render(<TextArea label="x" rows={6} />)
    expect(screen.getByLabelText('x')).toHaveAttribute('rows', '6')
  })
})
```

- [ ] **Step 2: Run test, expect failure**

```bash
npx vitest run src/components/ui/TextArea.test.tsx
```

Expected: FAIL.

- [ ] **Step 3: Implement TextArea**

```tsx
import { forwardRef, TextareaHTMLAttributes, useEffect, useId, useRef } from 'react'
import { cn } from '../../lib/cn'

export interface TextAreaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  label: string
  error?: string
}

export const TextArea = forwardRef<HTMLTextAreaElement, TextAreaProps>(function TextArea(
  { label, error, id, className, value, rows, onChange, ...rest },
  forwardedRef,
) {
  const auto = useId()
  const inputId = id ?? auto
  const localRef = useRef<HTMLTextAreaElement | null>(null)

  // Auto-resize: grow to fit content. Caller can still pass rows= for an
  // initial floor; we only grow beyond that.
  useEffect(() => {
    const el = localRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${el.scrollHeight}px`
  }, [value])

  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={inputId} className="text-xs text-muted">{label}</label>
      <textarea
        ref={(node) => {
          localRef.current = node
          if (typeof forwardedRef === 'function') forwardedRef(node)
          else if (forwardedRef) forwardedRef.current = node
        }}
        id={inputId}
        rows={rows ?? 3}
        aria-invalid={error ? true : undefined}
        value={value}
        onChange={onChange}
        className={cn(
          'bg-surface border border-border rounded-md-token px-3 py-2.5 text-sm text-text font-mono',
          'focus:outline-2 focus:outline-accent/40 focus:outline-offset-2 focus:border-accent',
          'resize-none overflow-hidden leading-relaxed',
          error && 'border-danger',
          className,
        )}
        {...rest}
      />
      {error && <span className="text-xs text-danger">{error}</span>}
    </div>
  )
})
```

- [ ] **Step 4: Run test, expect pass**

```bash
npx vitest run src/components/ui/TextArea.test.tsx
```

Expected: 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ui/TextArea.tsx frontend/src/components/ui/TextArea.test.tsx
git commit -m "feat(frontend/ui): TextArea primitive

Auto-resize on value change, mono font, label binding via useId."
```

---

## Task 16: Skeleton primitive

**Files:**
- Create: `frontend/src/components/ui/Skeleton.tsx`
- Create: `frontend/src/components/ui/Skeleton.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { SkeletonLine, SkeletonCard } from './Skeleton'

describe('Skeleton', () => {
  it('SkeletonLine renders with shimmer class', () => {
    render(<SkeletonLine data-testid="s" />)
    expect(screen.getByTestId('s').className).toMatch(/animate-pulse/)
  })

  it('SkeletonLine respects width and height props', () => {
    render(<SkeletonLine data-testid="s" width="50%" height={20} />)
    const el = screen.getByTestId('s')
    expect(el.style.width).toBe('50%')
    expect(el.style.height).toBe('20px')
  })

  it('SkeletonCard renders multiple lines', () => {
    const { container } = render(<SkeletonCard />)
    const lines = container.querySelectorAll('[data-skel-line]')
    expect(lines.length).toBeGreaterThanOrEqual(3)
  })
})
```

- [ ] **Step 2: Run test, expect failure**

```bash
npx vitest run src/components/ui/Skeleton.test.tsx
```

Expected: FAIL.

- [ ] **Step 3: Implement Skeleton**

```tsx
import { HTMLAttributes } from 'react'
import { cn } from '../../lib/cn'

export interface SkeletonLineProps extends HTMLAttributes<HTMLDivElement> {
  width?: string | number
  height?: string | number
}

export function SkeletonLine({ width, height, style, className, ...rest }: SkeletonLineProps) {
  const merged = {
    width: typeof width === 'number' ? `${width}px` : width,
    height: typeof height === 'number' ? `${height}px` : height ?? '12px',
    ...style,
  }
  return (
    <div
      data-skel-line
      className={cn('bg-surface-2 rounded-sm-token animate-pulse', className)}
      style={merged}
      {...rest}
    />
  )
}

export function SkeletonCard() {
  return (
    <div className="bg-surface border border-border rounded-lg-token p-4 space-y-2">
      <SkeletonLine width="30%" height={14} />
      <SkeletonLine width="70%" height={18} />
      <SkeletonLine width="40%" height={14} />
    </div>
  )
}
```

- [ ] **Step 4: Run test, expect pass**

```bash
npx vitest run src/components/ui/Skeleton.test.tsx
```

Expected: 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ui/Skeleton.tsx frontend/src/components/ui/Skeleton.test.tsx
git commit -m "feat(frontend/ui): Skeleton primitives (Line + Card)

Shimmer placeholder for loading lists."
```

---

## Task 17: SwipeableCard primitive

**Files:**
- Create: `frontend/src/components/ui/SwipeableCard.tsx`
- Create: `frontend/src/components/ui/SwipeableCard.test.tsx`

The most complex primitive. Wraps content in a horizontally-translatable surface; pointer-down → drag → pointer-up commits if past threshold (24px), else springs back. Reveals an action under the surface when partially swiped.

- [ ] **Step 1: Write the failing test**

```tsx
import { describe, it, expect, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { SwipeableCard } from './SwipeableCard'

describe('SwipeableCard', () => {
  it('renders children and the revealed action', () => {
    render(
      <SwipeableCard onCommit={() => {}} actionLabel="Dismiss">
        <div>card content</div>
      </SwipeableCard>
    )
    expect(screen.getByText('card content')).toBeInTheDocument()
    expect(screen.getByText('Dismiss')).toBeInTheDocument()
  })

  it('calls onCommit when dragged past threshold and released', () => {
    const onCommit = vi.fn()
    render(
      <SwipeableCard onCommit={onCommit} actionLabel="Dismiss" thresholdPx={24}>
        <div>x</div>
      </SwipeableCard>
    )
    const surface = screen.getByTestId('swipe-surface')
    fireEvent.pointerDown(surface, { clientX: 100, pointerId: 1 })
    fireEvent.pointerMove(surface, { clientX: 60, pointerId: 1 })   // -40px, past threshold
    fireEvent.pointerUp(surface, { clientX: 60, pointerId: 1 })
    expect(onCommit).toHaveBeenCalled()
  })

  it('does NOT commit when released before threshold', () => {
    const onCommit = vi.fn()
    render(
      <SwipeableCard onCommit={onCommit} actionLabel="Dismiss" thresholdPx={24}>
        <div>x</div>
      </SwipeableCard>
    )
    const surface = screen.getByTestId('swipe-surface')
    fireEvent.pointerDown(surface, { clientX: 100, pointerId: 1 })
    fireEvent.pointerMove(surface, { clientX: 90, pointerId: 1 })   // -10px
    fireEvent.pointerUp(surface, { clientX: 90, pointerId: 1 })
    expect(onCommit).not.toHaveBeenCalled()
  })

  it('does not capture rightward drag (we only dismiss leftward)', () => {
    const onCommit = vi.fn()
    render(
      <SwipeableCard onCommit={onCommit} actionLabel="Dismiss" thresholdPx={24}>
        <div>x</div>
      </SwipeableCard>
    )
    const surface = screen.getByTestId('swipe-surface')
    fireEvent.pointerDown(surface, { clientX: 100, pointerId: 1 })
    fireEvent.pointerMove(surface, { clientX: 200, pointerId: 1 })  // +100px
    fireEvent.pointerUp(surface, { clientX: 200, pointerId: 1 })
    expect(onCommit).not.toHaveBeenCalled()
  })
})
```

- [ ] **Step 2: Run test, expect failure**

```bash
npx vitest run src/components/ui/SwipeableCard.test.tsx
```

Expected: FAIL.

- [ ] **Step 3: Implement SwipeableCard**

```tsx
import { ReactNode, useRef, useState } from 'react'
import { cn } from '../../lib/cn'

export interface SwipeableCardProps {
  children: ReactNode
  /** Called when the user swipes far enough left and releases. */
  onCommit: () => void
  /** Visible label of the action revealed underneath. */
  actionLabel: string
  /** Pixels of leftward travel required to commit. */
  thresholdPx?: number
  className?: string
}

export function SwipeableCard({
  children,
  onCommit,
  actionLabel,
  thresholdPx = 24,
  className,
}: SwipeableCardProps) {
  const [dx, setDx] = useState(0)
  const startX = useRef<number | null>(null)

  function onPointerDown(e: React.PointerEvent) {
    startX.current = e.clientX
    ;(e.target as Element).setPointerCapture?.(e.pointerId)
  }
  function onPointerMove(e: React.PointerEvent) {
    if (startX.current == null) return
    const delta = e.clientX - startX.current
    // Only translate leftward (delta <= 0). Rightward is ignored.
    setDx(Math.min(0, delta))
  }
  function onPointerUp() {
    if (startX.current == null) return
    if (dx <= -thresholdPx) {
      onCommit()
    }
    setDx(0)
    startX.current = null
  }

  return (
    <div className={cn('relative select-none touch-pan-y', className)}>
      {/* Action surface revealed underneath */}
      <div className="absolute inset-y-0 right-0 w-20 bg-danger flex items-center justify-center text-white text-xs font-bold rounded-r-lg-token">
        {actionLabel}
      </div>
      {/* The draggable surface on top */}
      <div
        data-testid="swipe-surface"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        style={{ transform: `translateX(${dx}px)` }}
        className={cn(
          'relative bg-surface border border-border rounded-lg-token transition-transform duration-[var(--t-fast)] ease-token-ease',
          // While dragging we drop the transition for 1:1 finger tracking
          startX.current != null && 'transition-none',
        )}
      >
        {children}
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Run test, expect pass**

```bash
npx vitest run src/components/ui/SwipeableCard.test.tsx
```

Expected: 4 tests pass. If "does NOT commit before threshold" fails, it's likely because state updates are batched — fix by reading the in-flight delta directly inside `onPointerUp` rather than via React state. Adjusted implementation:

If you hit that issue, change `onPointerUp` to:

```tsx
function onPointerUp(e: React.PointerEvent) {
  if (startX.current == null) return
  const delta = Math.min(0, e.clientX - startX.current)
  if (delta <= -thresholdPx) onCommit()
  setDx(0)
  startX.current = null
}
```

This computes the final delta from the released pointer position directly, side-stepping the state batching question. Re-run tests; expect 4 pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ui/SwipeableCard.tsx frontend/src/components/ui/SwipeableCard.test.tsx
git commit -m "feat(frontend/ui): SwipeableCard primitive

Pointer-event leftward drag with commit threshold; reveals action
surface underneath; rightward drag is ignored. Used by Plan B's
match card."
```

---

## Task 18: Remaining inline icons + barrel re-exports

**Files:**
- Create: `frontend/src/components/ui/icons/Settings.tsx`
- Create: `frontend/src/components/ui/icons/Coach.tsx`
- Create: `frontend/src/components/ui/icons/Hamburger.tsx`
- Create: `frontend/src/components/ui/icons/Kebab.tsx`
- Modify: `frontend/src/components/ui/icons/index.ts`

- [ ] **Step 1: Create Settings icon**

```tsx
import { SVGAttributes } from 'react'
export function Settings(props: SVGAttributes<SVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" {...props}>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.9.3h.1a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.9v.1a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" />
    </svg>
  )
}
```

- [ ] **Step 2: Create Coach icon (✦ sparkle)**

```tsx
import { SVGAttributes } from 'react'
export function Coach(props: SVGAttributes<SVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" {...props}>
      <path d="M12 2l1.8 5.4L19 9l-5.2 1.6L12 16l-1.8-5.4L5 9l5.2-1.6L12 2zM19 16l.7 2.1 2.3.7-2.3.7L19 22l-.7-2.1L16 19.2l2.3-.7L19 16zM5 16l.5 1.5L7 18l-1.5.5L5 20l-.5-1.5L3 18l1.5-.5L5 16z" />
    </svg>
  )
}
```

- [ ] **Step 3: Create Hamburger icon**

```tsx
import { SVGAttributes } from 'react'
export function Hamburger(props: SVGAttributes<SVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" {...props}>
      <path d="M4 6h16M4 12h16M4 18h16" strokeLinecap="round" />
    </svg>
  )
}
```

- [ ] **Step 4: Create Kebab icon**

```tsx
import { SVGAttributes } from 'react'
export function Kebab(props: SVGAttributes<SVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" {...props}>
      <circle cx="12" cy="5" r="1.6" />
      <circle cx="12" cy="12" r="1.6" />
      <circle cx="12" cy="19" r="1.6" />
    </svg>
  )
}
```

- [ ] **Step 5: Update icons barrel**

```ts
export { Close } from './Close'
export { Settings } from './Settings'
export { Coach } from './Coach'
export { Hamburger } from './Hamburger'
export { Kebab } from './Kebab'
```

- [ ] **Step 6: Smoke compile**

```bash
npx tsc --noEmit
```

Expected: no type errors.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/ui/icons/
git commit -m "feat(frontend/ui): inline-SVG icon set (Settings, Coach, Hamburger, Kebab)

Single barrel re-export. No icon-library dependency."
```

---

## Task 19: AppShell — desktop layout (header with inline icons)

**Files:**
- Create: `frontend/src/components/AppShell.tsx`
- Create: `frontend/src/components/AppShell.test.tsx`

`AppShell` wraps the routed content with the new header. Mobile sheet is added in Task 20.

- [ ] **Step 1: Write the failing test**

The desktop nav and hamburger are gated on `useAuth().user`. Mock the hook to return a fake user.

```tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u-1', email: 'maks@example.com' },
    token: 'fake',
    loading: false,
    signOut: vi.fn(),
  }),
  // AuthProvider isn't used by AppShell directly, but export a no-op
  // so any transitive imports during render don't blow up.
  AuthProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}))

import { AppShell } from './AppShell'

function renderShell(pathname = '/') {
  return render(
    <MemoryRouter initialEntries={[pathname]}>
      <AppShell>
        <p>page body</p>
      </AppShell>
    </MemoryRouter>
  )
}

describe('AppShell (desktop)', () => {
  it('renders children inside <main>', () => {
    renderShell()
    expect(screen.getByText('page body')).toBeInTheDocument()
  })

  it('renders the brand link → /', () => {
    renderShell('/anywhere')
    const brand = screen.getByText('Job Agent')
    expect(brand.closest('a')).toHaveAttribute('href', '/')
  })

  it('renders Settings, Coach, Sign-out controls (desktop bar)', () => {
    renderShell()
    expect(screen.getByRole('link', { name: /settings/i })).toHaveAttribute('href', '/settings')
    expect(screen.getByRole('button', { name: /coach/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /sign out/i })).toBeInTheDocument()
  })

  it('renders the hamburger button (visible on mobile; rendered at all widths)', () => {
    renderShell()
    expect(screen.getByRole('button', { name: /open menu/i })).toBeInTheDocument()
  })
})
```

Note: `vi.mock` calls are hoisted by Vitest before imports, so the `vi.mock` block must come before the `import { AppShell }` line. The block above has them in the correct order.

- [ ] **Step 2: Run test, expect failure**

```bash
npx vitest run src/components/AppShell.test.tsx
```

Expected: FAIL.

- [ ] **Step 3: Implement AppShell**

```tsx
import { ReactNode, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { IconButton } from './ui/IconButton'
import { ActionSheet, ActionSheetItem } from './ui/ActionSheet'
import { Settings, Coach, Hamburger, Close as _Close } from './ui/icons'
// _Close unused here but ensures barrel import compiles even if Drawer file not yet referenced

void _Close

export interface AppShellProps {
  children: ReactNode
}

export function AppShell({ children }: AppShellProps) {
  const { signOut, user } = useAuth()
  const [, setParams] = useSearchParams()
  const navigate = useNavigate()
  const [menuOpen, setMenuOpen] = useState(false)

  function openCoach() {
    setParams((prev) => {
      const next = new URLSearchParams(prev)
      next.set('coach', '1')
      return next
    })
  }

  return (
    <div className="min-h-screen bg-bg text-text">
      <header className="sticky top-0 z-30 bg-surface border-b border-border">
        <div className="max-w-3xl mx-auto px-4 h-14 flex items-center justify-between gap-2">
          <Link to="/" className="font-bold text-text text-sm tracking-tight">Job Agent</Link>

          <nav className="flex items-center gap-1">
            {user && (
              <>
                {/* Desktop: inline icons. Mobile: hidden — collapsed into hamburger sheet. */}
                <div className="hidden md:flex items-center gap-1">
                  <Link
                    to="/settings"
                    aria-label="Settings"
                    className="inline-flex items-center justify-center w-11 h-11 rounded-md-token text-muted hover:bg-surface-2 hover:text-text"
                  >
                    <Settings className="w-5 h-5" />
                  </Link>
                  <IconButton aria-label="Coach" onClick={openCoach}>
                    <Coach className="w-5 h-5" />
                  </IconButton>
                  <button
                    type="button"
                    onClick={() => signOut()}
                    className="px-3 py-1.5 text-sm text-muted hover:text-text"
                  >
                    Sign out
                  </button>
                </div>

                {/* Mobile hamburger trigger — also rendered at all widths so tests find it */}
                <IconButton
                  aria-label="Open menu"
                  className="md:hidden"
                  onClick={() => setMenuOpen(true)}
                >
                  <Hamburger className="w-5 h-5" />
                </IconButton>
              </>
            )}
          </nav>
        </div>
      </header>

      <main className="max-w-3xl mx-auto px-4 py-6">{children}</main>

      <ActionSheet
        open={menuOpen}
        onClose={() => setMenuOpen(false)}
        title="Menu"
        heading="Menu"
      >
        <ActionSheetItem
          onClick={() => { setMenuOpen(false); navigate('/settings') }}
        >
          Settings
        </ActionSheetItem>
        <ActionSheetItem
          onClick={() => { setMenuOpen(false); openCoach() }}
        >
          Coach
        </ActionSheetItem>
        <ActionSheetItem intent="danger" onClick={() => { setMenuOpen(false); signOut() }}>
          Sign out
        </ActionSheetItem>
      </ActionSheet>
    </div>
  )
}
```

Note: `useAuth()` already exposes `signOut` (verified in `frontend/src/context/AuthContext.tsx`). The existing `signOut` clears `sessionStorage`, resets context state, AND navigates to `/` via `window.location.href`. So calling it from the AppShell button handler is a single call — no extra `navigate(...)` needed. The `useNavigate` hook can stay imported for `setMenuOpen → navigate('/settings')` in the hamburger sheet but is not used by sign-out.

- [ ] **Step 4: Run test, expect pass**

```bash
npx vitest run src/components/AppShell.test.tsx
```

Expected: 4 tests pass. The "Sign out" button selector uses a regex so it matches whether the button text is "Sign out", "SIGN OUT", etc.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/AppShell.tsx frontend/src/components/AppShell.test.tsx
git commit -m "feat(frontend): AppShell component (header + mobile hamburger sheet)

Sticky header. Desktop: Settings link + Coach button + Sign out inline.
Mobile: those collapse into an ActionSheet behind a hamburger.
Coach button writes ?coach=1 (Plan B will read it). Sign out re-uses
the existing AuthContext.signOut() which clears the token and
navigates to /."
```

---

## Task 20: Wire AppShell into App.tsx

**Files:**
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Replace App.tsx contents**

```tsx
import { Routes, Route } from 'react-router-dom'
import { AuthProvider } from './context/AuthContext'
import { ToastProvider } from './components/ui/Toast'
import { AppShell } from './components/AppShell'
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
          <Route path="/" element={<Landing />} />
          <Route path="/auth/callback" element={<AuthCallback />} />
          <Route path="/matches" element={<RequireAuth><Matches /></RequireAuth>} />
          <Route path="/matches/:id" element={<RequireAuth><ApplicationReview /></RequireAuth>} />
          <Route path="/applied" element={<RequireAuth><Applied /></RequireAuth>} />
          <Route path="/profile" element={<RequireAuth><Onboarding /></RequireAuth>} />
          <Route path="/login" element={<Landing />} />
        </Routes>
      </AppShell>
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

Notes:
- Routes are unchanged from current behavior (Plan B will rename `/matches` → `/`, fold `/applied`, rename `/profile` → `/settings`). The shell wrap is the only structural change in this plan.
- `/login` is added as an alias of `/` for the Landing page since `AppShell.signOut()` navigates there. Until Plan B introduces a separate auth-gated root, both `/` and `/login` show Landing for unauth users.

- [ ] **Step 2: Run full test suite**

```bash
npm run test
```

Expected: all tests pass — both the new ui/ + AppShell tests AND the existing Matches / Onboarding / Landing / etc. tests. Pass count must be strictly greater than the Task 0 baseline.

- [ ] **Step 3: Type check**

```bash
npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 4: Boot dev server, eyeball each route**

```bash
npm run dev
```

Visit each route in a browser and confirm:
- `http://localhost:5173/` → Landing (no shell visible since user is unauth, but page renders)
- After dev login → `http://localhost:5173/matches` shows the new sticky header with Settings / Coach / Sign out icons on desktop, hamburger on mobile (resize the browser to test).
- Existing match cards still render (legacy styling — they use the old MatchCard, untouched in this plan).
- Hamburger sheet opens on mobile width and shows Settings / Coach / Sign out items.
- Tap Coach → URL gains `?coach=1` (Plan B will render the actual drawer; for now the URL change is the contract).

Stop dev server with `Ctrl-C`.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "feat(frontend): mount AppShell + ToastProvider in App.tsx

AppShell wraps all routes; ToastProvider becomes globally available.
Existing pages render unchanged inside the new shell — per-page
migration in Plan B."
```

---

## Task 21: Final verification — full test + lint + build

**Files:** none

- [ ] **Step 1: Full test suite**

```bash
cd frontend
npm run test
```

Expected: all tests pass. Note the new pass count and confirm it's the Task 0 baseline + the new ui/ component test counts (Button: 11, IconButton: 4, Chip: 5, Badge: 5, Card: 5, ActionSheet: 5, Drawer: 5, Toast: 5, EmptyState: 4, TextField: 4, TextArea: 4, Skeleton: 3, SwipeableCard: 4, AppShell: 4, cn: 4).

- [ ] **Step 2: Type check**

```bash
npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 3: Production build**

```bash
npm run build
```

Expected: build succeeds; `app/static/` populated.

- [ ] **Step 4: Run e2e tests**

```bash
npm run test:e2e
```

Expected: all e2e specs pass. The shell change should not break any existing e2e selectors (existing tests target page content, not the nav). If a spec fails because it relied on `<NavLink>` text "Matches / History / Profile", update the selector to match the new icons-only nav (use `aria-label` on the icon link/button: e.g., `getByRole('link', { name: 'Settings' })`).

- [ ] **Step 5: Push branch and open PR**

```bash
git push -u origin feat/ui-foundation
gh pr create --title "feat(frontend): UX redesign Plan A — design tokens, primitives, AppShell" \
  --body "$(cat <<'EOF'
## Summary
Foundation for the frontend UX redesign per spec at
\`docs/superpowers/specs/2026-05-06-frontend-ux-redesign-design.md\`.

- New design tokens (colors, radii, motion) in \`src/styles/tokens.css\`,
  exposed via Tailwind extension as \`bg-surface\`, \`text-muted\`,
  \`rounded-lg-token\`, etc.
- Primitive component library in \`src/components/ui/\`: Button,
  IconButton, Chip, Badge, Card, ActionSheet, Drawer, Toast, EmptyState,
  TextField, TextArea, Skeleton, SwipeableCard. Each colocated test.
- Inline-SVG icon set in \`src/components/ui/icons/\`. No icon-library
  dependency.
- New \`AppShell\` component (header + mobile hamburger sheet) wraps the
  routed content. Existing pages render unchanged inside it.

Plan B (separate PR) will migrate per-page surfaces and add analytics.

## Test plan
- [ ] \`npm run test\` — all unit tests pass, count strictly greater than baseline
- [ ] \`npm run test:e2e\` — Playwright suite passes
- [ ] \`npm run build\` — production build succeeds
- [ ] Manual: open / on desktop and mobile widths; confirm header icons + hamburger work; confirm Coach button writes \`?coach=1\` to the URL
- [ ] Manual: existing pages (Matches, ApplicationReview, Onboarding, Applied, Landing) still render and function normally

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Expected: PR created, returns URL.

- [ ] **Step 6: Confirm CI passes on the PR**

Visit the PR URL. Wait for the `ci.yml` workflow to complete. Expected: green check on every job.

If CI fails, fix the failure on this branch (NEW commits, never amend), push, and wait again.

---

## Self-Review Checklist (run before claiming done)

- [ ] Every primitive in spec Section 2 is covered by a task: Button (5), IconButton (6), Chip (7), Badge (8), Card (9), ActionSheet (10), Drawer (11), Toast (12), EmptyState (13), TextField (14), TextArea (15), Skeleton (16), SwipeableCard (17). ✓
- [ ] Design tokens in spec Section 1 are codified in `tokens.css` and Tailwind config: colors ✓, typography (font-family + reliance on Tailwind sizes) ✓, radii (sm-token, md-token, lg-token, pill) ✓, motion (--t-fast, --t-slow, prefers-reduced-motion override) ✓, touch targets (44px on Button md/lg, IconButton, Chip min-h-32, TextField min-h-44) ✓.
- [ ] Spec's "no headless-UI / Radix dependency" honored — every primitive is hand-rolled. ✓
- [ ] Spec's "no icon library" honored — inline SVGs only. ✓
- [ ] Spec's `as="a"` / real-anchor preservation on Card honored (Task 9, with `as="a"` and `as="rrlink"` discriminated unions). ✓
- [ ] Spec's focus-trap requirement on ActionSheet/Drawer is partially honored — initial focus is moved into the panel; full Tab cycling within the panel is **not** implemented in this plan (deferred to Plan B if user testing flags it). Acceptable: jsdom doesn't reliably exercise tab order so a real-browser pass is the right verification.
- [ ] Tests are written BEFORE implementation in every component task. ✓
- [ ] Every test step shows actual test code, every implementation step shows actual implementation code. ✓
- [ ] No `// TODO`, `// TBD`, "implement appropriately" language anywhere in plan. ✓
- [ ] `cn()` is consistent — same name everywhere it's used. ✓
- [ ] AppShell test names match the rendered text/aria-label exactly (regex used where copy might shift). ✓
- [ ] All commits use conventional prefixes (`feat(frontend/ui):`, `feat(frontend):`, `test(frontend):`). ✓
- [ ] Branching strategy is explicit (Task 0). ✓
- [ ] PR description references the spec by path. ✓
- [ ] Plan B's prerequisites (`?coach=1` toggling, ToastProvider mounted, all primitives available) are met by the end of this plan. ✓

## Out of scope for Plan A (carried forward to Plan B)

- Migrating `MatchCard`, `Matches`, `ApplicationReview`, `Onboarding`, `Applied`, `Landing`, `BudgetBanner`, `SyncStatusChip`, `InvalidSlugsNotice` to the new design.
- Renaming `/matches` → `/` and folding `/applied`.
- Replacing `/profile` with the new structured `/settings` page.
- Coach drawer rendering itself (Plan A only adds the URL toggle).
- Profile-completeness card, status-chips row on the feed, sync-button live state, pull-to-refresh.
- Match detail full-width description, sticky bottom action bar, optimistic mark-applied.
- Cover-letter editor restyle.
- SSE meta marker for agent profile mutations + inline `Search now` CTA.
- `events` table, Alembic migration, `POST /api/events`, `track.ts` wrapper, instrumentation.
- `scripts/analytics_views.sql`.
- Cleanup deletes (`Applied.tsx`, `Onboarding.tsx`, old `MatchCard.tsx`, redirects).
- Visual-regression coverage (deliberately not introduced — covered by manual review).

End of Plan A.
