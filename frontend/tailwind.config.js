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
