import { expect, test } from '@playwright/test'

import { fixtures, login } from './fixtures'

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
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

    // --- Invoice detail, GoBD timeline and partial credit note -------------
    await invoiceRow.getByRole('button', { name: /^View invoice INV-/ }).click()
    const invoiceSheet = adminPage.getByRole('dialog', { name: /^Invoice INV-/ })
    await expect(invoiceSheet).toBeVisible()
    await expect(invoiceSheet.getByText('GoBD event log')).toBeVisible()
    await expect(invoiceSheet.getByText('Chain verified', { exact: true })).toBeVisible()

    await invoiceSheet.getByRole('button', { name: 'Issue credit note' }).click()
    const creditDialog = adminPage.getByRole('dialog', {
      name: /^Issue credit note for INV-/,
    })
    await expect(creditDialog).toBeVisible()
    await expect(creditDialog.getByRole('button', { name: 'Cancel' })).toBeFocused()
    await creditDialog.getByLabel(/^Credit amount/).fill('2.00')
    await creditDialog.getByLabel('Reason').fill('E2E partial credit')
    await creditDialog.getByRole('button', { name: 'Issue credit note' }).click()

    await expect(creditDialog).toBeHidden()
    await expect(adminPage.getByText(/Credit note GS-\d{4}-\d{4} issued/)).toBeVisible()
    await expect(invoiceSheet.getByText('Partially credited', { exact: true })).toBeVisible()
    await expect(invoiceSheet.getByText(/^GS-\d{4}-\d{4}$/).first()).toBeVisible()
    await expect(invoiceSheet.getByText('E2E partial credit')).toBeVisible()

    await invoiceSheet.getByRole('button', { name: 'Close' }).click()
    await expect(invoiceSheet).toBeHidden()

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

    await expect(adminPage.getByText('Retry queued', { exact: true })).toBeVisible()
    await expect(outboxRow.getByText('Pending', { exact: true })).toBeVisible()
    await expect(outboxRow.getByText('0/1', { exact: true })).toBeVisible()
  } finally {
    await adminContext.close()
  }
})
