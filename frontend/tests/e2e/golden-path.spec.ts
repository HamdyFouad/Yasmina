import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

interface Credentials {
  username: string
  password: string
}

interface Fixtures {
  approver: Credentials
  admin: Credentials
  offer_number: string
  outbox_offer_number: string
  customer_name: string
  buyer_reference: string
  invoice_net: string
  invoice_gross: string
  tender_number: string
}

function fixturePath(): string {
  const override = process.env['E2E_FIXTURES']
  if (override !== undefined && override.length > 0) {
    return path.resolve(override)
  }
  return path.join(path.dirname(fileURLToPath(import.meta.url)), '.fixtures.json')
}

function loadFixtures(): Fixtures {
  const file = fixturePath()
  try {
    // Tolerate a UTF-8 BOM (editors and PowerShell may add one).
    return JSON.parse(readFileSync(file, 'utf8').replace(/^\uFEFF/, '')) as Fixtures
  } catch (error) {
    throw new Error(
      `E2E fixtures missing at ${file}. Run "python scripts/seed_e2e.py" first. (${String(error)})`,
    )
  }
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

const fixtures = loadFixtures()

async function login(page: Page, credentials: Credentials): Promise<void> {
  await page.goto('/login')
  await page.getByLabel('Username').fill(credentials.username)
  await page.getByLabel('Password').fill(credentials.password)
  await page.getByRole('button', { name: /sign in/i }).click()
  await expect(page.getByRole('link', { name: 'Offers', exact: true })).toBeVisible()
}

test('golden path: approve with invoice, pay it, and retry the outbox job', async ({
  browser,
}) => {
  const approverContext = await browser.newContext()
  const approverPage = await approverContext.newPage()
  try {
    await login(approverPage, fixtures.approver)

    // --- Offer workspace + four-eyes approval with e-invoice issuance ------
    await approverPage.getByRole('link', { name: 'Offers', exact: true }).click()
    await expect(
      approverPage.getByRole('heading', { name: 'Offers', level: 1 }),
    ).toBeVisible()

    const offerRow = approverPage.getByRole('row', {
      name: new RegExp(escapeRegExp(fixtures.offer_number)),
    })
    await expect(offerRow).toHaveCount(1)
    await offerRow
      .getByRole('button', { name: `Approve offer ${fixtures.offer_number}` })
      .click()

    const dialog = approverPage.getByRole('dialog')
    await expect(
      dialog.getByRole('heading', { name: `Approve offer ${fixtures.offer_number}?` }),
    ).toBeVisible()

    await dialog.getByRole('button', { name: 'Generate e-invoice', exact: true }).click()

    await dialog.getByLabel(/Street and house number/).fill('Rathausstr. 2')
    await dialog.getByLabel(/Postal code/).fill('12345')
    await dialog.getByLabel(/^City/).fill('Musterstadt')
    await dialog.getByLabel(/Buyer e-mail/).fill('einkauf@musterstadt.de')
    await dialog.getByLabel(/Buyer reference \(Leitweg-ID\)/).fill(fixtures.buyer_reference)
    await dialog.getByLabel(/Due date/).fill('2030-01-15')
    await dialog.getByLabel(/^IBAN/).fill('DE89370400440532013000')
    await dialog.getByLabel(/Payment terms/).fill('Payable within 14 days')

    await dialog
      .getByRole('button', { name: 'Approve, sign and issue invoice' })
      .click()

    await expect(dialog).toBeHidden()
    await expect(
      approverPage.getByText(new RegExp(`Offer ${escapeRegExp(fixtures.offer_number)} signed`)),
    ).toBeVisible()
    await expect(approverPage.getByText(/XRechnung: offers\/.+\/invoice\.xml/)).toBeVisible()
    await expect(approverPage.getByText(/ZUGFeRD: offers\/.+\/invoice\.pdf/)).toBeVisible()
  } finally {
    await approverContext.close()
  }

  // The approver cannot read invoices or record payments; the golden path
  // continues with the seeded admin, which also covers the RBAC boundary.
  const adminContext = await browser.newContext()
  const adminPage = await adminContext.newPage()
  try {
    await login(adminPage, fixtures.admin)

    // --- Invoice generated during approval + payment -----------------------
    await adminPage.getByRole('link', { name: 'Invoices', exact: true }).click()
    await expect(
      adminPage.getByRole('heading', { name: 'Invoices & billing', level: 1 }),
    ).toBeVisible()

    const invoiceRow = adminPage.getByRole('row', { name: /INV-\d{4}-\d{4}/ })
    await expect(invoiceRow).toHaveCount(1)
    await expect(invoiceRow.getByText('Issued', { exact: true })).toBeVisible()

    await invoiceRow.getByRole('button', { name: 'Mark as paid' }).click()
    const paymentDialog = adminPage.getByRole('dialog', { name: 'Mark invoice as paid' })
    await paymentDialog.getByLabel(/Bank reference/).fill('E2E-PAYMENT-1')
    await paymentDialog.getByRole('button', { name: 'Record payment' }).click()

    await expect(adminPage.getByText('Payment recorded')).toBeVisible()
    await expect(invoiceRow.getByText('Paid', { exact: true })).toBeVisible()

    // --- Outbox retry resets the terminal FAILED job ----------------------
    await adminPage.getByRole('link', { name: 'Outbox', exact: true }).click()
    await expect(adminPage.getByRole('heading', { name: 'ERPNext outbox', level: 1 })).toBeVisible()

    const outboxRow = adminPage.getByRole('row', {
      name: new RegExp(escapeRegExp(fixtures.outbox_offer_number)),
    })
    await expect(outboxRow.getByText('Failed', { exact: true })).toBeVisible()
    await outboxRow
      .getByRole('button', { name: `Retry quotation ${fixtures.outbox_offer_number}` })
      .click()

    await expect(adminPage.getByText('Retry queued')).toBeVisible()
    await expect(outboxRow.getByText('Pending', { exact: true })).toBeVisible()
    await expect(outboxRow.getByText('0/1', { exact: true })).toBeVisible()
  } finally {
    await adminContext.close()
  }
})
