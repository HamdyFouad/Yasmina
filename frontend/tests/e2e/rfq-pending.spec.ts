import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { expect, test } from '@playwright/test'

import { fixtures, login } from './fixtures'

const fixtureDir = path.dirname(fileURLToPath(import.meta.url))
const tenderFile = path.join(fixtureDir, 'fixtures', 'tender-rfq.x83')
const pendingTtl = Number(process.env['N8N_DISPATCH_PENDING_TTL_SECONDS'] ?? '900')

test.skip(
  pendingTtl > 10,
  'requires N8N_DISPATCH_PENDING_TTL_SECONDS <= 10 to exercise TTL recovery',
)

test('RFQ pending reservation returns 409 and recovers after the TTL', async ({ page }) => {
  await login(page, fixtures.sales)
  await page.getByRole('link', { name: 'Tenders', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Tenders & pipeline', level: 1 })).toBeVisible()

  await page.getByRole('button', { name: 'Upload tender' }).click()
  const uploadDialog = page.getByRole('dialog', {
    name: /^(Upload tender|Review uploaded tender)$/,
  })
  await uploadDialog.getByLabel('Tender file').setInputFiles(tenderFile)
  await uploadDialog.getByRole('button', { name: 'Upload tender' }).click()
  await uploadDialog.getByRole('button', { name: 'Open tender' }).click()

  const sheet = page.getByRole('dialog', { name: 'Tender workspace' })
  await expect(sheet.getByText('E2E-RFQ')).toBeVisible()

  await page.request.post('http://127.0.0.1:5678/control/hold')
  try {
    const openRfq = async (): Promise<void> => {
      await sheet.getByRole('button', { name: 'Dispatch RFQ' }).click()
      const dialog = page.getByRole('dialog', { name: 'Dispatch RFQ for E2E-RFQ' })
      await expect(dialog).toBeVisible()
      await dialog.getByRole('textbox', { name: 'Name', exact: true }).fill('Pending Supplier')
      await dialog
        .getByRole('textbox', { name: 'E-mail', exact: true })
        .fill('pending@example.test')
      await dialog.getByRole('button', { name: 'Dispatch RFQ' }).click()
    }

    // First call stays pending at the relay and reserves the idempotency key.
    await openRfq()
    await expect
      .poll(async () => {
        const response = await page.request.get('http://127.0.0.1:5678/control/state')
        const state = (await response.json()) as { held: number }
        return state.held
      })
      .toBe(1)

    await page
      .getByRole('dialog', { name: 'Dispatch RFQ for E2E-RFQ' })
      .getByRole('button', { name: 'Close', exact: true })
      .click()
    await expect(sheet).toBeVisible()

    // Reopening and re-entering the same payload reuses the dispatch key, so
    // the API reports the still-pending reservation as a conflict.
    await openRfq()
    const retryDialog = page.getByRole('dialog', { name: 'Dispatch RFQ for E2E-RFQ' })
    await expect(retryDialog.getByText('An identical RFQ is already pending')).toBeVisible()

    // The reservation is reclaimable after the configured micro-TTL.
    await page.waitForTimeout((pendingTtl + 0.75) * 1000)
    await retryDialog.getByRole('button', { name: 'Dispatch RFQ' }).click()
    await expect(retryDialog.getByText('RFQ dispatched')).toBeVisible()
  } finally {
    await page.request.post('http://127.0.0.1:5678/control/release')
  }
})
