import { RouterProvider } from 'react-router-dom'
import { Toaster } from 'sonner'

import { AppProviders } from '@/app/providers'
import { router } from '@/app/router'

export default function App() {
  return (
    <AppProviders>
      <RouterProvider router={router} />
      <Toaster position="top-right" richColors closeButton />
    </AppProviders>
  )
}
