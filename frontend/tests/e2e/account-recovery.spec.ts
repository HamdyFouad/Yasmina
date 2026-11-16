import { expect, test } from '@playwright/test'

import { fixtures, login } from './fixtures'

test('forgot-password request and reset-token flow', async ({ page, request }) => {
  const username = `e2e-reset-${Date.now().toString(36)}`
  const initialPassword = 'E2E-Reset-Initial-2026!'
  const newPassword = 'E2E-Reset-Changed-2026!'

  const adminLogin = await request.post('/api/v1/auth/token', {
    data: fixtures.admin,
  })
  expect(adminLogin.status()).toBe(200)
  const adminToken = (await adminLogin.json()).access_token as string
  const authorization = { Authorization: `Bearer ${adminToken}` }

  const created = await request.post('/api/v1/auth/users', {
    headers: authorization,
    data: {
      username,
      password: initialPassword,
      email: `${username}@musterstadt.de`,
      roles: ['viewer'],
    },
  })
  expect(created.status()).toBe(201)

  // The public request always returns the same confirmation, regardless of
  // whether the account exists or delivery is configured.
  await page.goto('/forgot-password')
  await page.getByLabel('Username').fill(username)
  await page.getByRole('button', { name: 'Send reset instructions' }).click()
  await expect(page.getByRole('status')).toContainText('If that account exists')

  // In CI no SMTP/Telegram relay is configured, so the test simulates the
  // operator-delivered token through the admin-only reset endpoint.
  const issued = await request.post(
    `/api/v1/auth/users/${encodeURIComponent(username)}/password/reset`,
    { headers: authorization },
  )
  expect(issued.status()).toBe(201)
  const resetToken = (await issued.json()).reset_token as string

  await page.goto(`/reset-password?token=${encodeURIComponent(resetToken)}`)
  await page.getByLabel('New password', { exact: true }).fill(newPassword)
  await page.getByLabel('Confirm new password', { exact: true }).fill(newPassword)
  await page.getByRole('button', { name: 'Reset password' }).click()
  await expect(page.getByRole('status')).toContainText('Your password was reset')
  await page.getByRole('link', { name: 'Sign in with your new password' }).click()
  await expect(page).toHaveURL(/\/login$/)

  await login(page, { username, password: newPassword })
})
