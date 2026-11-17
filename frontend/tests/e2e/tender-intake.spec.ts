import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { expect, test } from '@playwright/test'

import { fixtures, login } from './fixtures'

const fixtureDir = path.dirname(fileURLToPath(import.meta.url))
const tenderFile = path.join(fixtureDir, 'fixtures', 'tender-intake.x83')

test('tender intake: upload, review, price and create an offer', async ({ page }) => {
  await login(page, fixtures.admin)

  await page.getByRole('link', { name: 'Tenders', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Tenders & pipeline', level: 1 })).toBeVisible()

  await page.getByRole('button', { name: 'Upload tender' }).click()
  const uploadDialog = page.getByRole('dialog', {
    name: /^(Upload tender|Review uploaded tender)$/,
  })
  await expect(uploadDialog).toBeVisible()

  await uploadDialog.getByLabel('Tender file').setInputFiles(tenderFile)
  await expect(uploadDialog.getByText('tender-intake.x83')).toBeVisible()
  await uploadDialog.getByRole('button', { name: 'Upload tender' }).click()

  await expect(
    uploadDialog.getByRole('heading', { name: 'Review uploaded tender' }),
  ).toBeVisible()
  await expect(uploadDialog.getByText('Tender parsed and stored')).toBeVisible()
  await expect(uploadDialog.getByText('E2E Tender Intake')).toBeVisible()
  await expect(uploadDialog.getByText('E2E-INTAKE')).toBeVisible()
  await expect(
    uploadDialog.getByText('no submission/open date in GAEB file; using now + 30 days'),
  ).toBeVisible()
  await expect(uploadDialog.getByText('Nitrilhandschuhe blau Gr. M')).toBeVisible()

  // The admin can assign the new tender to the seeded approver account.
  await uploadDialog.getByLabel('Owner username').fill(fixtures.approver.username)
  await uploadDialog.getByRole('button', { name: 'Assign owner' }).click()
  await expect(page.getByText('Tender owner assigned', { exact: true })).toBeVisible()

  await uploadDialog.getByRole('button', { name: 'Open tender' }).click()
  const tenderSheet = page.getByRole('dialog', { name: 'Tender workspace' })
  await expect(tenderSheet).toBeVisible()
  await expect(tenderSheet.getByText('E2E-INTAKE')).toBeVisible()
  await expect(tenderSheet.getByText('Nitrilhandschuhe blau Gr. M')).toBeVisible()

  await tenderSheet.getByRole('button', { name: 'Create offer' }).click()
  const offerDialog = page.getByRole('dialog', { name: /Create offer for E2E-INTAKE/ })
  await expect(offerDialog).toBeVisible()

  await offerDialog
    .getByLabel(/Supplier cost net for position/)
    .fill('10.0000')
  await offerDialog.getByLabel('Freight flat').fill('25.00')
  await offerDialog.getByLabel('Pallet unit price').fill('12.50')
  await offerDialog.getByLabel('Pallet count').fill('2')

  // Server-side deterministic preview: 10 × 10.00 × 1.15 + 25.00 + 2 × 12.50 = 165.00 net.
  await expect(offerDialog.getByText('196,35 €')).toBeVisible()
  await expect(offerDialog.getByText('31,35 €').first()).toBeVisible()

  await offerDialog.getByRole('button', { name: 'Create offer' }).click()

  await expect(offerDialog.getByText(/Offer AN-E2E-INTAKE-/)).toBeVisible()
  await expect(offerDialog.getByText('The offer is handed off as a draft')).toBeVisible()

  await offerDialog.getByRole('button', { name: 'Open approval console' }).click()

  await expect(page).toHaveURL(/\/offers\?offer=/)
  const offerSheet = page.getByRole('dialog', { name: /Offer AN-E2E-INTAKE-/ })
  await expect(offerSheet).toBeVisible()
  await expect(offerSheet.getByText('Not signed yet')).toBeVisible()
})
