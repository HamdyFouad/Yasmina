import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { expect, test } from '@playwright/test'

import { fixtures, login } from './fixtures'

const fixtureDir = path.dirname(fileURLToPath(import.meta.url))
const invalidTender = path.join(fixtureDir, 'fixtures', 'invalid-tender.x83')
const validTender = path.join(fixtureDir, 'fixtures', 'tender-intake.x83')

test('upload errors: malformed GAEB and mocked ClamAV rejection', async ({ page }) => {
  await login(page, fixtures.sales)

  await page.getByRole('link', { name: 'Tenders', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Tenders & pipeline', level: 1 })).toBeVisible()

  await page.getByRole('button', { name: 'Upload tender' }).click()
  const dialog = page.getByRole('dialog', { name: /^(Upload tender|Review uploaded tender)$/ })
  await expect(dialog).toBeVisible()

  await dialog.getByLabel('Tender file').setInputFiles(invalidTender)
  await dialog.getByRole('button', { name: 'Upload tender' }).click()
  await expect(dialog.getByText('The tender could not be parsed')).toBeVisible()
  await expect(dialog.getByText(/no usable tender positions/)).toBeVisible()

  // The same dialog stays open; swap in a valid file and mock a scanner rejection.
  await page.route('**/api/v1/tenders/upload', async (route) => {
    await route.fulfill({
      status: 422,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'malware detected: Eicar-Test-Signature' }),
    })
  })
  try {
    await dialog.getByLabel('Tender file').setInputFiles(validTender)
    await dialog.getByRole('button', { name: 'Upload tender' }).click()
    await expect(dialog.getByText('Rejected by malware scanning')).toBeVisible()
    await expect(dialog.getByText(/Eicar-Test-Signature/)).toBeVisible()
  } finally {
    await page.unroute('**/api/v1/tenders/upload')
  }
})
