/**
 * The stage while a lesson is being made: the agents as a live diagram, what
 * they are saying to each other, and the frames as they come off the
 * renderer. Everything here is driven by the event stream - nothing is
 * simulated on the page.
 */

import { useEffect, useRef } from 'react'
import { agentLabel, clock } from '../lib/format'
import { useElapsed, type Production as ProductionState } from '../lib/useLesson'
import { AgentGraph } from './AgentGraph'
import './production.css'

function eyebrow(p: ProductionState): string {
  if (p.status === 'queued') {
    const ahead = p.queuePosition ?? 0
    return ahead > 0 ? `Queued · ${ahead} lesson${ahead === 1 ? '' : 's'} ahead` : 'Queued'
  }
  if (p.status === 'connecting') return 'Connecting'
  return p.round ? `Round ${p.round}` : 'Planning'
}

function headline(p: ProductionState): string {
  if (p.status === 'queued') return 'Waiting for the studio to finish the lesson before yours'
  if (p.status === 'connecting') return 'Waiting for the studio'
  if (p.status === 'failed') return 'The studio stopped'
  const active = Object.entries(p.agents)
    .filter(([, s]) => s === 'active')
    .map(([id]) => agentLabel(id))
  if (!active.length) return 'Passing work along'
  if (active.length === 1) return `${active[0]} at work`
  return `${active.slice(0, -1).join(', ')} and ${active[active.length - 1]} at work`
}

export function Production({ production, usesReader }: { production: ProductionState; usesReader: boolean }) {
  const elapsed = useElapsed(production)
  const feedRef = useRef<HTMLOListElement>(null)
  const stripRef = useRef<HTMLDivElement>(null)
  const feed = production.feed.slice(-40)

  useEffect(() => {
    feedRef.current?.scrollTo({ top: feedRef.current.scrollHeight, behavior: 'smooth' })
  }, [production.feed.length])

  useEffect(() => {
    stripRef.current?.scrollTo({ left: stripRef.current.scrollWidth, behavior: 'smooth' })
  }, [production.frames.length])

  const scenes = Object.entries(production.scenes).sort(([a], [b]) => a.localeCompare(b, undefined, { numeric: true }))

  return (
    <div className="prod">
      <header className="prod__top">
        <div>
          <p className="prod__eyebrow num">
            {eyebrow(production)} · {clock(elapsed)}
          </p>
          <h3 className="prod__headline">{headline(production)}</h3>
        </div>
        <div className="prod__scenes">
          {production.progress && production.progress.done < production.progress.total ? (
            <span className="prod__progress num">{production.progress.text}</span>
          ) : null}
          {scenes.map(([id, s]) => (
            <span key={id} className={`prod__scene is-${s.status}`} title={s.error ?? s.status}>
              {id.toUpperCase()}
            </span>
          ))}
        </div>
      </header>

      <div className="prod__main">
        <div className="prod__graph">
          <AgentGraph production={production} usesReader={usesReader} />
        </div>
        <ol className="prod__feed" ref={feedRef} aria-live="polite">
          {feed.length === 0 ? <li className="prod__empty">The agents are getting started…</li> : null}
          {feed.map((item) => (
            <li key={item.seq} className={`feed feed--${item.kind}${item.tone ? ` is-${item.tone}` : ''}`}>
              {item.kind === 'message' ? (
                <p className="feed__who">
                  {agentLabel(item.from ?? '')} <span aria-hidden="true">→</span> {agentLabel(item.to ?? '')}
                </p>
              ) : (
                <p className="feed__who">{agentLabel(item.from ?? '')}</p>
              )}
              <p className="feed__text">{item.text}</p>
            </li>
          ))}
        </ol>
      </div>

      <div className="prod__strip" ref={stripRef}>
        {production.frames.length === 0 ? (
          <p className="prod__strip-empty">Frames appear here as each scene renders.</p>
        ) : (
          production.frames.map((frame) => (
            <figure key={frame.beat} className="strip__frame">
              <img src={frame.url} alt={`Keyframe ${frame.beat}`} loading="lazy" />
              <figcaption className="num">{frame.beat}</figcaption>
            </figure>
          ))
        )}
      </div>
    </div>
  )
}
