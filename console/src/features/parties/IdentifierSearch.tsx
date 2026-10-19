import { Search, X } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import type { IdentifierType } from '@/api/types'
import { Field } from '@/components/common/Field'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { IDENTIFIER_TYPES } from './form'

export interface IdentifierQuery {
  id_type: IdentifierType
  value: string
}

interface IdentifierSearchProps {
  /** Called once per submit with what to look for, or with `null` when the search is cleared. */
  onSearch: (query: IdentifierQuery | null) => void
  /** True while a search is applied, to offer clearing it. */
  active: boolean
}

/**
 * Exact search by identifier, which the API answers through its blind index. Nothing is sent
 * while the user types: the identifier is personal data, so it leaves the page only when the
 * user asks, once.
 */
export function IdentifierSearch({ onSearch, active }: IdentifierSearchProps) {
  const { t } = useTranslation()
  const [idType, setIdType] = useState<IdentifierType>('RFC')
  const [value, setValue] = useState('')

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const trimmed = value.trim()
    onSearch(trimmed === '' ? null : { id_type: idType, value: trimmed })
  }

  function clear() {
    setValue('')
    onSearch(null)
  }

  return (
    <form role="search" onSubmit={submit} noValidate className="flex flex-wrap items-end gap-3">
      <div className="w-56">
        <Field label={t('parties.search.idType')}>
          {(control) => (
            <Select value={idType} onValueChange={(next) => setIdType(next as IdentifierType)}>
              <SelectTrigger {...control} className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {IDENTIFIER_TYPES.map((type) => (
                  <SelectItem key={type} value={type}>
                    {t(`parties.identifierType.${type}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </Field>
      </div>
      <div className="w-64">
        <Field label={t('parties.search.value')}>
          {(control) => (
            <Input
              {...control}
              value={value}
              onChange={(event) => setValue(event.target.value)}
              autoComplete="off"
              autoCapitalize="characters"
              spellCheck={false}
              maxLength={64}
              className="font-mono"
            />
          )}
        </Field>
      </div>
      <Button type="submit">
        <Search aria-hidden />
        {t('parties.search.submit')}
      </Button>
      {active && (
        <Button type="button" variant="outline" onClick={clear}>
          <X aria-hidden />
          {t('parties.search.clear')}
        </Button>
      )}
    </form>
  )
}
