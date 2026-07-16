import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Search, RefreshCw } from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext'
import api from '../../lib/api'
import { Card, CardHeader } from '../../components/ui/Card'
import Spinner from '../../components/ui/Spinner'
import EmptyState from '../../components/ui/EmptyState'
import { cn } from '../../lib/utils'

const TYPE_COLORS: Record<string, string> = {
  asset: 'bg-blue-100 text-blue-700',
  liability: 'bg-red-100 text-red-700',
  equity: 'bg-purple-100 text-purple-700',
  revenue: 'bg-green-100 text-green-700',
  cogs: 'bg-orange-100 text-orange-700',
  expense: 'bg-yellow-100 text-yellow-700',
  other_income: 'bg-emerald-100 text-emerald-700',
  other_expense: 'bg-pink-100 text-pink-700',
  tax_expense: 'bg-gray-100 text-gray-700',
}

export default function COAPage() {
  const { entityId } = useAuth()
  const { t } = useTranslation(['coa', 'common'])
  const [search, setSearch] = useState('')
  const [typeFilter, setTypeFilter] = useState('')

  const TYPE_FILTER = [
    { value: '', label: t('coa:typeAll') },
    { value: 'asset', label: t('coa:typeAsset') },
    { value: 'liability', label: t('coa:typeLiability') },
    { value: 'equity', label: t('coa:typeEquity') },
    { value: 'revenue', label: t('coa:typeRevenue') },
    { value: 'cogs', label: t('coa:typeCogs') },
    { value: 'expense', label: t('coa:typeExpense') },
  ]

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['coa', entityId],
    queryFn: () =>
      api.get(`/coa/?entity_id=${entityId}&limit=500`).then((r) => r.data),
    enabled: !!entityId,
  })

  const accounts: any[] = Array.isArray(data) ? data : (data?.accounts ?? [])

  const filtered = accounts.filter((a) => {
    const matchSearch =
      !search ||
      a.account_code?.toLowerCase().includes(search.toLowerCase()) ||
      a.account_name?.toLowerCase().includes(search.toLowerCase())
    const matchType = !typeFilter || a.account_type === typeFilter
    return matchSearch && matchType
  })

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">{t('coa:title')}</h1>
        <p className="text-sm text-gray-500 mt-0.5">{t('coa:subtitle')}</p>
      </div>

      <Card noPad>
        <CardHeader
          title={t('coa:listTitle')}
          subtitle={`${filtered.length} ${t('coa:accountUnit')}`}
          actions={
            <button onClick={() => refetch()} className="btn-secondary">
              <RefreshCw className="h-4 w-4" />
            </button>
          }
        />

        {/* Filters */}
        <div className="px-6 py-3 border-b border-gray-100 flex items-center gap-3 flex-wrap">
          <div className="relative flex-1 min-w-48">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={t('coa:searchPlaceholder')}
              className="form-input pl-9"
            />
          </div>
          <select
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value)}
            className="form-select w-44"
          >
            {TYPE_FILTER.map((opt) => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
        </div>

        {isLoading ? (
          <div className="flex justify-center py-16"><Spinner size="lg" /></div>
        ) : filtered.length === 0 ? (
          <EmptyState title={t('coa:emptyTitle')} description={t('coa:emptyDescription')} />
        ) : (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>{t('coa:colAccountCode')}</th>
                  <th>{t('coa:colAccountName')}</th>
                  <th>{t('coa:colType')}</th>
                  <th>{t('coa:colNormalBalance')}</th>
                  <th className="text-center">{t('coa:colHeader')}</th>
                  <th className="text-center">{t('coa:colActive')}</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((a) => (
                  <tr key={a.id ?? a.account_code}
                    className={a.is_header ? 'bg-gray-50 font-semibold' : ''}>
                    <td className="font-mono text-sm">{a.account_code}</td>
                    <td className={cn('text-sm', a.is_header ? '' : 'pl-4')}>
                      {a.account_name}
                    </td>
                    <td>
                      <span className={cn(
                        'inline-flex px-2 py-0.5 rounded-full text-xs font-medium capitalize',
                        TYPE_COLORS[a.account_type] ?? 'bg-gray-100 text-gray-600',
                      )}>
                        {a.account_type}
                      </span>
                    </td>
                    <td className="text-sm capitalize text-gray-500">{a.normal_balance}</td>
                    <td className="text-center">
                      {a.is_header && <span className="text-xs text-primary-600 font-medium">{t('common:yes')}</span>}
                    </td>
                    <td className="text-center">
                      <span className={cn(
                        'inline-block h-2 w-2 rounded-full',
                        a.is_active !== false ? 'bg-green-400' : 'bg-gray-300',
                      )} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}
