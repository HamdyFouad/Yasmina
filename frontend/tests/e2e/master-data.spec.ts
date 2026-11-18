import { expect, test } from '@playwright/test'

import { fixtures, login } from './fixtures'

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

test('admin manages suppliers and customers and uses the RFQ address book', async ({ page }) => {
  await login(page, fixtures.admin)

  await page.getByRole('link', { name: 'Suppliers', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Suppliers', level: 1 })).toBeVisible()
  await expect(
    page.getByRole('row', { name: new RegExp(escapeRegExp(fixtures.supplier_name)) }),
  ).toBeVisible()

  const createdSupplierName = `E2E Supplier UI ${Date.now()}`
  await page.getByRole('button', { name: 'Create supplier' }).click()
  const supplierDialog = page.getByRole('dialog', { name: 'Create supplier' })
  await expect(supplierDialog).toBeVisible()
  await supplierDialog.getByLabel(/^Name/).first().fill(createdSupplierName)
  await supplierDialog.getByLabel('E-mail').first().fill('ui-supplier@example.de')
  await supplierDialog.getByLabel('Delivery days').fill('4')
  await supplierDialog.getByLabel('Payment terms').fill('Payable within 21 days')
  await supplierDialog.getByLabel(/^Name/).nth(1).fill('UI Contact')
  await supplierDialog.getByLabel('E-mail').nth(1).fill('ui-contact@example.de')
  await supplierDialog.getByRole('button', { name: 'Save supplier' }).click()
  await expect(page.getByText('Supplier created', { exact: true })).toBeVisible()
  await expect(supplierDialog).toBeHidden()

  const supplierRow = page.getByRole('row', {
    name: new RegExp(escapeRegExp(createdSupplierName)),
  })
  await expect(supplierRow).toBeVisible()
  await page
    .getByRole('button', { name: `Deactivate supplier ${createdSupplierName}` })
    .click()
  const supplierDeactivate = page.getByRole('dialog', {
    name: `Deactivate ${createdSupplierName}?`,
  })
  await expect(supplierDeactivate).toBeVisible()
  await expect(supplierDeactivate.getByRole('button', { name: 'Cancel' })).toBeFocused()
  await supplierDeactivate.getByRole('button', { name: 'Deactivate' }).click()
  await expect(page.getByText('Supplier deactivated', { exact: true })).toBeVisible()

  await page.getByRole('link', { name: 'Customers', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Customers', level: 1 })).toBeVisible()
  await expect(
    page.getByRole('row', { name: new RegExp(escapeRegExp(fixtures.master_customer_name)) }),
  ).toBeVisible()

  const createdCustomerName = `E2E Customer UI ${Date.now()}`
  await page.getByRole('button', { name: 'Create customer' }).click()
  const customerDialog = page.getByRole('dialog', { name: 'Create customer' })
  await expect(customerDialog).toBeVisible()
  await customerDialog.getByLabel(/^Name/).fill(createdCustomerName)
  await customerDialog.getByLabel(/^Billing street/).fill('Marktplatz 5')
  await customerDialog.getByLabel(/^Postal code/).fill('10115')
  await customerDialog.getByLabel(/^City/).fill('Berlin')
  await customerDialog.getByLabel('VAT ID').fill('DE987654321')
  await customerDialog.getByLabel('Leitweg-ID').fill('991-03730-88')
  await customerDialog.getByLabel('Payment terms').fill('Payable within 30 days')
  await customerDialog.getByLabel('Contact e-mail').fill('ui-customer@example.de')
  await customerDialog.getByRole('button', { name: 'Save customer' }).click()
  await expect(page.getByText('Customer created', { exact: true })).toBeVisible()
  await expect(customerDialog).toBeHidden()
  await expect(
    page.getByRole('row', { name: new RegExp(escapeRegExp(createdCustomerName)) }),
  ).toBeVisible()

  // The RFQ dialog reads the saved supplier address book and prefills the envelope.
  await page.getByRole('link', { name: 'Tenders', exact: true }).click()
  const tenderRow = page.getByRole('row', {
    name: new RegExp(escapeRegExp(fixtures.tender_number)),
  })
  await expect(tenderRow).toBeVisible()
  await tenderRow.getByRole('button', { name: 'Open' }).click()
  const tenderSheet = page.getByRole('dialog', { name: 'Tender workspace' })
  await expect(tenderSheet).toBeVisible()
  await tenderSheet.getByRole('button', { name: 'Dispatch RFQ' }).click()
  const rfqDialog = page.getByRole('dialog', {
    name: new RegExp(`Dispatch RFQ for ${escapeRegExp(fixtures.tender_number)}`),
  })
  await expect(rfqDialog).toBeVisible()
  await rfqDialog.getByRole('combobox', { name: 'Saved supplier' }).click()
  await page.getByRole('option', { name: fixtures.supplier_name }).click()
  await expect(rfqDialog.getByLabel(/^Name/)).toHaveValue(fixtures.supplier_name)
  await expect(rfqDialog.getByLabel(/^E-mail/)).toHaveValue('rfq@hygiene-gross.de')
  await expect(rfqDialog.getByLabel(/^Contact/)).toHaveValue('Erika Muster')
  await rfqDialog.getByRole('button', { name: 'Cancel' }).click()
  await expect(rfqDialog).toBeHidden()
})
