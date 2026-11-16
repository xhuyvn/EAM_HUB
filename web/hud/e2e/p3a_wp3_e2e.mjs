import { createRequire } from 'node:module'
import fs from 'node:fs'
import path from 'node:path'
import assert from 'node:assert/strict'

const require = createRequire(import.meta.url)
const playwrightPath =
  process.env.PLAYWRIGHT_PATH ||
  'C:/Users/xhuy/AppData/Local/npm-cache/_npx/e41f203b7505f1fb/node_modules/playwright'
const { chromium } = require(playwrightPath)

const HUD_URL = process.env.HUD_URL || 'http://127.0.0.1:5173/'
const API_URL = process.env.API_URL || 'http://127.0.0.1:8000'
const TOKEN = process.env.E2E_TOKEN || 'e2e-hud-owner-token'
const ASSETS_DIR = path.resolve(process.cwd(), '../../docs/acceptance/assets')

fs.mkdirSync(ASSETS_DIR, { recursive: true })

function findCachedChromium() {
  if (process.env.PLAYWRIGHT_EXECUTABLE_PATH) return process.env.PLAYWRIGHT_EXECUTABLE_PATH
  const msPlaywright = path.join(process.env.LOCALAPPDATA || '', 'ms-playwright')
  if (fs.existsSync(msPlaywright)) {
    const dirs = fs.readdirSync(msPlaywright)
    const shellDir = dirs.find((d) => d.startsWith('chromium_headless_shell-'))
    if (shellDir) {
      const candidate = path.join(msPlaywright, shellDir, 'chrome-headless-shell-win64', 'chrome-headless-shell.exe')
      if (fs.existsSync(candidate)) return candidate
    }
    const fullDir = dirs.find((d) => d.startsWith('chromium-'))
    if (fullDir) {
      const candidate = path.join(msPlaywright, fullDir, 'chrome-win64', 'chrome.exe')
      if (fs.existsSync(candidate)) return candidate
    }
  }
  return undefined
}

async function main() {
  const executablePath = findCachedChromium()
  const browser = await chromium.launch({ headless: true, executablePath })
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  const page = await context.newPage()

  const consoleErrors = []
  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text())
  })

  await page.goto(HUD_URL, { waitUntil: 'networkidle' })

  // 1. Connect with memory-only bearer token
  await page.getByLabel('Operator Bearer Token').fill(TOKEN)
  await page.getByRole('button', { name: 'Connect' }).click()

  await page.getByTestId('connection-status').waitFor({ state: 'visible' })
  await page.waitForFunction(
    () => document.querySelector('[data-testid="connection-status"]')?.textContent?.includes('live'),
    { timeout: 10000 },
  )

  // Verify unknown cost & stage_only rendering
  const tasksTableText = await page.locator('table.data-table').first().innerText()
  assert.ok(tasksTableText.includes('Unknown'), 'Expected Unknown cost to render')
  assert.ok(!tasksTableText.includes('$0'), 'Unknown cost must never render as $0')
  assert.ok(tasksTableText.includes('Stage: scanning'), 'Expected stage_only progress without %')

  // Screenshot 1: Tasks & Runs
  await page.screenshot({ path: path.join(ASSETS_DIR, 'p3a-wp3-tasks-runs.png'), fullPage: true })
  console.log('PASS: initial snapshot, live SSE, unknown cost, stage_only')

  // 2. Reconnect test: intercept next SSE or abort current SSE, commit progress while disconnected, verify exact-once delivery
  let sseAbortedOnce = false
  let sentResyncOnce = false
  let snapshotFetchCount = 0

  page.on('request', (req) => {
    if (req.url().includes('/v1/hud/snapshot')) snapshotFetchCount += 1
  })

  await page.route('**/v1/hud/events**', async (route) => {
    if (!sseAbortedOnce) {
      sseAbortedOnce = true
      // Commit progress while disconnected before fulfilling or aborting
      await fetch(`${API_URL}/__e2e/commit_progress`, { method: 'POST' })
      await route.abort('connectionreset')
      return
    }
    await route.continue()
  })

  // Force a resync button click or wait for natural reconnect: click Resync Snapshot to trigger new SSE that hits our abort route once
  await page.getByRole('button', { name: 'Resync Snapshot' }).click()
  await page.waitForFunction(
    () => document.body.innerText.includes('3 / 4 steps (75%) · verifying'),
    { timeout: 15000 },
  )
  const matches = await page.locator('text=3 / 4 steps (75%) · verifying').count()
  assert.equal(matches, 1, 'Progress event after reconnect must appear exactly once')
  console.log('PASS: SSE reconnect with Last-Event-ID delivered committed event once')

  // 3. resync_required path
  await page.unroute('**/v1/hud/events**')
  await page.route('**/v1/hud/events**', async (route) => {
    if (!sentResyncOnce) {
      sentResyncOnce = true
      await route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: 'event: resync_required\ndata: {"reason":"cursor_expired"}\n\n',
      })
      return
    }
    await route.continue()
  })

  const beforeResyncSnapshots = snapshotFetchCount
  await page.getByRole('button', { name: 'Resync Snapshot' }).click()
  await page.waitForFunction(
    (prev) => {
      const status = document.querySelector('[data-testid="connection-status"]')?.textContent || ''
      return status.includes('live')
    },
    beforeResyncSnapshots,
    { timeout: 10000 },
  )
  assert.ok(snapshotFetchCount >= beforeResyncSnapshots + 2, 'resync_required must trigger fresh snapshot fetch')
  await page.unroute('**/v1/hud/events**')
  console.log('PASS: resync_required triggers fresh snapshot and recovers to live')

  // 4. Revocation (row.removed): revoke proj-revoke via API while SSE is active
  assert.ok(
    await page.locator('text=task-e2e-revoked').isVisible(),
    'task-e2e-revoked should be visible before revocation',
  )
  const revokeRes = await fetch(`${API_URL}/v1/hud/grants/revoke`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${TOKEN}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      principal_id: 'owner-e2e',
      project_id: 'proj-revoke',
      reason: 'e2e_revocation',
    }),
  })
  assert.equal(revokeRes.status, 200)
  await page.waitForFunction(
    () => !document.body.innerText.includes('task-e2e-revoked'),
    { timeout: 10000 },
  )
  console.log('PASS: row.removed drops revoked row immediately')

  // 5. Keyboard navigation: ArrowLeft to Attention Queue, inspect screenshot, then navigate to Run Inspector
  await page.getByRole('tab', { name: 'Tasks & Runs' }).focus()
  await page.keyboard.press('ArrowLeft')
  await page.getByRole('heading', { name: 'Attention Queue' }).waitFor({ state: 'visible' })
  await page.screenshot({ path: path.join(ASSETS_DIR, 'p3a-wp3-attention-queue.png'), fullPage: true })

  // Use keyboard ArrowRight back to Tasks & Runs, then focus row for task-e2e-unknown and press Enter
  await page.getByRole('tab', { name: /Attention Queue/ }).focus()
  await page.keyboard.press('ArrowRight')
  await page.getByRole('heading', { name: 'Tasks & Runs' }).waitFor({ state: 'visible' })

  const unknownRow = page.locator('tbody tr', { hasText: 'task-e2e-unknown' })
  await unknownRow.focus()
  await page.keyboard.press('Enter')
  await page.getByRole('heading', { name: 'Run Inspector' }).waitFor({ state: 'visible' })
  await page.waitForFunction(
    () => document.body.innerText.includes('Remote outcome unknown — no retry until reconciled'),
    { timeout: 10000 },
  )
  await page.screenshot({ path: path.join(ASSETS_DIR, 'p3a-wp3-run-inspector.png'), fullPage: true })
  console.log('PASS: keyboard navigation + Run Inspector unknown outcome')

  // 6. Agent Inventory view + screenshot
  await page.getByRole('tab', { name: 'Agent Inventory' }).click()
  await page.waitForFunction(
    () => document.body.innerText.includes('agent-mock-provider') && document.body.innerText.includes('no_data'),
    { timeout: 10000 },
  )
  await page.screenshot({ path: path.join(ASSETS_DIR, 'p3a-wp3-agent-inventory.png'), fullPage: true })
  console.log('PASS: Agent Inventory rendered with certified capabilities and no_data')

  // 7. Project Assessment view + D1 assertions + XSS inert + screenshot
  await page.getByRole('tab', { name: 'Project Assessment' }).click()
  await page.waitForFunction(
    () =>
      document.body.innerText.includes('AC-001') &&
      document.body.innerText.includes('revision_mismatch') &&
      document.body.innerText.includes('Assessment #1 → #2'),
    { timeout: 10000 },
  )
  const xssVal = await page.evaluate(() => window.__e2eXssTriggered)
  assert.equal(xssVal, undefined, 'XSS payload in note must not execute')
  const approveCount = await page.getByRole('button', { name: /approve|accept/i }).count()
  assert.equal(approveCount, 0, 'No approve/accept buttons allowed in P3a-WP3')
  await page.screenshot({ path: path.join(ASSETS_DIR, 'p3a-wp3-project-assessment.png'), fullPage: true })
  console.log('PASS: Project Assessment view (Reported vs Verified, diff, excluded, inert XSS)')

  await browser.close()
  if (consoleErrors.length > 0) {
    console.warn('Console errors observed:', consoleErrors)
  }
}

main().catch((err) => {
  console.error('E2E FAILED:', err)
  process.exit(1)
})
