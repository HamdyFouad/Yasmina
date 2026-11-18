import { expect, test } from '@playwright/test'

import { fixtures, login } from './fixtures'

test('admin reviews reporting, notifications and audit exports', async ({ page }) => {
  await login(page, fixtures.admin)

  const notificationButton = page.getByRole('button', { name: /^Notifications/ })
  await expect(notificationButton).toBeVisible()
  await notificationButton.click()
  await expect(page.getByText('Critical operational alerts', { exact: true })).toBeVisible()
  await page.keyboard.press('Escape')

  await page.getByRole('link', { name: 'Reporting', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Reporting', level: 1 })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Commercial KPIs', level: 2 })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Pipeline', level: 2 })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Win / loss', level: 2 })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Tender conversion', level: 2 })).toBeVisible()
  await expect(
    page.getByRole('heading', {
      name: 'Margin by customer, supplier and tender',
      level: 2,
    }),
  ).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Invoice aging', level: 2 })).toBeVisible()
  await expect(page.getByText('Pipeline value', { exact: true })).toBeVisible()

  await page.getByRole('link', { name: 'Audit Logs', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Audit Logs', level: 1 })).toBeVisible()
  const tableRegion = page.getByRole('region', { name: /Audit log events with timestamp/ })
  await expect(tableRegion).toBeVisible()
  await expect(tableRegion.getByText(/USER_LOGIN_SUCCEEDED|User Login Succeeded/).first()).toBeVisible()

  await page.getByRole('combobox', { name: 'Event type' }).click()
  await page.getByRole('option', { name: 'User Login Succeeded' }).click()
  await expect(tableRegion.getByText('User Login Succeeded').first()).toBeVisible()

  const firstView = tableRegion.getByRole('button', { name: /^View audit event \d+ details$/ }).first()
  await firstView.click()
  const detail = page.getByRole('dialog', { name: /Audit event #\d+/ })
  await expect(detail).toBeVisible()
  await expect(detail.getByText('Entry hash', { exact: true })).toBeVisible()
  await detail.getByRole('button', { name: 'Close audit event details' }).click()
  await expect(detail).toBeHidden()

  const csvDownload = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Export CSV' }).click()
  const csv = await csvDownload
  expect(csv.suggestedFilename()).toMatch(/^yasmina-audit-logs-.*\.csv$/)

  const xlsxDownload = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Export XLSX' }).click()
  const xlsx = await xlsxDownload
  expect(xlsx.suggestedFilename()).toMatch(/^yasmina-audit-logs-.*\.xlsx$/)
})
