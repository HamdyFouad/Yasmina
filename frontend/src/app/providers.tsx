import * as React from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import { AuthProvider } from '@/features/auth/auth-context'
import { isApiError } from '@/lib/api-error'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: (failureCount, error) => {
        if (isApiError(error)) {
          const status = error.status
          if (status === 401 || status === 403 || status === 429 || status === 404) {
            return false
          }
        }
        return failureCount < 1
      },
    },
    mutations: {
      retry: false,
    },
  },
})

export function AppProviders({ children }: { children: React.ReactNode }) {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>{children}</AuthProvider>
    </QueryClientProvider>
  )
}
