import { expect, test } from '@playwright/test'

import { fixtures, login } from './fixtures'

test('admin lists users, creates an account and locks it', async ({ page }) => {
  await login(page, fixtures.admin)

  await page.getByRole('link', { name: 'Users', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Users', level: 1 })).toBeVisible()

  const table = page.getByRole('region', { name: /User accounts with roles/ })
  await expect(table).toBeVisible()
  await expect(
    table.getByRole('row', { name: new RegExp(fixtures.admin.username) }),
  ).toHaveCount(1)
  await expect(
    table.getByRole('row', { name: new RegExp(fixtures.approver.username) }),
  ).toHaveCount(1)

  const username = `e2e-ui-${Date.now().toString(36)}`
  const password = 'E2E-Created-2026!'

  await page.getByRole('button', { name: 'Create user' }).click()
  const createDialog = page.getByRole('dialog')
  await expect(createDialog.getByRole('heading', { name: 'Create user' })).toBeVisible()

  await createDialog.getByLabel('Username').fill(username)
  await createDialog.getByLabel(/E-mail/).fill(`${username}@example.test`)
  await createDialog.getByLabel('Initial password').fill(password)
  await createDialog.getByLabel('Sales').check()
  await createDialog.getByRole('button', { name: 'Create user' }).click()

  await expect(createDialog).toBeHidden()
  await expect(page.getByText('User created', { exact: true })).toBeVisible()

  const createdRow = table.getByRole('row', { name: new RegExp(username) })
  await expect(createdRow).toHaveCount(1)
  await expect(createdRow.getByText('Sales', { exact: true })).toBeVisible()
  await expect(createdRow.getByText('Active', { exact: true })).toBeVisible()

  // Locking is destructive, so the confirmation dialog focuses Cancel first.
  await createdRow.getByRole('button', { name: `Lock account ${username}` }).click()
  const lockDialog = page.getByRole('dialog', { name: `Lock ${username}?` })
  await expect(lockDialog).toBeVisible()
  await expect(lockDialog.getByRole('button', { name: 'Cancel' })).toBeFocused()
  await lockDialog.getByRole('button', { name: 'Lock account' }).click()

  await expect(page.getByText('Account locked', { exact: true })).toBeVisible()
  await expect(createdRow.getByText('Locked', { exact: true })).toBeVisible()
  await expect(
    createdRow.getByRole('button', { name: `Unlock account ${username}` }),
  ).toBeVisible()
})

test('approvers cannot open user administration', async ({ browser }) => {
  const context = await browser.newContext()
  const page = await context.newPage()
  try {
    await login(page, fixtures.approver)
    await expect(page.getByRole('link', { name: 'Users', exact: true })).toHaveCount(0)

    await page.goto('/admin/users')
    await expect(page).toHaveURL(/\/$/)
    await expect(page.getByRole('heading', { name: 'Overview', level: 1 })).toBeVisible()
  } finally {
    await context.close()
  }
})
