/**
 * The light column above the wordmark (from Keystone).
 *
 * One soft shaft descending onto the title, the way a skylight falls on an
 * object in a gallery. It drifts on a forty-second cycle by a few pixels -
 * enough that the page is not dead, far too slow to read as an animation.
 */

import './light-column.css'

export function LightColumn({ intensity = 1 }: { intensity?: number }) {
  return (
    <div className="lightcol" style={{ ['--lc-i' as string]: intensity }} aria-hidden="true">
      <span className="lightcol__wash" />
      <span className="lightcol__core" />
      <span className="lightcol__pool" />
    </div>
  )
}
