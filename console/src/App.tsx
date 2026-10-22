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
                element: <RequireRole roles={ACCESS.parties} />,
                children: [
                  {
                    path: '/parties',
                    lazy: () =>
                      import('@/features/parties/PartiesPage').then((m) => ({ Component: m.PartiesPage })),
                  },
                  {
                    path: '/parties/new',
                    lazy: () =>
                      import('@/features/parties/CreatePartyPage').then((m) => ({
                        Component: m.CreatePartyPage,
                      })),
                  },
                  {
                    path: '/parties/:partyId',
                    lazy: () =>
                      import('@/features/parties/PartyPage').then((m) => ({ Component: m.PartyPage })),
                  },
                ],
              },
              {
                element: <RequireRole roles={ACCESS.transactions} />,
                children: [
                  {
                    path: '/transactions',
                    lazy: () =>
                      import('@/features/transactions/TransactionsPage').then((m) => ({
                        Component: m.TransactionsPage,
                      })),
                  },
                  {
                    path: '/transactions/:transactionId',
                    lazy: () =>
                      import('@/features/transactions/TransactionPage').then((m) => ({
                        Component: m.TransactionPage,
                      })),
                  },
                ],
              },
              {
                element: <RequireRole roles={ACCESS.monitoring} />,
                children: [
                  {
                    path: '/monitoring/hits',
                    lazy: () => import('@/features/hits/HitsPage').then((m) => ({ Component: m.HitsPage })),
                  },
                  {
                    path: '/monitoring/hits/:hitId',
                    lazy: () => import('@/features/hits/HitPage').then((m) => ({ Component: m.HitPage })),
                  },
                ],
              },
              {
                element: <RequireRole roles={ACCESS.rules} />,
                children: [
                  {
                    path: '/rules/monitoring',
                    lazy: () => import('@/features/rules/RulesPage').then((m) => ({ Component: m.RulesPage })),
                  },
                  {
                    path: '/rules/monitoring/:ruleId',
                    lazy: () => import('@/features/rules/RulePage').then((m) => ({ Component: m.RulePage })),
                  },
                ],
              },
              {
                element: <RequireRole roles={ACCESS.screening} />,
                children: [
                  {
                    path: '/screening/matches',
                    lazy: () =>
                      import('@/features/screening/MatchesPage').then((m) => ({ Component: m.MatchesPage })),
                  },
                  {
                    path: '/screening/matches/:matchId',
                    lazy: () =>
                      import('@/features/screening/MatchPage').then((m) => ({ Component: m.MatchPage })),
                  },
                  {
                    path: '/screening/evaluation',
                    lazy: () =>
                      import('@/features/screening/EvaluationPage').then((m) => ({ Component: m.EvaluationPage })),
                  },
                  {
                    path: '/counterparties',
                    lazy: () =>
                      import('@/features/counterparties/CounterpartiesPage').then((m) => ({
                        Component: m.CounterpartiesPage,
                      })),
                  },
                  {
                    path: '/counterparties/:counterpartyId',
                    lazy: () =>
                      import('@/features/counterparties/CounterpartyPage').then((m) => ({
                        Component: m.CounterpartyPage,
                      })),
                  },
                ],
              },
              {
                element: <RequireRole roles={ACCESS.screeningCheck} />,
                children: [
                  {
                    path: '/screening/check',
                    lazy: () =>
                      import('@/features/screening/CheckPage').then((m) => ({ Component: m.CheckPage })),
                  },
                ],
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
                element: <RequireRole roles={ACCESS.lists} />,
                children: [
                  {
                    path: '/lists',
                    lazy: () =>
                      import('@/features/lists/ListsPage').then((m) => ({ Component: m.ListsPage })),
                  },
                  {
                    path: '/lists/:code',
                    lazy: () =>
                      import('@/features/lists/ListVersionsPage').then((m) => ({
                        Component: m.ListVersionsPage,
                      })),
                  },
                ],
              },
              {
                element: <RequireRole roles={ACCESS.privateLists} />,
                children: [
                  {
                    path: '/private-lists',
                    lazy: () =>
                      import('@/features/private-lists/PrivateListsPage').then((m) => ({
                        Component: m.PrivateListsPage,
                      })),
                  },
                  {
                    path: '/private-lists/:listId',
                    lazy: () =>
                      import('@/features/private-lists/PrivateListPage').then((m) => ({
                        Component: m.PrivateListPage,
                      })),
                  },
                ],
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
                element: <RequireRole roles={ACCESS.rails} />,
                children: [
                  {
                    path: '/admin/rails',
                    lazy: () =>
                      import('@/features/rails/RailsPage').then((m) => ({ Component: m.RailsPage })),
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
