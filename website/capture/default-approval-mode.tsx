/**
 * Isolated capture entry for Settings -> Security -> Approval, framing the
 * "Default approval mode for new chats" card (#6812 / #8418).
 *
 * WHY ISOLATED: reaching /settings/security/approval through the full SPA needs a
 * live gateway plus a dashboard credential. Measured without one, the shell renders
 * its prerequisite gate and the panel never mounts (0 `[data-setting-label]` nodes),
 * which is worse evidence than none. This mounts the REAL `SecurityPanel` against
 * the REAL stylesheet, theme tokens and live i18n catalog, with a server snapshot
 * seeded into the same ['kirocrewConfig'] query key the card reads in production --
 * so the row, its option order and its copy are the shipped ones.
 *
 * `SecurityPanel` is mounted whole rather than the card alone: the card is a local
 * function, and mounting the panel also shows the row in its real neighbourhood
 * (directly under YOLO duration, the sibling it was copied from).
 *
 *   ?theme=dark|light   theme tokens
 *   ?mode=<key>         seeds `agent.default_approval_mode`; omit for the unset case
 */
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Provider } from 'react-redux'
import { MemoryRouter } from 'react-router-dom'

import { initI18n } from '../src/i18n/all'
import { store } from '../src/store'
import { SecurityPanel } from '../src/pages/settings/SecurityPanel'
import '../src/index.css'

const params = new URLSearchParams(location.search)
const theme = params.get('theme') || 'dark'
// Absent on purpose for the unset frame: the card must show today's effective
// value (Normal) from a config that simply has no such key, which is what every
// existing installation looks like before this PR.
const mode = params.get('mode')
// `?fail=load` leaves the ['kirocrewConfig'] query UNSEEDED so its fetch runs and
// fails (this harness has no gateway), which is the real read-failure path rather
// than a faked notice. `?fail=save` seeds normally; the shoot script then clicks an
// option and the patch fails for the same reason.
const fail = params.get('fail')

document.documentElement.dataset.theme = theme
document.documentElement.classList.toggle('dark', theme === 'dark')

// `staleTime: Infinity` + `refetchOnMount: false` matter: the card's query refetches
// on mount otherwise, the harness has no gateway to answer it, and the failed
// refetch replaces the seeded snapshot with the load-failure notice.
const qc = new QueryClient({
  defaultOptions: { queries: { retry: false, staleTime: Infinity, refetchOnMount: fail === 'load' } },
})
// `yolo_duration` is seeded too so the sibling row above renders its real value
// rather than a blank, keeping the frame representative of the panel.
if (fail !== 'load') {
  qc.setQueryData(['kirocrewConfig'], {
    agent: { yolo_duration: 3600, ...(mode ? { default_approval_mode: mode } : {}) },
  })
}

await initI18n()

createRoot(document.getElementById('root')!).render(
  <Provider store={store}>
    <QueryClientProvider client={qc}>
      {/* `?section=approval` is how SecurityPanel selects a section when no
          `basePath` is passed -- the historical query behaviour it still supports. */}
      <MemoryRouter initialEntries={['/settings/security?section=approval']}>
        {/* Width mirrors the Settings content column so wrapping matches production. */}
        <div data-capture-root className="bg-bg text-text min-h-screen p-8">
          <div className="max-w-[860px]">
            <SecurityPanel />
          </div>
        </div>
      </MemoryRouter>
    </QueryClientProvider>
  </Provider>,
)
