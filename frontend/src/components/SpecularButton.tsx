/**
 * Specular button (from Keystone): a highlight tracks the pointer across the
 * face of the button, the way light moves on a curved surface. Written to CSS
 * custom properties on pointermove, so it never re-renders anything.
 */

import { useCallback, useRef, type ButtonHTMLAttributes, type PointerEvent, type ReactNode } from 'react'
import './specular-button.css'

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  children: ReactNode
  tone?: 'product' | 'quiet'
}

export function SpecularButton({ children, tone = 'product', className = '', ...rest }: Props) {
  const ref = useRef<HTMLButtonElement>(null)

  const track = useCallback((event: PointerEvent<HTMLButtonElement>) => {
    const node = ref.current
    if (!node) return
    const box = node.getBoundingClientRect()
    node.style.setProperty('--px', `${((event.clientX - box.left) / box.width) * 100}%`)
    node.style.setProperty('--py', `${((event.clientY - box.top) / box.height) * 100}%`)
  }, [])

  const reset = useCallback(() => {
    ref.current?.style.setProperty('--px', '50%')
    ref.current?.style.setProperty('--py', '120%')
  }, [])

  return (
    <button {...rest} ref={ref} onPointerMove={track} onPointerLeave={reset} className={`specular specular--${tone} ${className}`.trim()}>
      <span className="specular__sheen" aria-hidden="true" />
      <span className="specular__label">{children}</span>
    </button>
  )
}
