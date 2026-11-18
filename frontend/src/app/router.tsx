import * as React from 'react'
import { createBrowserRouter } from 'react-router-dom'

import { AppShell, RequireAdmin, RequireAuth, RequireRoles } from '@/app/app-shell'
import { CatalogAdminPage } from '@/features/admin/catalog-admin-page'
import { DashboardPage } from '@/features/dashboard/dashboard-page'
import { ForgotPasswordPage } from '@/features/auth/forgot-password-page'
import { LoginPage } from '@/features/auth/login-page'
import { ResetPasswordPage } from '@/features/auth/reset-password-page'
import { InvoicesPage } from '@/features/invoices/invoices-page'
import { NotFoundPage } from '@/features/not-found-page'
import { OffersPage } from '@/features/offers/offers-page'
import { OutboxPage } from '@/features/outbox/outbox-page'
import { SettingsPage } from '@/features/settings/settings-page'
import { TendersPage } from '@/features/tenders/tenders-page'
import { UsersPage } from '@/features/admin/users-page'
import { WorkersPage } from '@/features/workers/workers-page'

const SuppliersPage = React.lazy(() =>
  import('@/features/master-data/suppliers-page').then((module) => ({
    default: module.SuppliersPage,
  })),
)
const CustomersPage = React.lazy(() =>
  import('@/features/master-data/customers-page').then((module) => ({
    default: module.CustomersPage,
  })),
)
const ProcurementPage = React.lazy(() =>
  import('@/features/procurement/procurement-page').then((module) => ({
    default: module.ProcurementPage,
  })),
)
const ReportingPage = React.lazy(() =>
  import('@/features/reporting/reporting-page').then((module) => ({
    default: module.ReportingPage,
  })),
)
const AuditPage = React.lazy(() =>
  import('@/features/audit/audit-page').then((module) => ({
    default: module.AuditPage,
  })),
)

function LazyPage({ children }: { children: React.ReactNode }) {
  return <React.Suspense fallback={<div className="p-6 text-sm text-muted-foreground">Loading…</div>}>{children}</React.Suspense>
}

export const router = createBrowserRouter([
  {
    path: '/login',
    element: <LoginPage />,
  },
  {
    path: '/forgot-password',
    element: <ForgotPasswordPage />,
  },
  {
    path: '/reset-password',
    element: <ResetPasswordPage />,
  },
  {
    path: '/',
    element: <RequireAuth />,
    children: [
      {
        element: <AppShell />,
        children: [
          { index: true, element: <DashboardPage /> },
          { path: 'tenders', element: <TendersPage /> },
          { path: 'offers', element: <OffersPage /> },
          {
            path: 'procurement',
            element: (
              <LazyPage>
                <ProcurementPage />
              </LazyPage>
            ),
          },
          {
            path: 'reporting',
            element: (
              <LazyPage>
                <ReportingPage />
              </LazyPage>
            ),
          },
          {
            path: 'audit',
            element: (
              <RequireAdmin>
                <LazyPage>
                  <AuditPage />
                </LazyPage>
              </RequireAdmin>
            ),
          },
          { path: 'invoices', element: <InvoicesPage /> },
          { path: 'settings', element: <SettingsPage /> },
          {
            path: 'ops/outbox',
            element: (
              <RequireRoles roles={['admin', 'approver', 'viewer']}>
                <OutboxPage />
              </RequireRoles>
            ),
          },
          {
            path: 'ops/workers',
            element: (
              <RequireAdmin>
                <WorkersPage />
              </RequireAdmin>
            ),
          },
          {
            path: 'master-data/suppliers',
            element: (
              <RequireAdmin>
                <LazyPage>
                  <SuppliersPage />
                </LazyPage>
              </RequireAdmin>
            ),
          },
          {
            path: 'master-data/customers',
            element: (
              <RequireAdmin>
                <LazyPage>
                  <CustomersPage />
                </LazyPage>
              </RequireAdmin>
            ),
          },
          {
            path: 'admin/catalog',
            element: (
              <RequireAdmin>
                <CatalogAdminPage />
              </RequireAdmin>
            ),
          },
          {
            path: 'admin/users',
            element: (
              <RequireAdmin>
                <UsersPage />
              </RequireAdmin>
            ),
          },
        ],
      },
    ],
  },
  {
    path: '*',
    element: <NotFoundPage />,
  },
])
