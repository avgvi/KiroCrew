/**
 * Screenshot harness for the "Default approval mode for new chats" Settings row
 * (Settings -> Security -> Approval). Issues #6812 / #8418.
 *
 * Shoots the ISOLATED capture entry (`capture/default-approval-mode.html`), not the
 * full SPA: reaching /settings/security/approval for real needs a gateway and a
 * dashboard credential, and without one the shell renders its prerequisite gate and
 * the panel never mounts. See that entry's header for the details.
 *
 * Three FIXTURE STATES rather than a click sequence, because what the row renders is
 * owned by the ['kirocrewConfig'] read; seeding it shows each steady state exactly as
 * a user meets it:
 *   1. unset       -> Normal, which is the behaviour on `main` today
 *   2. trust_reads -> the middle tier, under its existing "Reads" label
 *   3. trust       -> the tier both reporters asked for
 *
 * Usage:
 *   ./node_modules/vite/bin/vite.js --host 127.0.0.1 --port 6812 --strictPort  # other shell
 *   node scripts/capture-default-approval-mode.mjs [baseUrl] [outDir]
 */
import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'

const BASE = process.argv[2] || 'http://127.0.0.1:6812'
const OUT = process.argv[3] || '../temp-screenshots/default-approval-mode'
mkdirSync(OUT, { recursive: true })

/** Each scene is a value of `agent.default_approval_mode`; `null` means the key is absent. */
const SCENES = [
  { name: '01-unset-normal', mode: null, expect: 'normal' },
  { name: '02-reads', mode: 'trust_reads', expect: 'trust_reads' },
  // A stored `trust` is NOT persistable and must render as Normal -- the security
  // property of this feature's shape, photographed rather than asserted in prose.
  { name: '03-stored-trust-refused', mode: 'trust', expect: 'normal' },
]

// The two error states. Each waits for its OWN notice text before shooting, so a
// frame can never be a plausible-looking picture of a card that rendered nothing --
// the failure mode that produced empty crops earlier in this work.
const FAIL_SCENES = [
  { name: '04-load-failed', fail: 'load', key: 'default_approval_mode_load_failed' },
  { name: '05-save-failed', fail: 'save', key: 'default_approval_mode_save_failed' },
]

const browser = await chromium.launch()
const context = await browser.newContext({ viewport: { width: 1000, height: 900 }, deviceScaleFactor: 2 })
const page = await context.newPage()

for (const scene of SCENES) {
  const q = new URLSearchParams({ theme: 'dark' })
  if (scene.mode) q.set('mode', scene.mode)
  await page.goto(`${BASE}/capture/default-approval-mode.html?${q}`, { waitUntil: 'domcontentloaded' })
  await page.waitForSelector('[data-capture-root]', { timeout: 20000 })

  // Wait on the radio carrying the DECLARED key and reporting itself checked, not on
  // the card merely existing. A frame taken before the seeded read lands would
  // photograph the `normal` fallback for every scene and quietly misrepresent two of
  // the three -- the failure mode this wait exists to prevent.
  const group = page.locator('[role="radiogroup"]', { has: page.locator('[data-approval-mode="trust_reads"]') }).first()
  await group.waitFor({ state: 'visible', timeout: 20000 })
  await page.locator(`[data-approval-mode="${scene.expect}"][aria-checked="true"]`).first().waitFor({ timeout: 20000 })

  // The enclosing card, so the frame carries the title and description the row is
  // read with -- not the option list alone. `data-setting-label` holds the LOCALIZED
  // title rather than a stable key, so it is not usable as a selector here.
  const card = group.locator('xpath=..')
  const out = `${OUT}/${scene.name}.png`
  await card.screenshot({ path: out })
  console.log('wrote', out)
}

// Error-state frames.
{
  const en = JSON.parse(
    (await import('node:fs')).readFileSync('src/i18n/locales/en.manual.json', 'utf8'),
  ).pages.settings.securityPanel
  const context = await browser.newContext({ viewport: { width: 1000, height: 900 }, deviceScaleFactor: 2 })
  const page = await context.newPage()
  for (const scene of FAIL_SCENES) {
    const q = new URLSearchParams({ theme: 'dark', fail: scene.fail })
    await page.goto(`${BASE}/capture/default-approval-mode.html?${q}`, { waitUntil: 'domcontentloaded' })
    await page.waitForSelector('[data-capture-root]', { timeout: 20000 })
    if (scene.fail === 'save') {
      // Trigger a real save; the patch fails because there is no gateway.
      await page.locator('[data-approval-mode="trust_reads"]').first().click()
    }
    const expected = en[scene.key]
    if (!expected) throw new Error(`no English string for ${scene.key}`)
    // ASSERT the frame's content before the shutter, not after.
    await page.getByText(expected, { exact: false }).first().waitFor({ timeout: 20000 })
    const card = page.locator('[role="radiogroup"]', { has: page.locator('[data-approval-mode="trust_reads"]') }).first().locator('xpath=..')
    const out = `${OUT}/${scene.name}.png`
    await card.screenshot({ path: out })
    console.log('wrote', out, `(asserted: ${scene.key})`)
  }
  await context.close()
}

await browser.close()
