import { useTranslation } from 'react-i18next'
import type { ConnectorResponse } from '@/api/types'
import { DataTable, type Column } from '@/components/common/DataTable'
import { Badge } from '@/components/ui/badge'
import { CircuitBadge, ModeBadge } from './badges'

interface ConnectorsTableProps {
  rows: readonly ConnectorResponse[] | undefined
  loading: boolean
}

/** One row per connector: what it checks, its mode, its routes, its circuit and its credentials. */
export function ConnectorsTable({ rows, loading }: ConnectorsTableProps) {
  const { t } = useTranslation()

  const columns: Column<ConnectorResponse>[] = [
    {
      id: 'name',
      header: t('connectors.table.name'),
      cell: (connector) => <span className="font-mono text-xs">{connector.name}</span>,
    },
    {
      id: 'checkTypes',
      header: t('connectors.table.checkTypes'),
      cell: (connector) => (
        <ul className="flex flex-wrap gap-1">
          {connector.check_types.map((checkType) => (
            <li key={checkType}>
              <Badge variant="mono">{t(`connectors.checkType.${checkType}`)}</Badge>
            </li>
          ))}
        </ul>
      ),
      className: 'whitespace-normal',
    },
    {
      id: 'mode',
      header: t('connectors.table.mode'),
      cell: (connector) => <ModeBadge mode={connector.mode} />,
    },
    {
      id: 'routes',
      header: t('connectors.table.routes'),
      cell: (connector) => (
        <ul className="space-y-0.5 font-mono text-xs">
          {connector.routes.map((route) => (
            <li key={`${route.check_type}-${route.country}-${route.position}`}>
              {`${route.check_type} · ${route.country} · ${t(`connectors.route.${route.position}`)}`}
            </li>
          ))}
        </ul>
      ),
    },
    {
      id: 'circuit',
      header: t('connectors.table.circuit'),
      cell: (connector) => <CircuitBadge state={connector.circuit_state} />,
    },
    {
      id: 'credentials',
      header: t('connectors.table.credentials'),
      cell: (connector) =>
        connector.credential_modes.length === 0 ? (
          <span className="text-muted-foreground">{t('connectors.credentials.none')}</span>
        ) : (
          t('connectors.credentials.modes', { modes: connector.credential_modes.join(', ') })
        ),
    },
  ]

  return (
    <DataTable
      label={t('connectors.table.label')}
      columns={columns}
      rows={rows}
      getRowId={(connector) => connector.name}
      loading={loading}
      empty={t('connectors.empty')}
    />
  )
}
