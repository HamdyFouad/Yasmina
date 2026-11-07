import { createBrowserRouter } from 'react-router-dom'

import { AppShell, RequireAuth } from '@/app/app-shell'
import { DashboardPage } from '@/features/dashboard/dashboard-page'
import { LoginPage } from '@/features/auth/login-page'
import { InvoicesPage } from '@/features/invoices/invoices-page'
import { NotFoundPage } from '@/features/not-found-page'
import { OffersPage } from '@/features/offers/offers-page'
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
        ],
      },
    ],
  },
  {
    path: '*',
    element: <NotFoundPage />,
  },
])
