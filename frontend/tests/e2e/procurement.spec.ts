import { expect, test } from '@playwright/test'

import { fixtures, login } from './fixtures'

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

test('admin accepts a supplier quote and creates a purchase order', async ({ page }) => {
  await login(page, fixtures.admin)

  await page.getByRole('link', { name: 'Tenders', exact: true }).click()
  const tenderRow = page.getByRole('row', {
    name: new RegExp(escapeRegExp(fixtures.tender_number)),
  })
  await expect(tenderRow).toBeVisible()
  await tenderRow.getByRole('button', { name: 'Open' }).click()

  const tenderSheet = page.getByRole('dialog', { name: 'Tender workspace' })
  await expect(tenderSheet).toBeVisible()
  const acceptButton = tenderSheet.getByRole('button', {
    name: `Accept quote for ${fixtures.supplier_name} ${fixtures.supplier_quote_reference}`,
  })
  await expect(acceptButton).toBeVisible()
  await acceptButton.click()
  await expect(
    page.getByText(`Supplier quote ${fixtures.supplier_quote_reference} accepted`, {
      exact: true,
    }),
  ).toBeVisible()
  await expect(tenderSheet.getByText('Ready for PO', { exact: true })).toBeVisible()

  await page.keyboard.press('Escape')
  await expect(tenderSheet).toBeHidden()

  await page.getByRole('link', { name: 'Procurement', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Procurement', level: 1 })).toBeVisible()

  await page.getByRole('button', { name: 'Create PO' }).click()
  const dialog = page.getByRole('dialog', { name: 'Create purchase order' })
  await expect(dialog).toBeVisible()
  await dialog.getByRole('combobox', { name: 'Accepted supplier quote' }).click()
  await page
    .getByRole('option', {
      name: new RegExp(escapeRegExp(fixtures.supplier_quote_reference)),
    })
    .click()
  await dialog.getByRole('button', { name: 'Create PO' }).click()

  await expect(page.getByText('Purchase order created', { exact: true })).toBeVisible()
  await expect(dialog).toBeHidden()

  const ordersSection = page.locator('section', {
    has: page.getByRole('heading', { name: 'Purchase orders', level: 2 }),
  })
  const poRow = ordersSection.getByRole('row', {
    name: new RegExp(escapeRegExp(fixtures.supplier_name)),
  })
  await expect(poRow).toBeVisible()
  await expect(poRow.getByText('Draft', { exact: true })).toBeVisible()
  await expect(poRow.getByRole('button', { name: /Send purchase order PO-/ })).toBeVisible()
})
