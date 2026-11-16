import { expect, test, type BrowserContext, type Page } from '@playwright/test'

const API_URL = process.env.API_URL || 'http://127.0.0.1:8000'
const TOKEN = process.env.E2E_TOKEN || 'e2e-hud-owner-token'

test.describe.serial('P3a-WP3 Operator HUD E2E', () => {
  let context: BrowserContext
  let page: Page
  const consoleErrors: string[] = []
  let snapshotFetchCount = 0

  test.beforeAll(async ({ browser }) => {
    context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
    page = await context.newPage()
    page.on('console', (msg) => {
      if (msg.type() === 'error') {
        consoleErrors.push(msg.text())
      }
    })
    page.on('request', (req) => {
      if (req.url().includes('/v1/hud/snapshot')) {
        snapshotFetchCount += 1
      }
    })
  })

  test.afterAll(async () => {
    if (context) {
      await context.close()
    }
    if (consoleErrors.length > 0) {
      console.warn('Console errors observed:', consoleErrors)
    }
  })

  test('1. initial snapshot, live SSE, unknown cost, and stage_only rendering', async () => {
    await page.goto('/', { waitUntil: 'networkidle' })

    await page.getByLabel('Operator Bearer Token').fill(TOKEN)
    await page.getByRole('button', { name: 'Connect' }).click()

    await expect(page.getByTestId('connection-status')).toBeVisible()
    await expect(page.getByTestId('connection-status')).toContainText('live')

    const tasksTable = page.locator('table.data-table').first()
    await expect(tasksTable).toContainText('Unknown')
    await expect(tasksTable).not.toContainText('$0')
    await expect(tasksTable).toContainText('Stage: scanning')
  })

  test('2. SSE reconnect with Last-Event-ID delivers committed event once', async () => {
    let sseAbortedOnce = false

    await page.route('**/v1/hud/events**', async (route) => {
      if (!sseAbortedOnce) {
        sseAbortedOnce = true
        const res = await fetch(`${API_URL}/__e2e/commit_progress`, { method: 'POST' })
        expect(res.ok).toBe(true)
        await route.abort('connectionreset')
        return
      }
      await route.continue()
    })

    await page.getByRole('button', { name: 'Resync Snapshot' }).click()
    await expect(page.locator('body')).toContainText('3 / 4 steps (75%) · verifying', {
      timeout: 15_000,
    })
    await expect(page.locator('text=3 / 4 steps (75%) · verifying')).toHaveCount(1)

    await page.unroute('**/v1/hud/events**')
  })

  test('3. resync_required triggers fresh snapshot and recovers to live', async () => {
    let sentResyncOnce = false

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
    await expect(page.getByTestId('connection-status')).toContainText('live')
    await expect.poll(() => snapshotFetchCount).toBeGreaterThanOrEqual(beforeResyncSnapshots + 2)

    await page.unroute('**/v1/hud/events**')
  })

  test('4. row.removed drops revoked row immediately', async () => {
    await expect(page.locator('text=task-e2e-revoked')).toBeVisible()

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
    expect(revokeRes.status).toBe(200)

    await expect(page.locator('body')).not.toContainText('task-e2e-revoked')
  })

  test('5. keyboard navigation and Run Inspector unknown outcome', async () => {
    await page.getByRole('tab', { name: 'Tasks & Runs' }).focus()
    await page.keyboard.press('ArrowLeft')
    await expect(page.getByRole('heading', { name: 'Attention Queue' })).toBeVisible()

    await page.getByRole('tab', { name: /Attention Queue/ }).focus()
    await page.keyboard.press('ArrowRight')
    await expect(page.getByRole('heading', { name: 'Tasks & Runs' })).toBeVisible()

    const unknownRow = page.locator('tbody tr', { hasText: 'task-e2e-unknown' })
    await unknownRow.focus()
    await page.keyboard.press('Enter')
    await expect(page.getByRole('heading', { name: 'Run Inspector' })).toBeVisible()
    await expect(page.locator('body')).toContainText(
      'Remote outcome unknown — no retry until reconciled',
    )
  })

  test('6. Agent Inventory renders certified capabilities and no_data', async () => {
    await page.getByRole('tab', { name: 'Agent Inventory' }).click()
    await expect(page.locator('body')).toContainText('agent-mock-provider')
    await expect(page.locator('body')).toContainText('no_data')
  })

  test('7. Project Assessment renders D1 diff, excluded evidence, inert XSS, and zero approve buttons', async () => {
    await page.getByRole('tab', { name: 'Project Assessment' }).click()
    await expect(page.locator('body')).toContainText('AC-001')
    await expect(page.locator('body')).toContainText('revision_mismatch')
    await expect(page.locator('body')).toContainText('Assessment #1 → #2')

    const xssVal = await page.evaluate(
      () => (window as unknown as { __e2eXssTriggered?: unknown }).__e2eXssTriggered,
    )
    expect(xssVal).toBeUndefined()

    const approveCount = await page.getByRole('button', { name: /approve|accept/i }).count()
    expect(approveCount).toBe(0)
  })
})
