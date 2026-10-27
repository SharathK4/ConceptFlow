/** 03 - Earlier lessons: everything the studio has made, newest first. */

import { ago, clock } from '../lib/format'
import type { LessonSummary } from '../lib/types'
import { Card } from './Card'
import './library.css'

interface Props {
  lessons: LessonSummary[]
  current: string | null
  onOpen: (id: string) => void
  onDelete: (id: string) => void
}

export function Library({ lessons, current, onOpen, onDelete }: Props) {
  if (!lessons.length) return null
  return (
    <Card index={3} className="library" delay={200} title="Earlier lessons" subtitle="Every lesson keeps its film, captions, code and the full record of how it was made.">
      <ol className="shelf">
        {lessons.map((lesson) => (
          <li key={lesson.id} className={`shelf__item${lesson.id === current ? ' is-current' : ''}`}>
            <button type="button" className="shelf__open" onClick={() => onOpen(lesson.id)}>
              <span className="shelf__poster">
                {lesson.poster ? <img src={lesson.poster} alt="" loading="lazy" /> : <span className={`shelf__state is-${lesson.status}`}>{label(lesson)}</span>}
                {lesson.duration ? <span className="shelf__dur num">{clock(lesson.duration)}</span> : null}
              </span>
              <span className="shelf__title">{lesson.title || lesson.prompt || 'Untitled lesson'}</span>
              <span className="shelf__meta">
                {ago(lesson.created)}
                {lesson.mode === 'replay' ? ' · replay' : ''}
                {lesson.language !== 'en' ? ` · ${lesson.language.toUpperCase()}` : ''}
              </span>
            </button>
            {lesson.status === 'done' || lesson.status === 'failed' ? (
              <button
                type="button"
                className="shelf__delete"
                title="Delete this lesson"
                onClick={() => {
                  if (window.confirm('Delete this lesson and all its files?')) onDelete(lesson.id)
                }}
              >
                Delete
              </button>
            ) : null}
          </li>
        ))}
      </ol>
    </Card>
  )
}

function label(lesson: LessonSummary): string {
  if (lesson.status === 'running') return 'In the studio'
  if (lesson.status === 'queued') return 'Queued'
  if (lesson.status === 'failed') return 'Stopped'
  return ''
}
