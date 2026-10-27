/**
 * A numbered panel, as in Keystone: an index, a display-serif title, one line
 * of plain English, then whatever the panel is for. The footer, when present,
 * is a quiet uppercase line with a rule that grows an arrowhead on hover.
 */

import type { ReactNode } from 'react'
import { index as pad } from '../lib/format'
import './card.css'

interface Props {
  index: number
  title: ReactNode
  subtitle?: ReactNode
  aside?: ReactNode
  children: ReactNode
  footer?: { label: string; onClick: () => void } | null
  className?: string
  delay?: number
}

export function Card({ index, title, subtitle, aside, children, footer, className = '', delay = 0 }: Props) {
  return (
    <section className={`card ${className}`.trim()} style={{ ['--card-delay' as string]: `${140 + delay}ms` }}>
      <header className="card__head">
        <div>
          <span className="card__index num">{pad(index)}</span>
          <h2 className="card__title">{title}</h2>
          {subtitle ? <p className="card__subtitle">{subtitle}</p> : null}
        </div>
        {aside ? <div className="card__aside">{aside}</div> : null}
      </header>
      <div className="card__body">{children}</div>
      {footer ? (
        <button type="button" className="card__foot" onClick={footer.onClick}>
          <span className="card__reveals">{footer.label}</span>
          <span className="card__open" aria-hidden="true">
            <i />
          </span>
        </button>
      ) : null}
    </section>
  )
}
