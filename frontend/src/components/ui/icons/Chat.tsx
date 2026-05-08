import { SVGAttributes } from 'react'
export function Chat(props: SVGAttributes<SVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" {...props}>
      <path d="M12 2l1.8 5.4L19 9l-5.2 1.6L12 16l-1.8-5.4L5 9l5.2-1.6L12 2zM19 16l.7 2.1 2.3.7-2.3.7L19 22l-.7-2.1L16 19.2l2.3-.7L19 16zM5 16l.5 1.5L7 18l-1.5.5L5 20l-.5-1.5L3 18l1.5-.5L5 16z" />
    </svg>
  )
}
