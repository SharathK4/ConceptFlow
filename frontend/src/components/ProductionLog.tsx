/**
 * The production log: how the agents made this lesson.
 *
 * Opening it does not navigate. The page recedes and the record comes forward
 * on top of it (Keystone's expanded-analysis pattern); closing returns the
 * reader exactly where they were. Eight views of one run: who said what to
 * whom, the storyboard with the frame that realised each beat, the review
 * panel's verdicts and formula checks, the Manim code, the plan it all came
 * from - and how it was made: performance and cost, the trace, provenance.
 */

import { useEffect, useMemo, useState } from 'react'
import { api } from '../lib/api'
import { agentLabel, clock, index } from '../lib/format'
import type { LessonDetail, MathCheckRow, ReviewRecord } from '../lib/types'
import type { Production } from '../lib/useLesson'
import { PerformanceView, ProvenanceView, TraceView } from './RunViews'
import './production-log.css'

type Tab = 'conversation' | 'storyboard' | 'review' | 'code' | 'plan' | 'performance' | 'trace' | 'provenance'

const TABS: { id: Tab; label: string }[] = [
  { id: 'conversation', label: 'The conversation' },
  { id: 'storyboard', label: 'The storyboard' },
  { id: 'review', label: 'The review' },
  { id: 'code', label: 'The code' },
  { id: 'plan', label: 'The plan' },
  { id: 'performance', label: 'Performance' },
  { id: 'trace', label: 'Trace' },
  { id: 'provenance', label: 'Provenance' },
]

const DIMENSIONS: [string, string][] = [
  ['math_correctness', 'Mathematics'],
  ['pedagogy', 'Pedagogy'],
  ['pacing', 'Pacing'],
  ['visual_clarity', 'Clarity'],
  ['aesthetics', 'Aesthetics'],
  ['fidelity', 'Fidelity'],
]

export function ProductionLog({ production, detail, onClose }: { production: Production; detail: LessonDetail | null; onClose: () => void }) {
  const [tab, setTab] = useState<Tab>('conversation')

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.style.overflow = ''
    }
  }, [onClose])

  const messages = production.feed.filter((f) => f.kind === 'message').length
  const toolCalls = Object.values(production.tools).reduce((a, b) => a + b, 0)
  const models = Object.entries(production.models)
  const running = production.status === 'running' || production.status === 'queued'
  const reviews: ReviewRecord[] = (detail?.artifacts?.reviews as ReviewRecord[] | null | undefined) ?? production.reviews

  return (
    <div className="log" role="dialog" aria-modal="true" aria-label="Production log">
      <div className="log__scrim" onClick={onClose} />
      <article className="log__panel">
        <button type="button" className="log__close" onClick={onClose} aria-label="Close">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="1.6" fill="none" />
          </svg>
        </button>
        <header className="log__head">
          <p className="log__eyebrow">Production log</p>
          <h2 className="log__title">{detail?.manifest?.title ?? detail?.title ?? 'This lesson'}</h2>
          <p className="log__facts num">
            {messages} hand-offs between agents · {toolCalls ? `${toolCalls} MCP tool calls · ` : ''}
            {Math.max(production.round, detail?.manifest?.rounds ?? 0)} render round(s) · {clock(production.lastT)} in the studio
            {detail?.mode === 'replay' ? ' · replayed model replies' : ''}
          </p>
          {detail?.has_report ? (
            <p className="log__downloads">
              <span>The structured report</span>
              <a href={api.reportUrl(detail.id, 'md')} download>
                Markdown
              </a>
              <a href={api.reportUrl(detail.id, 'json')} target="_blank" rel="noreferrer">
                JSON
              </a>
            </p>
          ) : null}
          {models.length ? (
            <p className="log__models">
              {models.map(([role, model]) => (
                <span key={role}>
                  <b>{role.replace('_', ' ')}</b> {model}
                </span>
              ))}
            </p>
          ) : null}
          <nav className="log__tabs" aria-label="Views">
            {TABS.map((t) => (
              <button key={t.id} type="button" className={`log__tab${tab === t.id ? ' is-active' : ''}`} onClick={() => setTab(t.id)}>
                {t.label}
              </button>
            ))}
          </nav>
        </header>

        <div className="log__body">
          {tab === 'conversation' ? <Conversation production={production} /> : null}
          {tab === 'storyboard' ? <StoryboardView production={production} detail={detail} /> : null}
          {tab === 'review' ? <ReviewView reviews={reviews} sheets={production.sheets} checks={production.mathChecks?.checks ?? []} /> : null}
          {tab === 'code' ? <CodeView code={detail?.code ?? null} /> : null}
          {tab === 'plan' ? <PlanView detail={detail} /> : null}
          {tab === 'performance' && detail ? <PerformanceView id={detail.id} running={running} /> : null}
          {tab === 'trace' && detail ? <TraceView id={detail.id} /> : null}
          {tab === 'provenance' && detail ? <ProvenanceView id={detail.id} /> : null}
        </div>
      </article>
    </div>
  )
}

function Conversation({ production }: { production: Production }) {
  if (!production.feed.length) return <p className="log__empty">Nothing recorded yet.</p>
  return (
    <ol className="convo">
      {production.feed.map((item) => (
        <li key={item.seq} className={`convo__row convo__row--${item.kind}${item.tone ? ` is-${item.tone}` : ''}`}>
          <span className="convo__t num">{clock(item.t)}</span>
          <div>
            <p className="convo__who">
              {agentLabel(item.from ?? '')}
              {item.to ? (
                <>
                  <span aria-hidden="true"> → </span>
                  {agentLabel(item.to)}
                </>
              ) : null}
            </p>
            <p className="convo__text">{item.text}</p>
          </div>
        </li>
      ))}
    </ol>
  )
}

function StoryboardView({ production, detail }: { production: Production; detail: LessonDetail | null }) {
  const board = detail?.artifacts?.storyboard
  const lines = detail?.artifacts?.narration?.lines ?? {}
  const frames = useMemo(() => Object.fromEntries(production.frames.map((f) => [f.beat, f.url])), [production.frames])
  if (!board) return <p className="log__empty">The storyboard appears once the writers' room has finished.</p>
  return (
    <div className="board">
      <p className="board__story">{board.color_story}</p>
      {board.scenes.map((scene, i) => (
        <section key={scene.id} className="board__scene">
          <h3 className="board__title">
            <span className="num">{index(i + 1)}</span> {scene.title}
          </h3>
          <p className="board__purpose">{scene.purpose}</p>
          <ol className="board__beats">
            {scene.beats.map((beat) => (
              <li key={beat.id} className="beat">
                {frames[beat.id] ? <img src={frames[beat.id]} alt={`Frame for ${beat.id}`} loading="lazy" /> : <span className="beat__blank" />}
                <div>
                  <p className="beat__id num">{beat.id}</p>
                  <p className="beat__said">“{lines[beat.id] ?? beat.narration}”</p>
                  <p className="beat__shown">{beat.visual}</p>
                </div>
              </li>
            ))}
          </ol>
        </section>
      ))}
    </div>
  )
}

const CHECK_LABEL: Record<MathCheckRow['status'], string> = {
  holds: 'Identity holds',
  differs: 'Sides differ',
  parsed: 'Parsed',
  unchecked: 'Not checkable',
}

function ReviewView({ reviews, sheets, checks }: { reviews: ReviewRecord[]; sheets: string[]; checks: MathCheckRow[] }) {
  if (!reviews.length) return <p className="log__empty">The review panel has not met yet.</p>
  return (
    <div className="review">
      {checks.length ? (
        <section className="checks">
          <h3 className="perf__title">Formula checks · SymPy over MCP</h3>
          <p className="perf__note">
            Every equation the lesson shows was checked symbolically before the Maths Critic read it. Sides that differ are expected for definitions and
            equations to solve; for an identity they would be an error.
          </p>
          <ul className="checks__list">
            {checks.map((c) => (
              <li key={c.latex} className={`check is-${c.status}`}>
                <span className="check__status">{CHECK_LABEL[c.status]}</span>
                <code className="check__latex">{c.latex}</code>
                {c.status !== 'holds' && c.detail ? <span className="check__detail">{c.detail}</span> : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      {reviews.map((r) => (
        <section key={r.round} className="review__round">
          <header className="review__head">
            <h3>Round {r.round}</h3>
            <span className={`review__verdict is-${r.verdict}`}>{r.verdict === 'approve' ? (r.forced ? 'Accepted' : 'Approved') : 'Sent back'}</span>
            <span className="review__mean num">{r.mean.toFixed(1)} / 10</span>
          </header>
          <div className="scores">
            {DIMENSIONS.map(([key, label]) => (
              <div key={key} className="score">
                <span className="score__label">{label}</span>
                <span className="score__bar">
                  <span style={{ width: `${(r.scores[key] ?? 0) * 10}%` }} />
                </span>
                <span className="score__value num">{r.scores[key] ?? '-'}</span>
              </div>
            ))}
          </div>
          <p className="review__summary">{r.pedagogy_summary}</p>
          <p className="review__summary">{r.visual_summary}</p>
          {r.issues.length ? (
            <ul className="issues">
              {r.issues.map((issue, i) => (
                <li key={i} className={`issue is-${issue.severity}`}>
                  <span className="issue__tag">
                    {issue.severity} · {issue.scene}
                    {issue.beat ? `/${issue.beat}` : ''} · {issue.source}
                  </span>
                  <p>{issue.problem}</p>
                  <p className="issue__fix">{issue.fix}</p>
                </li>
              ))}
            </ul>
          ) : null}
        </section>
      ))}
      {sheets.length ? (
        <figure className="review__sheet">
          <figcaption>What the visual critic saw: one keyframe per beat.</figcaption>
          {sheets.map((s) => (
            <img key={s} src={s} alt="Contact sheet of keyframes" loading="lazy" />
          ))}
        </figure>
      ) : null}
    </div>
  )
}

const PY_KEYWORDS = /\b(from|import|class|def|with|as|for|in|if|else|elif|return|lambda|and|or|not|None|True|False|self)\b/g

function highlight(line: string): string {
  const escaped = line.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  const comment = escaped.indexOf('#')
  const [code, rest] = comment >= 0 && !/["']/.test(escaped.slice(0, comment)) ? [escaped.slice(0, comment), escaped.slice(comment)] : [escaped, '']
  const withStrings = code.replace(/(r?"[^"]*"|r?'[^']*')/g, '\u0001$1\u0002')
  const parts = withStrings.split(/(\u0001[^\u0002]*\u0002)/)
  const html = parts
    .map((part) =>
      part.startsWith('\u0001')
        ? `<span class="tk-s">${part.slice(1, -1)}</span>`
        : part.replace(PY_KEYWORDS, '<span class="tk-k">$1</span>').replace(/\b(\d+(?:\.\d+)?)\b/g, '<span class="tk-n">$1</span>'),
    )
    .join('')
  return html + (rest ? `<span class="tk-c">${rest}</span>` : '')
}

function CodeView({ code }: { code: string | null }) {
  if (!code) return <p className="log__empty">The final module appears when the lesson is finished.</p>
  return (
    <pre className="code">
      {code.split('\n').map((line, i) => (
        <span key={i} className="code__line">
          <span className="code__n num">{i + 1}</span>
          <span dangerouslySetInnerHTML={{ __html: highlight(line) || ' ' }} />
        </span>
      ))}
    </pre>
  )
}

function PlanView({ detail }: { detail: LessonDetail | null }) {
  const plan = detail?.artifacts?.plan
  const analysis = detail?.artifacts?.analysis as Record<string, unknown> | null | undefined
  if (!plan) return <p className="log__empty">The plan appears once the writers' room has finished.</p>
  return (
    <div className="plan">
      {analysis ? (
        <section className="plan__block">
          <p className="plan__label">Concept Analyst · core insight</p>
          <p className="plan__insight">{String(analysis.core_insight ?? '')}</p>
          {Array.isArray(analysis.misconceptions) && analysis.misconceptions.length ? (
            <>
              <p className="plan__label">Misconceptions to address</p>
              <ul className="plan__list">
                {(analysis.misconceptions as string[]).map((m) => (
                  <li key={m}>{m}</li>
                ))}
              </ul>
            </>
          ) : null}
        </section>
      ) : null}
      <section className="plan__block">
        <p className="plan__label">Lesson Planner · hook</p>
        <p className="plan__insight">{plan.hook}</p>
        <p className="plan__label">Objectives</p>
        <ul className="plan__list">
          {plan.objectives.map((o) => (
            <li key={o}>{o}</li>
          ))}
        </ul>
        <p className="plan__label">Sections</p>
        <ol className="plan__sections">
          {plan.sections.map((s, i) => (
            <li key={s.title}>
              <b>
                {index(i + 1)} {s.title}
              </b>
              <span>{s.explanation}</span>
            </li>
          ))}
        </ol>
        <p className="plan__label">Takeaway</p>
        <p className="plan__insight">{plan.takeaway}</p>
      </section>
    </div>
  )
}
