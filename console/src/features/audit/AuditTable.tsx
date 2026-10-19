import { useTranslation } from 'react-i18next'
import { Badge } from '@/components/ui/badge'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { useLocale } from '@/i18n/use-locale'
import { DetailCell } from './DetailCell'
import { formatTimestampParts } from './format'
import { HashCell } from './HashCell'
import { showInvisibles } from './safe-text'
import { TruncatedId } from './TruncatedId'
import type { AuditEvent } from './types'

const KNOWN_ACTOR_TYPES = ['STAFF', 'API_CLIENT', 'SYSTEM', 'PLATFORM_OPERATOR'] as const
type KnownActorType = (typeof KNOWN_ACTOR_TYPES)[number]

/** The API types `actor_type` as a plain string, so a value this console has not seen is possible. */
function isKnownActorType(value: string): value is KnownActorType {
  return (KNOWN_ACTOR_TYPES as readonly string[]).includes(value)
}

interface AuditTableProps {
  events: readonly AuditEvent[]
}

/**
 * The audit events, newest first. Every value from the trail goes through `showInvisibles`
 * and is printed as text; none of it is ever interpreted as markup.
 */
export function AuditTable({ events }: AuditTableProps) {
  const { t } = useTranslation()
  const locale = useLocale()

  return (
    <Table>
      <caption className="sr-only">{t('audit.table.caption')}</caption>
      <TableHeader>
        <TableRow>
          <TableHead>{t('audit.table.seq')}</TableHead>
          <TableHead>{t('audit.table.at')}</TableHead>
          <TableHead className="whitespace-normal">{t('audit.table.actorType')}</TableHead>
          <TableHead className="whitespace-normal">{t('audit.table.actorId')}</TableHead>
          <TableHead>{t('audit.table.action')}</TableHead>
          <TableHead>{t('audit.table.entity')}</TableHead>
          <TableHead>{t('audit.table.detail')}</TableHead>
          <TableHead className="whitespace-normal">{t('audit.table.hashes')}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody className="[&_td]:align-top">
        {events.map((event) => {
          const when = formatTimestampParts(event.at, locale)
          return (
            <TableRow key={event.id}>
              <TableCell className="tabular font-mono text-xs">{event.seq}</TableCell>
              <TableCell className="tabular">
                {/* The exact instant, as the API recorded it, is the hover text. */}
                <time dateTime={event.at} title={event.at} className="block">
                  <span className="block">{when.date}</span>
                  <span className="block text-xs text-muted-foreground">{when.time}</span>
                </time>
              </TableCell>
              <TableCell>
                <Badge variant="muted">
                  {isKnownActorType(event.actor_type)
                    ? t(`audit.actorType.${event.actor_type}`)
                    : showInvisibles(event.actor_type)}
                </Badge>
              </TableCell>
              <TableCell>
                <TruncatedId value={showInvisibles(event.actor_id)} />
              </TableCell>
              <TableCell className="font-mono text-xs">{showInvisibles(event.action)}</TableCell>
              <TableCell>
                <EntityCell type={event.entity_type} id={event.entity_id} />
              </TableCell>
              <TableCell className="whitespace-normal">
                <DetailCell event={event} />
              </TableCell>
              <TableCell>
                <HashesCell event={event} />
              </TableCell>
            </TableRow>
          )
        })}
      </TableBody>
    </Table>
  )
}

function EntityCell({ type, id }: { type: string | null; id: string | null }) {
  const { t } = useTranslation()
  if (type === null && id === null) {
    return (
      <span className="text-muted-foreground">
        <span aria-hidden>—</span>
        <span className="sr-only">{t('audit.entity.none')}</span>
      </span>
    )
  }
  return (
    <div className="font-mono text-xs">
      {type !== null && <div>{showInvisibles(type)}</div>}
      {id !== null && <TruncatedId value={showInvisibles(id)} className="text-muted-foreground" />}
    </div>
  )
}

/** The hash this event links back to, and its own: a pair, read down the chain. */
function HashesCell({ event }: { event: AuditEvent }) {
  const { t } = useTranslation()
  return (
    <dl className="grid grid-cols-[auto_auto] items-center justify-start gap-x-2 gap-y-0.5 text-xs">
      <dt className="text-muted-foreground">{t('audit.hash.prevLabel')}</dt>
      <dd>
        <HashCell hash={event.prev_hash} copyLabel={t('audit.hash.copyPrev', { seq: event.seq })} />
      </dd>
      <dt className="text-muted-foreground">{t('audit.hash.eventLabel')}</dt>
      <dd>
        <HashCell hash={event.hash} copyLabel={t('audit.hash.copyHash', { seq: event.seq })} />
      </dd>
    </dl>
  )
}
