import { createBrowserRouter } from 'react-router-dom'

import { AppShell, RequireAdmin, RequireAuth, RequireRoles } from '@/app/app-shell'
import { CatalogAdminPage } from '@/features/admin/catalog-admin-page'
import { DashboardPage } from '@/features/dashboard/dashboard-page'
import { LoginPage } from '@/features/auth/login-page'
import { InvoicesPage } from '@/features/invoices/invoices-page'
import { NotFoundPage } from '@/features/not-found-page'
import { OffersPage } from '@/features/offers/offers-page'
import { OutboxPage } from '@/features/outbox/outbox-page'
import { TendersPage } from '@/features/tenders/tenders-page'

export const router = createBrowserRouter([
  {
    path: '/login',
    element: <LoginPage />,
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
          { path: 'invoices', element: <InvoicesPage /> },
          {
            path: 'ops/outbox',
            element: (
              <RequireRoles roles={['admin', 'approver', 'viewer']}>
                <OutboxPage />
              </RequireRoles>
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
        ],
      },
    ],
  },
  {
    path: '*',
    element: <NotFoundPage />,
  },
])
