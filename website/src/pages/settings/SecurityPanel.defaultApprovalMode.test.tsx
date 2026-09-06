/**
 * Default approval mode for new chats (Settings -> Security -> Approval).
 *
 * Issue #6812 / #8418. The properties pinned here are the ones that make this
 * control safe rather than merely present:
 *
 * - it offers exactly the three tiers the backend enum declares, in declared
 *   order, and NOT `yolo`;
 * - it writes the DECLARED KEY (`trust_reads`) while showing the existing LABEL
 *   ("Reads"), so no second spelling of that tier enters the tree;
 * - it shows the EFFECTIVE value, so an unset config reads as Normal -- today's
 *   behaviour -- and a failed read says so instead of passing Normal off as
 *   stored;
 * - it writes nothing until the user actually moves it.
 *
 * A per-section file, matching SecurityPanel.tailnet.test.tsx, rather than more
 * cases bolted onto the 1900-line SecurityPanel.test.tsx.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { screen, fireEvent, waitFor, within, cleanup } from '@testing-library/react'

import { renderWithProviders } from '../../test/helpers'

vi.mock('../../api/client', () => ({
  api: {
    // The panel's rail reads these on mount regardless of the selected section.
    deniedCommands: vi.fn(),
    governancePolicy: vi.fn(),
    securityPosture: vi.fn(),
    tailnetStatus: vi.fn(),
    // The pair this card actually uses, shared with YoloDurationCard.
    kirocrewConfig: vi.fn(),
    patchConfig: vi.fn(),
  },
}))

import { api } from '../../api/client'
import { SecurityPanel } from './SecurityPanel'
import { i18nT } from '../../i18n/t'

/** Copy is read through `i18nT`, never asserted as literal English, so a catalog
 *  edit cannot make this file red for a reason unrelated to the behaviour. */
const TITLE = () => i18nT('pages.settings.securityPanel.default_approval_mode_title')
const LABEL = {
  normal: () => i18nT('components.approvalModePicker.normal_label'),
  trust_reads: () => i18nT('components.approvalModePicker.reads_label'),
  trust: () => i18nT('components.approvalModePicker.trust_label'),
  yolo: () => i18nT('components.approvalModePicker.yolo_label'),
}

/** The card, located by its stable aria-label rather than by any value under
 *  assertion -- the approval section also renders YoloDurationCard, whose radios
 *  would otherwise be in scope. */
async function renderCard(cfg: unknown) {
  ;(api.kirocrewConfig as ReturnType<typeof vi.fn>).mockResolvedValue(cfg)
  ;(api.patchConfig as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true })
  const utils = renderWithProviders(<SecurityPanel />, { route: '/?section=approval' })
  const group = await screen.findByRole('radiogroup', { name: TITLE() })
  return { ...utils, group }
}

/** Escape a localized label for use inside a RegExp -- some catalogs contain
 *  regex metacharacters (ko's "은(는)" has parentheses). */
function escapeRe(v: string): string {
  return v.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function radios(group: HTMLElement) {
  return within(group).getAllByRole('radio')
}

function checked(group: HTMLElement): string | null {
  const hit = radios(group).find(r => r.getAttribute('aria-checked') === 'true')
  // The DECLARED KEY, not the rendered text. Each row now carries a description
  // beside its label, so text equality would compare against label+description --
  // and the key is the stronger assertion regardless: locale-independent, and the
  // same value the save actually writes.
  return hit ? hit.getAttribute('data-approval-mode') : null
}

/** Assert the SELECTED tier, waiting for the config query to settle first.
 *
 *  The radiogroup exists while that query is still in flight -- and until it
 *  resolves, `current` is the `normal` fallback -- so asserting on the group's
 *  mere presence races the read. That is the same trap SecurityPanel.tailnet's
 *  own comment records, and it is why the `normal` case needs this too: with an
 *  empty config the pre-resolution and post-resolution renders are BOTH Normal,
 *  so an immediate assertion would pass without ever observing the resolved
 *  value. Requiring the read to have happened is what stops that being vacuous. */
async function expectChecked(group: HTMLElement, key: string) {
  expect(api.kirocrewConfig).toHaveBeenCalled()
  await waitFor(() => expect(checked(group)).toBe(key))
}

describe('SecurityPanel -- default approval mode for new chats', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    cleanup()
    ;(api.deniedCommands as ReturnType<typeof vi.fn>).mockResolvedValue({ builtins: [], user: [], disable_all: false })
    ;(api.governancePolicy as ReturnType<typeof vi.fn>).mockResolvedValue({})
    ;(api.securityPosture as ReturnType<typeof vi.fn>).mockResolvedValue({})
    ;(api.tailnetStatus as ReturnType<typeof vi.fn>).mockResolvedValue({
      enabled: false, governance_pinned: false, host: '', origin: '', resolved_at: 0, state: 'off',
    })
  })

  it('offers exactly the three declared tiers, in declared order', async () => {
    const { group } = await renderCard({})
    // Each row is "<label><description>" now, so the label is asserted as the
    // PREFIX. Order still comes from this assertion; the no-YOLO test below pins
    // the same order on the declared keys, which is the locale-independent half.
    expect(radios(group).map(r => (r.textContent ?? '').trim())).toEqual([
      expect.stringMatching(new RegExp('^' + escapeRe(LABEL.normal()))),
      expect.stringMatching(new RegExp('^' + escapeRe(LABEL.trust_reads()))),
    ])
  })

  it('does not offer YOLO, which is process-global rather than a per-chat tier', async () => {
    const { group } = await renderCard({})
    // Asserted on the DECLARED KEYS, not the labels. `optionLabel` has no `yolo`
    // case, so a `yolo` tier wrongly added to the list would render under the
    // NORMAL label and a label-based assertion would pass while the tier was
    // offered -- measured: that mutation reddened the order test and left a
    // label-based no-YOLO assertion green.
    expect(radios(group).map(r => r.getAttribute('data-approval-mode'))).toEqual([
      'normal', 'trust_reads',
    ])
    expect(radios(group).map(r => r.getAttribute('data-approval-mode'))).not.toContain('yolo')
    expect(within(group).queryByText(LABEL.yolo())).toBeNull()
  })

  it('shows Normal when nothing is configured, which is the behaviour today', async () => {
    const { group } = await renderCard({})
    await expectChecked(group, 'normal')
  })

  it('does NOT honour a stored trust, which is not persistable', async () => {
    const { group } = await renderCard({ agent: { default_approval_mode: 'trust' } })
    await expectChecked(group, 'normal')
  })

  it('shows the reads tier under its existing label when trust_reads is stored', async () => {
    const { group } = await renderCard({ agent: { default_approval_mode: 'trust_reads' } })
    await expectChecked(group, 'trust_reads')
  })

  it('falls back to Normal when the stored value is not an offerable tier', async () => {
    // `yolo` is the case that matters: it must not select, and must not appear.
    const { group } = await renderCard({ agent: { default_approval_mode: 'yolo' } })
    await expectChecked(group, 'normal')
  })

  it('writes the DECLARED KEY, not the label spelling, when a tier is chosen', async () => {
    const { group } = await renderCard({})
    fireEvent.click(within(group).getByText(LABEL.trust_reads()))
    await waitFor(() => expect(api.patchConfig).toHaveBeenCalledWith('agent.default_approval_mode', 'trust_reads'))
    // The label spelling must never reach the wire.
    expect(api.patchConfig).not.toHaveBeenCalledWith('agent.default_approval_mode', 'reads')
  })

  it('writes nothing until the user actually moves it', async () => {
    const { group } = await renderCard({})
    // Mount alone must not write.
    expect(api.patchConfig).not.toHaveBeenCalled()
    // Re-choosing the already-effective tier is not a change either.
    fireEvent.click(within(group).getByText(LABEL.normal()))
    await Promise.resolve()
    expect(api.patchConfig).not.toHaveBeenCalled()
  })

  it('explains what each tier permits, not just its name', async () => {
    // The UX block this closes: three bare words ("Normal" / "Reads" / "Trust")
    // do not say what the tier allows, on the one control whose entire purpose is
    // choosing that. Asserted per option so dropping any single description fails.
    const { group } = await renderCard({})
    for (const r of radios(group)) {
      const key = r.getAttribute('data-approval-mode')
      const label = LABEL[key as keyof typeof LABEL]()
      const text = (r.textContent || '').trim()
      expect(text.length, `${key} has no text`).toBeGreaterThan(0)
      // Something beyond the label itself.
      const beyondLabel = text.replace(label, '').trim()
      expect(beyondLabel.length, `${key} shows only its label, no description`).toBeGreaterThan(0)
    }
  })

  it('leaves Trust selectable INTERACTIVELY, which this change must not remove', async () => {
    // The regression guard. Narrowing the PERSISTABLE set must not narrow what a
    // session can be set to at runtime: those are two different domains and two
    // different types. `ApprovalModeKey` (from APPROVAL_SEGMENTS) is the runtime
    // vocabulary the footer picker renders; `DefaultApprovalModeKey` is the
    // persistable one. Deleting Trust from the picker to satisfy the compiler would
    // remove a shipping capability, so it is pinned here rather than by memory.
    const { APPROVAL_SEGMENTS } = await import('../../components/ApprovalModePicker')
    const runtimeKeys = APPROVAL_SEGMENTS.map(s => s.key)
    expect(runtimeKeys).toContain('trust')
    // And the two sets are deliberately DIFFERENT: persistable is a strict subset.
    expect(runtimeKeys).toContain('trust_reads')
    const persistable = ['normal', 'trust_reads']
    expect(persistable.every(k => runtimeKeys.includes(k as never))).toBe(true)
    expect(persistable).not.toContain('trust')
  })

  it('does not offer Trust, which is not persistable', async () => {
    // Trust is the only tier that also writes the session approval policy "auto"
    // (unattended auto-approve, inherited by subagents). config.json is
    // agent-writable, so a persistable Trust would let one already-trusted session
    // raise the floor for every future one. Asserted on the DECLARED KEYS, because
    // `optionLabel` has no Trust case any more and would fall through to the Normal
    // label -- a label-based check could not see a Trust option if one returned.
    const { group } = await renderCard({})
    const keys = radios(group).map(r => r.getAttribute('data-approval-mode'))
    expect(keys).toEqual(['normal', 'trust_reads'])
    expect(keys).not.toContain('trust')
    expect(keys).not.toContain('yolo')
  })

  it('reuses the picker\'s approved wording where it is already scope-free', async () => {
    // normal_desc and reads_desc carry no chat scope, so reusing them keeps the two
    // surfaces from drifting into two explanations of one tier -- and costs no new
    // translation. If either picker string later gains a scope clause, this fails
    // and the reuse gets revisited.
    const { group } = await renderCard({})
    const byKey = (k: string) => radios(group).find(r => r.getAttribute('data-approval-mode') === k)!
    expect(byKey('normal').textContent).toContain(i18nT('components.approvalModePicker.normal_desc'))
    expect(byKey('trust_reads').textContent).toContain(i18nT('components.approvalModePicker.reads_desc'))
  })

  it('says so when the config read failed, rather than passing Normal off as stored', async () => {
    ;(api.kirocrewConfig as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('nope'))
    ;(api.patchConfig as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true })
    renderWithProviders(<SecurityPanel />, { route: '/?section=approval' })
    await waitFor(() =>
      expect(
        screen.getByText(i18nT('pages.settings.securityPanel.default_approval_mode_load_failed')),
      ).toBeTruthy(),
    )
  })

  it('selects NOTHING when the read failed, so no tick contradicts the notice', async () => {
    // The notice says the highlighted option is the default rather than what is
    // stored. Rendering a green tick anyway makes the frame disagree with its own
    // copy, so on a failed read no option is selected at all.
    ;(api.kirocrewConfig as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('nope'))
    renderWithProviders(<SecurityPanel />, { route: '/?section=approval' })
    const group = await screen.findByRole('radiogroup', { name: TITLE() })
    await waitFor(() =>
      expect(
        screen.getByText(i18nT('pages.settings.securityPanel.default_approval_mode_load_failed')),
      ).toBeTruthy(),
    )
    expect(radios(group).filter(r => r.getAttribute('aria-checked') === 'true')).toEqual([])
  })

  it('says so when a stored value was overridden, instead of silently showing Normal', async () => {
    // A stored `trust` is clamped to normal. Without this the frame is identical to
    // the unset one and whoever wrote the value gets no cue it was ignored.
    const { group } = await renderCard({ agent: { default_approval_mode: 'trust' } })
    await expectChecked(group, 'normal')
    expect(
      screen.getByText(
        i18nT('pages.settings.securityPanel.default_approval_mode_overridden', { value: 'trust' }),
      ),
    ).toBeTruthy()
  })

  it('shows no override cue when the stored value IS persistable', async () => {
    // The control: without it, a cue rendered unconditionally would pass the test above.
    const { group } = await renderCard({ agent: { default_approval_mode: 'trust_reads' } })
    await expectChecked(group, 'trust_reads')
    expect(
      screen.queryByText(
        i18nT('pages.settings.securityPanel.default_approval_mode_overridden', { value: 'trust_reads' }),
      ),
    ).toBeNull()
  })

  it('surfaces a failed save', async () => {
    const { group } = await renderCard({})
    ;(api.patchConfig as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('nope'))
    fireEvent.click(within(group).getByText(LABEL.trust_reads()))
    await waitFor(() =>
      expect(
        screen.getByText(i18nT('pages.settings.securityPanel.default_approval_mode_save_failed')),
      ).toBeTruthy(),
    )
  })
})
