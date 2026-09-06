/**
 * An older page lands IMMEDIATELY. It is never held waiting for the scroller.
 *
 * The payload used to be held until the scroller had been quiet for a beat,
 * because splicing rows mid-glide raced the pre-paint anchor machinery against
 * the window recompute the gesture itself schedules (phone rig: per-landing
 * kilopixel jumps). Measured on the device with the spans split apart, that hold
 * was 1.95s of a 2.1s wait -- 92% -- against a 0.16s request.
 *
 * And it could not be tuned down: the hold ends when the READER stops, so its
 * length is however long they keep scrolling. That is precisely the gesture that
 * needs the page, and this hold ended on quiescence well inside its own 2.5s cap,
 * so lowering the cap would have changed nothing.
 *
 * Pinned as source shape rather than behaviour because the failure mode is a
 * REINSTATEMENT -- a reviewer restoring the wait to fix a landing jump. If those
 * jumps reproduce, the fix is to stop splicing at all (ship geometry up front,
 * hydrate content in place), not to make the reader wait again.
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const SRC = readFileSync(resolve(__dirname, '../store/chatSlice.ts'), 'utf8')

/** The loadOlderMessages thunk body, so a pin cannot pass on an unrelated file.
 *  Anchored on the DECLARATION, not the action-type string: the type is
 *  `chat/loadOlder`, and guessing it from the export name is how this slice
 *  silently became empty and reported four green pins as red. */
function thunkBody(): string {
  const i = SRC.indexOf('export const loadOlderMessages = createAsyncThunk(')
  expect(i, 'loadOlderMessages thunk not found').toBeGreaterThan(0)
  const end = SRC.indexOf('export const', i + 1)
  return SRC.slice(i, end > 0 ? end : undefined)
}

describe('older pages are not held for scroll quiescence', () => {
  it('does not await the quiescence signal', () => {
    expect(thunkBody()).not.toMatch(/whenScrollQuiet/)
  })

  it('does not import it either, so a reinstatement is a visible edit', () => {
    // A dormant import is how a removed mechanism creeps back one line at a time.
    expect(SRC).not.toMatch(/from '\.\.\/lib\/scrollQuiet'/)
  })

  it('still reports the hold span, so the overlay can prove it is zero', () => {
    // The instrument is what tells "no hold" apart from "the instrument broke" --
    // a distinction that already cost a round tonight when the paint timing went
    // silent after its trigger was deleted.
    expect(thunkBody()).toMatch(/devOlderSpans\(netMs, 0\)/)
  })

  it('still refuses to splice a page from a chat the reader has left', () => {
    // The abort check outlived the wait it used to follow: a slot switch can land
    // between the response and the return, and a stale page is worse than none.
    expect(thunkBody()).toMatch(/controller\.signal\.aborted/)
  })

  it('still times the request itself', () => {
    // net vs hold is the split that overturned two wrong diagnoses; losing the
    // net measurement would put us back to one ambiguous number.
    expect(thunkBody()).toMatch(/const netStart = Date\.now\(\)/)
  })
})
