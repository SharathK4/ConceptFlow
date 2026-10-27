/** A quiet segmented choice, in the manner of Keystone's scenario selector. */

import './segmented.css'

export interface Choice<T extends string> {
  value: T
  label: string
  disabled?: boolean
  title?: string
}

interface Props<T extends string> {
  label: string
  value: T
  choices: Choice<T>[]
  onChange: (value: T) => void
}

export function Segmented<T extends string>({ label, value, choices, onChange }: Props<T>) {
  return (
    <div className="seg" role="radiogroup" aria-label={label}>
      <span className="seg__label">{label}</span>
      <div className="seg__items">
        {choices.map((choice) => (
          <button
            key={choice.value}
            type="button"
            role="radio"
            aria-checked={choice.value === value}
            className={`seg__item${choice.value === value ? ' is-active' : ''}`}
            disabled={choice.disabled}
            title={choice.title}
            onClick={() => onChange(choice.value)}
          >
            {choice.label}
          </button>
        ))}
      </div>
    </div>
  )
}
