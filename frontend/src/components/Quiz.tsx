/** The Lesson Planner's three questions, offered when the film ends. */

import { useState } from 'react'
import { index } from '../lib/format'
import type { QuizItem } from '../lib/types'

export function Quiz({ items, onReplay }: { items: QuizItem[]; onReplay: () => void }) {
  const [at, setAt] = useState(0)
  const [picked, setPicked] = useState<number | null>(null)
  const [score, setScore] = useState(0)
  const finished = at >= items.length
  const item = items[at]

  const choose = (i: number) => {
    if (picked !== null) return
    setPicked(i)
    if (i === item.answer) setScore((s) => s + 1)
  }

  const next = () => {
    setPicked(null)
    setAt((n) => n + 1)
  }

  return (
    <div className="quiz" role="dialog" aria-label="Check yourself">
      <div className="quiz__card">
        {finished ? (
          <>
            <p className="quiz__eyebrow">Check yourself</p>
            <h3 className="quiz__question">
              {score} of {items.length} - {score === items.length ? 'exactly right.' : 'worth another look.'}
            </h3>
            <div className="quiz__actions">
              <button type="button" className="quiz__btn" onClick={onReplay}>
                Watch again
              </button>
              <button
                type="button"
                className="quiz__btn quiz__btn--quiet"
                onClick={() => {
                  setAt(0)
                  setScore(0)
                }}
              >
                Retake
              </button>
            </div>
          </>
        ) : (
          <>
            <p className="quiz__eyebrow num">
              Check yourself · {index(at + 1)} / {index(items.length)}
            </p>
            <h3 className="quiz__question">{item.question}</h3>
            <ol className="quiz__choices">
              {item.choices.map((choice, i) => {
                const state = picked === null ? '' : i === item.answer ? ' is-right' : i === picked ? ' is-wrong' : ' is-muted'
                return (
                  <li key={i}>
                    <button type="button" className={`quiz__choice${state}`} onClick={() => choose(i)} disabled={picked !== null}>
                      <span className="quiz__letter">{String.fromCharCode(65 + i)}</span>
                      {choice}
                    </button>
                  </li>
                )
              })}
            </ol>
            {picked !== null ? (
              <div className="quiz__why">
                <p>{item.why}</p>
                <button type="button" className="quiz__btn" onClick={next}>
                  {at + 1 < items.length ? 'Next question' : 'See how you did'}
                </button>
              </div>
            ) : (
              <button type="button" className="quiz__skip" onClick={onReplay}>
                Skip - watch again
              </button>
            )}
          </>
        )}
      </div>
    </div>
  )
}
