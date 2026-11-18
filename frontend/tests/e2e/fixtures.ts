import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { expect } from '@playwright/test'
import type { Page } from '@playwright/test'

export interface Credentials {
  username: string
  password: string
}

export interface Fixtures {
  approver: Credentials
  admin: Credentials
  sales: Credentials
  offer_number: string
  outbox_offer_number: string
  customer_name: string
  buyer_reference: string
  invoice_net: string
  invoice_gross: string
  tender_number: string
  quarantine_tender_number: string
  supplier_name: string
  master_customer_name: string
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

export const fixtures = loadFixtures()

export async function login(page: Page, credentials: Credentials): Promise<void> {
  await page.goto('/login')
  await page.getByLabel('Username').fill(credentials.username)
  await page.getByLabel('Password').fill(credentials.password)
  await page.getByRole('button', { name: /sign in/i }).click()
  await expect(page.getByRole('link', { name: 'Offers', exact: true })).toBeVisible()
}
