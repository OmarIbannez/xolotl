import { createBrowserRouter, RouterProvider } from 'react-router-dom'
import { ACCESS } from '@/auth/access'
import { RequireAuth, RequireRole } from '@/auth/guards'
import { FullPageLoading } from '@/components/common/FullPageLoading'
import { AppShell } from '@/components/layout/AppShell'
import { LoginPage } from '@/features/auth/LoginPage'
import { SetupPasswordPage } from '@/features/auth/SetupPasswordPage'
import { NotFound } from './NotFound'

// Each signed-in page is its own chunk. The root route's HydrateFallback is what shows
// while the first page's chunk loads.
const router = createBrowserRouter([
  {
    HydrateFallback: FullPageLoading,
    children: [
      { path: '/login', element: <LoginPage /> },
      {
        // Reachable only while the account must change its password.
        element: <RequireAuth allowPasswordChange />,
        children: [{ path: '/setup-password', element: <SetupPasswordPage /> }],
      },
      {
        element: <RequireAuth />,
        children: [
          {
            element: <AppShell />,
            children: [
              {
                path: '/',
                lazy: () =>
                  import('@/features/home/HomePage').then((m) => ({ Component: m.HomePage })),
              },
              {
                element: <RequireRole roles={ACCESS.apiKeys} />,
                children: [
                  {
                    path: '/api-keys',
                    lazy: () =>
                      import('@/features/api-keys/ApiKeysPage').then((m) => ({
                        Component: m.ApiKeysPage,
                      })),
                  },
                ],
              },
              {
                element: <RequireRole roles={ACCESS.staff} />,
                children: [
                  {
                    path: '/staff',
                    lazy: () =>
                      import('@/features/staff/StaffPage').then((m) => ({
                        Component: m.StaffPage,
                      })),
                  },
                ],
              },
              {
                element: <RequireRole roles={ACCESS.audit} />,
                children: [
                  {
                    path: '/audit',
                    lazy: () =>
                      import('@/features/audit/AuditPage').then((m) => ({
                        Component: m.AuditPage,
                      })),
                  },
                ],
              },
              {
                // Every signed-in role reads the policy, as the API allows.
                path: '/policy',
                lazy: () =>
                  import('@/features/policy/PolicyPage').then((m) => ({ Component: m.PolicyPage })),
              },
              {
                // Unit values and the calendar are reference data every signed-in role reads.
                path: '/units',
                lazy: () =>
                  import('@/features/units/UnitsPage').then((m) => ({ Component: m.UnitsPage })),
              },
              {
                path: '/calendar',
                lazy: () =>
                  import('@/features/calendar/CalendarPage').then((m) => ({ Component: m.CalendarPage })),
              },
              {
                element: <RequireRole roles={ACCESS.connectors} />,
                children: [
                  {
                    path: '/connectors',
                    lazy: () =>
                      import('@/features/connectors/ConnectorsPage').then((m) => ({
                        Component: m.ConnectorsPage,
                      })),
                  },
                ],
              },
              {
                path: '/security',
                lazy: () =>
                  import('@/features/security/SecurityPage').then((m) => ({
                    Component: m.SecurityPage,
                  })),
              },
              { path: '*', element: <NotFound /> },
            ],
          },
        ],
      },
    ],
  },
])

export function App() {
  return <RouterProvider router={router} />
}
