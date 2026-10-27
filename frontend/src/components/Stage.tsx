/**
 * 02 - The lesson.
 *
 * One dark slab, the colour the lessons are rendered on, sitting in its own
 * light on the page. It holds whatever is true right now: a quiet drawing
 * before anything is asked, the studio at work while a lesson is made, and
 * the finished film - with its chapters beneath - once it is done.
 */

import { useCallback, useRef, useState } from 'react'
import { clock, index } from '../lib/format'
import type { LessonDetail, Manifest } from '../lib/types'
import type { Production as ProductionState } from '../lib/useLesson'
import { Card } from './Card'
import { IdleCanvas } from './IdleCanvas'
import { Player, type PlayerHandle } from './Player'
import { Production } from './Production'
import './stage.css'

const QUALITY: Record<string, string> = { preview: '480p', draft: '720p', hd: '1080p', hd60: '1080p60' }
const VOICE: Record<string, string> = { elevenlabs: 'ElevenLabs voice', sarvam: 'Sarvam voice', edge: 'Edge neural voice', silent: 'captions only' }

interface Props {
  lessonId: string | null
  production: ProductionState
  detail: LessonDetail | null
  onOpenLog: () => void
}

export function Stage({ lessonId, production, detail, onOpenLog }: Props) {
  const manifest: Manifest | null = production.manifest ?? detail?.manifest ?? null
  const status = lessonId ? (detail?.status === 'done' || production.status === 'done' ? 'done' : production.status) : 'idle'
  const failed = status === 'failed' || detail?.status === 'failed'
  const player = useRef<PlayerHandle>(null)
  const [time, setTime] = useState(0)
  const onTime = useCallback((t: number) => setTime(t), [])

  const title =
    status === 'done' && manifest
      ? manifest.title
      : failed
        ? 'Something went wrong'
        : status === 'idle'
          ? 'The lesson'
          : detail?.title && detail.title !== detail.prompt
            ? detail.title
            : 'In the studio'
  const subtitle =
    status === 'done' && manifest
      ? manifest.logline
      : failed
        ? 'The studio stopped before the lesson was finished. The production log shows where.'
        : status === 'idle'
          ? 'Your lesson will play here - narrated, captioned, in chapters, with a short quiz at the end.'
          : production.replayTitle
            ? `Replaying the recorded run of "${production.replayTitle}". Narration, rendering and review are happening live.`
            : 'Eight AI agents and four production tools, across two frameworks, are planning, animating, narrating and reviewing your lesson.'

  const usesReader = Boolean(detail?.request?.upload_name)

  return (
    <Card
      index={2}
      className="stage"
      delay={100}
      title={title}
      subtitle={subtitle}
      aside={
        status === 'done' && manifest ? (
          <p className="stage__facts num">
            {clock(manifest.duration)} · {manifest.chapters.length} chapters · {QUALITY[manifest.quality] ?? manifest.quality}
            {manifest.voice ? ` · ${VOICE[manifest.voice] ?? manifest.voice}` : ''}
            {manifest.language && manifest.language !== 'en' ? ` · ${manifest.language.toUpperCase()}` : ''}
          </p>
        ) : null
      }
      footer={lessonId ? { label: status === 'done' ? 'Open the production log - how the agents made this' : 'Open the production log', onClick: onOpenLog } : null}
    >
      <div className="tile">
        <div className="tile__glow" aria-hidden="true" />
        <div className="tile__body">
          <svg className="tile__texture" aria-hidden="true" preserveAspectRatio="xMidYMid slice">
            <filter id="stage-grain" x="0" y="0" width="100%" height="100%">
              <feTurbulence type="fractalNoise" baseFrequency="0.012 0.02" numOctaves="4" seed="7" />
              <feColorMatrix type="matrix" values="0.1 0 0 0 0.02  0.12 0 0 0 0.02  0.2 0 0 0 0.04  0 0 0 0 1" />
            </filter>
            <rect width="100%" height="100%" filter="url(#stage-grain)" />
          </svg>
          <div className="tile__sheen" aria-hidden="true" />

          {status === 'idle' ? (
            <div className="idle">
              <IdleCanvas />
              <div className="idle__copy">
                <p className="idle__eyebrow">ConceptFlow studio</p>
                <p className="idle__line">Any idea, animated.</p>
                <p className="idle__hint">Pose a concept above. The film appears here, made while you watch.</p>
              </div>
            </div>
          ) : null}

          {status !== 'idle' && status !== 'done' && !failed ? <Production production={production} usesReader={usesReader} /> : null}

          {failed ? (
            <div className="stage__failed">
              <p className="stage__failed-eyebrow">The studio stopped</p>
              <p className="stage__failed-line">{production.error ?? detail?.error ?? 'Unknown error'}</p>
              <p className="stage__failed-hint">
                If a model ran out of free quota, the ladders in .env decide what is tried next; quotas reset at midnight Pacific.
              </p>
            </div>
          ) : null}

          {status === 'done' && manifest ? <Player ref={player} manifest={manifest} onTime={onTime} /> : null}
        </div>
      </div>

      {status === 'done' && manifest ? (
        <div className="chapters">
          <ol className="chapters__list">
            {manifest.chapters.map((c, i) => {
              const active = time >= c.start && time < c.end
              return (
                <li key={c.scene}>
                  <button type="button" className={`chapter${active ? ' is-active' : ''}`} onClick={() => player.current?.seek(c.start, true)}>
                    {c.thumb ? <img src={c.thumb} alt="" loading="lazy" /> : <span className="chapter__blank" />}
                    <span className="chapter__meta num">
                      {index(i + 1)} · {clock(c.start)}
                    </span>
                    <span className="chapter__title">{c.title}</span>
                  </button>
                </li>
              )
            })}
          </ol>
          <div className="chapters__links">
            <a href={manifest.video} download>
              Download the film
            </a>
            <a href={manifest.captions} download>
              Captions (.vtt)
            </a>
            {manifest.fallback_scenes.length ? (
              <span className="chapters__note">
                {manifest.fallback_scenes.length} scene(s) used the storyboard fallback after {manifest.rounds} rounds.
              </span>
            ) : (
              <span className="chapters__note num">
                Approved after {manifest.rounds} render round{manifest.rounds === 1 ? '' : 's'}.
              </span>
            )}
          </div>
        </div>
      ) : null}
    </Card>
  )
}
