import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Search, RefreshCw, Package, TrendingDown } from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext'
import api from '../../lib/api'
import { Card, CardHeader } from '../../components/ui/Card'
import Spinner from '../../components/ui/Spinner'
import EmptyState from '../../components/ui/EmptyState'
import Badge from '../../components/ui/Badge'
import { formatRupiah, formatDate } from '../../lib/utils'

export default function AssetListPage() {
  const { t } = useTranslation(['assets', 'common'])
  const { entityId } = useAuth()
  const DEP_METHOD_LABELS: Record<string, string> = {
    straight_line:       t('assets:depMethod_straightLine'),
    double_declining:    t('assets:depMethod_doubleDeclining'),
    sum_of_years:        t('assets:depMethod_sumOfYears'),
    units_of_production: t('assets:depMethod_unitsOfProduction'),
  }
  const [search, setSearch] = useState('')
  const [categoryFilter, setCategoryFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('')

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['assets', entityId],
    queryFn: () =>
      api.get('/assets', { params: { entity_id: entityId, size: 500 } }).then((r) => r.data),
    enabled: !!entityId,
  })

  const assets: any[] = Array.isArray(data) ? data : (data?.assets ?? data?.items ?? [])

  const categories = [...new Set(assets.map((a) => a.category).filter(Boolean))]

  const filtered = assets.filter((a) => {
    const matchSearch =
      !search ||
      a.asset_code?.toLowerCase().includes(search.toLowerCase()) ||
      a.asset_name?.toLowerCase().includes(search.toLowerCase())
    const matchCat = !categoryFilter || a.category === categoryFilter
    const matchStatus = !statusFilter || a.status === statusFilter
    return matchSearch && matchCat && matchStatus
  })

  // Stats
  const totalCost  = assets.reduce((s, a) => s + (a.acquisition_cost ?? 0), 0)
  const totalAccum = assets.reduce((s, a) => s + (a.accumulated_depreciation ?? 0), 0)
  const totalBook  = assets.reduce((s, a) => s + (a.book_value ?? (a.acquisition_cost - (a.accumulated_depreciation ?? 0))), 0)

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">{t('assets:assetList_title')}</h1>
          <p className="text-sm text-gray-500 mt-0.5">{t('assets:assetList_subtitle')}</p>
        </div>
        <button className="btn-primary">
          <Package className="h-4 w-4" /> {t('assets:assetList_addAsset')}
        </button>
      </div>

      {/* Summary */}
      <div className="grid grid-cols-3 gap-4">
        <div className="card p-4">
          <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">{t('assets:assetList_totalAssets')}</p>
          <p className="text-2xl font-bold text-gray-900 mt-1">{assets.length}</p>
          <p className="text-xs text-gray-400 mt-0.5">{t('assets:assetList_acquisitionCost')}: Rp {formatRupiah(totalCost)}</p>
        </div>
        <div className="card p-4">
          <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">{t('assets:assetList_accumDep')}</p>
          <p className="text-xl font-bold text-red-500 mt-1">Rp {formatRupiah(totalAccum)}</p>
        </div>
        <div className="card p-4 border-l-4 border-primary-500">
          <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">{t('assets:assetList_bookValue')}</p>
          <p className="text-xl font-bold text-gray-900 mt-1">Rp {formatRupiah(totalBook)}</p>
        </div>
      </div>

      <Card noPad>
        <CardHeader
          title={t('assets:assetList_listTitle')}
          subtitle={`${filtered.length} ${t('assets:assetList_assetUnit')}`}
          actions={<button onClick={() => refetch()} className="btn-secondary"><RefreshCw className="h-4 w-4" /></button>}
        />

        {/* Filters */}
        <div className="px-6 py-3 border-b border-gray-100 flex items-center gap-3 flex-wrap">
          <div className="relative flex-1 min-w-48">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
            <input value={search} onChange={(e) => setSearch(e.target.value)}
              placeholder={t('assets:assetList_searchPlaceholder')} className="form-input pl-9" />
          </div>
          {categories.length > 0 && (
            <select value={categoryFilter} onChange={(e) => setCategoryFilter(e.target.value)}
              className="form-select w-40">
              <option value="">{t('assets:assetList_allCategories')}</option>
              {categories.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          )}
          <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}
            className="form-select w-36">
            <option value="">{t('common:allStatus')}</option>
            <option value="active">{t('assets:assetList_statusActive')}</option>
            <option value="disposed">{t('assets:assetList_statusDisposed')}</option>
            <option value="fully_depreciated">{t('assets:assetList_statusFullyDepreciated')}</option>
          </select>
        </div>

        {isLoading ? (
          <div className="flex justify-center py-16"><Spinner size="lg" /></div>
        ) : filtered.length === 0 ? (
          <EmptyState title={t('assets:assetList_emptyTitle')} />
        ) : (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>{t('common:code')}</th>
                  <th>{t('assets:assetList_colAssetName')}</th>
                  <th>{t('assets:assetList_colCategory')}</th>
                  <th>{t('assets:assetList_colAcquisitionDate')}</th>
                  <th>{t('assets:assetList_colDepMethod')}</th>
                  <th>{t('assets:assetList_colUsefulLife')}</th>
                  <th className="right">{t('assets:assetList_colAcquisitionCost')}</th>
                  <th className="right">{t('assets:assetList_colAccumDep')}</th>
                  <th className="right">{t('assets:assetList_colBookValue')}</th>
                  <th>{t('common:status')}</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((a) => {
                  const bookValue = a.book_value ?? (a.acquisition_cost - (a.accumulated_depreciation ?? 0))
                  const depPct = a.acquisition_cost > 0
                    ? (a.accumulated_depreciation ?? 0) / a.acquisition_cost * 100
                    : 0
                  return (
                    <tr key={a.id}>
                      <td className="font-mono text-xs text-gray-500">{a.asset_code}</td>
                      <td className="font-medium text-sm">{a.asset_name}</td>
                      <td className="text-sm">{a.category ?? '-'}</td>
                      <td className="text-sm text-gray-500">{formatDate(a.acquisition_date)}</td>
                      <td className="text-xs text-gray-500">
                        {DEP_METHOD_LABELS[a.depreciation_method] ?? a.depreciation_method ?? '-'}
                      </td>
                      <td className="text-sm text-gray-500 text-center">{a.useful_life_years ?? a.useful_life ?? '-'} {t('assets:assetList_yearsUnit')}</td>
                      <td className="right">{formatRupiah(a.acquisition_cost)}</td>
                      <td className="right text-red-500">
                        {formatRupiah(a.accumulated_depreciation)}
                        {depPct > 0 && (
                          <span className="ml-1 text-xs text-gray-400">({depPct.toFixed(0)}%)</span>
                        )}
                      </td>
                      <td className={`right font-semibold ${bookValue <= 0 ? 'text-gray-400' : ''}`}>
                        {formatRupiah(bookValue)}
                      </td>
                      <td>
                        <Badge
                          status={a.status ?? 'active'}
                          label={a.status === 'active' ? t('assets:assetList_statusActive') : (a.status === 'fully_depreciated' ? t('assets:assetList_statusFullyDepreciatedShort') : a.status)}
                        />
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}
