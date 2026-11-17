import { expect, test } from '@playwright/test'

import { fixtures, login } from './fixtures'

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

test('admin inspects workers, ingestion queues and the probe dialog', async ({ page }) => {
  await login(page, fixtures.admin)

  await page.getByRole('link', { name: 'Workers & Ingestion', exact: true }).click()
  await expect(
    page.getByRole('heading', { name: 'Workers & Ingestion', level: 1 }),
  ).toBeVisible()

  await expect(page.getByRole('heading', { name: 'Worker runs', level: 2 })).toBeVisible()
  const runsRegion = page.getByRole('region', {
    name: /Worker runs with type, resource, status, duration and error counts/,
  })
  await expect(runsRegion).toBeVisible()
  await expect(runsRegion.getByText('bayern')).toBeVisible()
  await expect(runsRegion.getByText('Succeeded', { exact: true })).toBeVisible()

  await expect(page.getByRole('heading', { name: 'Quarantined tenders', level: 2 })).toBeVisible()
  const quarantineRegion = page.getByRole('region', {
    name: /Quarantined tenders with tender, portal, status, retry budget, last error and actions/,
  })
  await expect(quarantineRegion).toBeVisible()
  await expect(
    quarantineRegion.getByText(fixtures.quarantine_tender_number, { exact: true }),
  ).toBeVisible()
  await expect(quarantineRegion.getByText('Malware quarantine')).toBeVisible()

  const dlqRegion = page.getByRole('region', {
    name: /Dead-letter queue with tender, portal, status, retry budget, last error and actions/,
  })
  await expect(page.getByRole('heading', { name: 'Dead-letter queue', level: 2 })).toBeVisible()
  await expect(dlqRegion.getByText(fixtures.tender_number, { exact: true })).toBeVisible()

  // The probe dialog is read-only and scoped to configured portals.
  await page.route(/\/api\/v1\/ops\/portals\/[^/]+\/probe/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        read_only: true,
        probed_at: '2026-02-01T10:00:00Z',
        elapsed_seconds: 0.42,
        portal: {
          id: 'bayern',
          name: 'Vergabe Bayern',
          engine: 'standard',
          start_url: 'https://example.test/',
          enabled: true,
          api_feed: false,
        },
        max_pages: 1,
        pages_fetched: 1,
        notices_discovered: 3,
        notices_parsed: 0,
        skipped: { cpv: 1, invalid: 0, stale: 2 },
        details_fetched: 0,
        errors: ['detail fetch failed'],
        issues: [
          {
            url: 'https://example.test/notice/1',
            code: 'missing_deadline',
            message: 'Deadline selector returned no value',
          },
        ],
        issues_truncated: 0,
        warnings: ['No publication date selector matched'],
        zero_results: true,
      }),
    })
  })

  await page.getByRole('button', { name: 'Run probe' }).click()
  const probeDialog = page.getByRole('dialog', { name: 'Run portal probe' })
  await expect(probeDialog).toBeVisible()
  await expect(
    probeDialog.getByText(/no database writes, no MinIO archive, no ingestion job/i),
  ).toBeVisible()
  await expect(probeDialog.getByRole('combobox', { name: 'Portal' })).toBeVisible()
  await probeDialog.getByRole('button', { name: 'Run probe' }).click()
  await expect(probeDialog.getByText('Read-only probe complete: Vergabe Bayern')).toBeVisible()
  await expect(probeDialog.getByText(/Zero results:/)).toBeVisible()
  await expect(probeDialog.getByText('No publication date selector matched')).toBeVisible()
  await expect(probeDialog.getByText('detail fetch failed')).toBeVisible()
  await expect(probeDialog.getByText('missing_deadline')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(probeDialog).toBeHidden()

  // A dead-letter job can be safely re-queued; focus starts on Cancel.
  await dlqRegion
    .getByRole('button', {
      name: `Retry ingestion job for ${fixtures.tender_number}`,
    })
    .click()
  const retryDialog = page.getByRole('dialog', { name: 'Retry dead-letter job?' })
  await expect(retryDialog).toBeVisible()
  await expect(retryDialog.getByRole('button', { name: 'Cancel' })).toBeFocused()
  await retryDialog.getByRole('button', { name: 'Retry job' }).click()
  await expect(page.getByText('Ingestion job re-queued', { exact: true })).toBeVisible()

  const queueRegion = page.getByRole('region', {
    name: /Ingestion queue with tender, portal, status, retry budget, last error/,
  })
  await expect(
    queueRegion.getByRole('row', {
      name: new RegExp(escapeRegExp(fixtures.tender_number)),
    }),
  ).toBeVisible()

  // Quarantine discard is destructive and focuses Cancel first.
  await quarantineRegion
    .getByRole('button', {
      name: `Discard ingestion job for ${fixtures.quarantine_tender_number}`,
    })
    .click()
  const discardDialog = page.getByRole('dialog', {
    name: 'Discard malware-quarantined job?',
  })
  await expect(discardDialog).toBeVisible()
  await expect(discardDialog.getByRole('button', { name: 'Cancel' })).toBeFocused()
  await discardDialog.getByRole('button', { name: 'Discard job' }).click()
  await expect(page.getByText('Ingestion job discarded', { exact: true })).toBeVisible()
})

test('sales and approvers cannot access the workers and ingestion console', async ({ browser }) => {
  for (const credentials of [fixtures.sales, fixtures.approver]) {
    const context = await browser.newContext()
    const page = await context.newPage()
    try {
      await login(page, credentials)
      await expect(
        page.getByRole('link', { name: 'Workers & Ingestion', exact: true }),
      ).toHaveCount(0)

      await page.goto('/ops/workers')
      await expect(page).toHaveURL(/\/$/)
      await expect(page.getByRole('heading', { name: 'Overview', level: 1 })).toBeVisible()
    } finally {
      await context.close()
    }
  }
})
