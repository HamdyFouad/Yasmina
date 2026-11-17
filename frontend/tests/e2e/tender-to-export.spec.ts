import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { expect, test } from '@playwright/test'

import { fixtures, login } from './fixtures'

const fixtureDir = path.dirname(fileURLToPath(import.meta.url))
const tenderFile = path.join(fixtureDir, 'fixtures', 'tender-intake.x83')

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

test('tender to export: sales prices, approver signs, and .x84 downloads', async ({
  browser,
}) => {
  const salesContext = await browser.newContext()
  const salesPage = await salesContext.newPage()
  const approverContext = await browser.newContext()
  const approverPage = await approverContext.newPage()

  try {
    await login(salesPage, fixtures.sales)
    await salesPage.getByRole('link', { name: 'Tenders', exact: true }).click()
    await expect(
      salesPage.getByRole('heading', { name: 'Tenders & pipeline', level: 1 }),
    ).toBeVisible()

    await salesPage.getByRole('button', { name: 'Upload tender' }).click()
    const uploadDialog = salesPage.getByRole('dialog', {
      name: /^(Upload tender|Review uploaded tender)$/,
    })
    await uploadDialog.getByLabel('Tender file').setInputFiles(tenderFile)
    await uploadDialog.getByRole('button', { name: 'Upload tender' }).click()
    await expect(
      uploadDialog.getByRole('heading', { name: 'Review uploaded tender' }),
    ).toBeVisible()
    await uploadDialog.getByRole('button', { name: 'Open tender' }).click()

    const tenderSheet = salesPage.getByRole('dialog', { name: 'Tender workspace' })
    await expect(tenderSheet).toBeVisible()
    await expect(tenderSheet.getByText('E2E-INTAKE')).toBeVisible()

    // Add a dashboard supplier quote, then use it to price the offer.
    await tenderSheet.getByRole('button', { name: 'Add supplier quote' }).click()
    const quoteDialog = salesPage.getByRole('dialog', { name: 'Add supplier quote' })
    await expect(quoteDialog).toBeVisible()
    await quoteDialog.getByRole('textbox', { name: 'Supplier', exact: true }).fill('Hygiene Gross GmbH')
    await quoteDialog.getByLabel(/Supplier unit price for position/).fill('10.0000')
    await quoteDialog.getByRole('button', { name: 'Save supplier quote' }).click()
    await expect(quoteDialog).toBeHidden()

    const sourcingSheet = salesPage.getByRole('dialog', { name: 'Tender workspace' })
    await expect(sourcingSheet.getByText('Hygiene Gross GmbH')).toBeVisible()

    await sourcingSheet.getByRole('button', { name: 'Create offer' }).click()
    const offerDialog = salesPage.getByRole('dialog', { name: /Create offer for E2E-INTAKE/ })
    await expect(offerDialog).toBeVisible()

    await offerDialog.getByRole('combobox', { name: 'Supplier quote', exact: true }).click()
    await salesPage.getByRole('option', { name: /Hygiene Gross GmbH/ }).click()
    await expect(offerDialog.getByText(/covers 1 of 1 position/)).toBeVisible()
    await expect(offerDialog.getByLabel(/Supplier cost net for position/)).toHaveCount(0)

    await offerDialog.getByLabel('Freight flat').fill('25.00')
    await offerDialog.getByLabel('Pallet unit price').fill('12.50')
    await offerDialog.getByLabel('Pallet count').fill('2')
    await expect(offerDialog.getByText('196,35 €')).toBeVisible()

    const workflowResponsePromise = salesPage.waitForResponse(
      (response) =>
        response.url().includes('/workflow') && response.request().method() === 'POST',
    )
    await offerDialog.getByRole('button', { name: 'Create offer' }).click()
    const workflowResponse = await workflowResponsePromise
    expect(workflowResponse.ok()).toBe(true)
    const workflowBody = (await workflowResponse.json()) as {
      offer: { id: string; offer_number: string } | null
    }
    expect(workflowBody.offer).not.toBeNull()
    const offerId = workflowBody.offer?.id
    const offerNumber = workflowBody.offer?.offer_number
    expect(offerId).toBeTruthy()
    expect(offerNumber).toBeTruthy()
    await expect(offerDialog.getByText(/Offer AN-E2E-INTAKE-/)).toBeVisible()

    await login(approverPage, fixtures.approver)
    await approverPage.goto('/offers')
    const row = approverPage.getByRole('row', {
      name: new RegExp(escapeRegExp(offerNumber ?? '')),
    })
    await expect(row).toBeVisible()
    await row.getByRole('button', { name: `Approve offer ${offerNumber}` }).click()

    const approvalDialog = approverPage.getByRole('dialog', {
      name: `Approve offer ${offerNumber}?`,
    })
    await expect(approvalDialog).toBeVisible()
    await approvalDialog.getByRole('button', { name: 'Approve and sign' }).click()
    await expect(approvalDialog).toBeHidden()
    await expect(row.getByText('Approved', { exact: true })).toBeVisible()

    await row.getByRole('button', { name: `View details for ${offerNumber}` }).click()
    const offerSheet = approverPage.getByRole('dialog', {
      name: new RegExp(`Offer ${escapeRegExp(offerNumber ?? '')}`),
    })
    await expect(offerSheet).toBeVisible()
    await expect(offerSheet.getByText('Approved', { exact: true })).toBeVisible()

    const downloadPromise = approverPage.waitForEvent('download')
    await offerSheet.getByRole('button', { name: 'Download .x84 bid' }).click()
    const download = await downloadPromise
    expect(download.suggestedFilename()).toMatch(/\.x84$/)
    const downloadedPath = await download.path()
    expect(downloadedPath).not.toBeNull()
    const xml = readFileSync(downloadedPath ?? '', 'utf8')
    expect(xml).toContain('<GAEB')
    expect(xml).toContain('<DP>84</DP>')
    expect(xml).toContain('11.5000')
  } finally {
    await salesContext.close()
    await approverContext.close()
  }
})
