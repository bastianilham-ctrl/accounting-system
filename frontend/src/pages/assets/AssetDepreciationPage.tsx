import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { RefreshCw, Play, Plus, X } from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext'
import api from '../../lib/api'
import { Card, CardHeader } from '../../components/ui/Card'
import Spinner from '../../components/ui/Spinner'
import EmptyState from '../../components/ui/EmptyState'
import Badge from '../../components/ui/Badge'
import { formatRupiah, formatDate, currentYear, currentMonth, MONTHS, lastDayOfMonth, todayISO } from '../../lib/utils'
import { showToast } from '../../components/ui/Toast'

// Last day of given month
function lastDay(year: number, month: number) {
  return new Date(year, month, 0).toISOString().slice(0, 10)
}

export default function AssetDepreciationPage() {
  const { t } = useTranslation(['assets', 'common'])
  const { entityId } = useAuth()
  const qc = useQueryClient()
  const [tab, setTab] = useState<'depreciation' | 'prepaid' | 'fiscal'>('depreciation')
  const [year, setYear]   = useState(currentYear())
  const [month, setMonth] = useState(currentMonth())

  // ── Assets list (for schedule lookup) ───────────────────────────────────────
  const { data: assetsData } = useQuery({
    queryKey: ['assets', entityId],
    queryFn: () => api.get(`/assets/${entityId}`, { params: { size: 500 } }).then(r => r.data),
    enabled: !!entityId,
  })
  const assets: any[] = Array.isArray(assetsData) ? assetsData : (assetsData?.assets ?? assetsData?.items ?? [])

  // ── Post depreciation mutation ──────────────────────────────────────────────
  const [postResult, setPostResult] = useState<any>(null)
  const postDepMutation = useMutation({
    mutationFn: () =>
      api.post('/assets/post-depreciation', {
        entity_id:   entityId,
        period_date: lastDay(year, month),
        posted_by:   'user',
      }),
    onSuccess: (r) => {
      setPostResult(r.data)
      const posted = r.data?.posted_count ?? r.data?.assets_posted ?? 0
      showToast(`${t('assets:depreciation_toastPosted')} ${MONTHS.find(m => m.value === month)?.label} ${year}: ${posted} ${t('assets:depreciation_assetsPostedSuffix')}`)
      qc.invalidateQueries({ queryKey: ['assets'] })
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('assets:depreciation_toastPostFailed'), 'error'),
  })

  // ── Asset schedule viewer ────────────────────────────────────────────────────
  const [selectedAsset, setSelectedAsset] = useState<any>(null)
  const { data: scheduleData, isLoading: schLoading } = useQuery({
    queryKey: ['asset-schedule', selectedAsset?.id],
    queryFn: () => api.get(`/assets/${selectedAsset.id}/schedule`).then(r => r.data),
    enabled: !!selectedAsset,
  })
  const schedule: any[] = Array.isArray(scheduleData) ? scheduleData : (scheduleData?.schedule ?? [])

  // ── Prepaid expenses ─────────────────────────────────────────────────────────
  const { data: prepaidData, isLoading: ppLoading, refetch: refetchPp } = useQuery({
    queryKey: ['prepaid', entityId],
    queryFn: () => api.get(`/prepaid/${entityId}`).then(r => r.data),
    enabled: !!entityId && tab === 'prepaid',
  })
  const prepaids: any[] = Array.isArray(prepaidData) ? prepaidData : (prepaidData?.items ?? [])

  const [showPpForm, setShowPpForm] = useState(false)
  const [ppForm, setPpForm] = useState({
    description: '', start_date: todayISO(), end_date: '',
    total_amount: '', coa_prepaid: '1-5-001', coa_expense: '6-1-003',
  })
  const createPpMutation = useMutation({
    mutationFn: () =>
      api.post('/prepaid', {
        entity_id: entityId, ...ppForm,
        total_amount: parseFloat(ppForm.total_amount) || 0,
      }),
    onSuccess: () => {
      showToast(t('assets:depreciation_toastPrepaidAdded'))
      setShowPpForm(false)
      setPpForm({ description: '', start_date: todayISO(), end_date: '', total_amount: '', coa_prepaid: '1-5-001', coa_expense: '6-1-003' })
      refetchPp()
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('assets:depreciation_toastFailed'), 'error'),
  })

  const postAmortiMutation = useMutation({
    mutationFn: () =>
      api.post('/prepaid/post-amortization', {
        entity_id:   entityId,
        period_date: lastDay(year, month),
        posted_by:   'user',
      }),
    onSuccess: (r) => {
      showToast(`${t('assets:depreciation_toastAmortizationPosted')}: ${r.data?.posted_count ?? 0} ${t('assets:depreciation_itemUnit')}`)
      refetchPp()
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('assets:depreciation_toastFailed'), 'error'),
  })

  // ── Fiscal correction ────────────────────────────────────────────────────────
  const [fiscalYear, setFiscalYear] = useState(currentYear())
  const { data: fiscalData, isLoading: fiscalLoading } = useQuery({
    queryKey: ['fiscal-correction', entityId, fiscalYear],
    queryFn: () =>
      api.get(`/assets/${entityId}/fiscal-correction`, { params: { year: fiscalYear } }).then(r => r.data),
    enabled: !!entityId && tab === 'fiscal',
  })
  const fiscalRows: any[] = Array.isArray(fiscalData)
    ? fiscalData
    : (fiscalData?.assets ?? fiscalData?.items ?? [])
  const fiscalSummary = fiscalData?.summary

  const years = [currentYear(), currentYear() - 1, currentYear() - 2]

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">{t('assets:depreciation_title')}</h1>
          <p className="text-sm text-gray-500 mt-0.5">{t('assets:depreciation_subtitle')}</p>
        </div>
        <div className="flex items-center gap-2">
          <select value={month} onChange={e => setMonth(+e.target.value)} className="form-select w-36">
            {MONTHS.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
          </select>
          <select value={year} onChange={e => setYear(+e.target.value)} className="form-select w-24">
            {years.map(y => <option key={y} value={y}>{y}</option>)}
          </select>
        </div>
      </div>

      {/* Tabs */}
      <div className="border-b border-gray-200">
        <nav className="flex gap-1">
          {[
            { key: 'depreciation', label: t('assets:depreciation_tabPosting') },
            { key: 'prepaid',      label: t('assets:depreciation_tabPrepaid') },
            { key: 'fiscal',       label: t('assets:depreciation_tabFiscal') },
          ].map(tb => (
            <button key={tb.key}
              onClick={() => setTab(tb.key as any)}
              className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                tab === tb.key ? 'border-primary-600 text-primary-700' : 'border-transparent text-gray-500 hover:text-gray-700'
              }`}>
              {tb.label}
            </button>
          ))}
        </nav>
      </div>

      {/* Tab: Depreciation */}
      {tab === 'depreciation' && (
        <div className="space-y-4">
          <Card>
            <p className="text-sm font-semibold text-gray-700 mb-3">{t('assets:depreciation_postMonthlyTitle')}</p>
            <p className="text-sm text-gray-500 mb-4">
              {t('assets:depreciation_postMonthlyDescPrefix')}{' '}
              <strong>{MONTHS.find(m => m.value === month)?.label} {year}</strong>.
              {' '}{t('assets:depreciation_periodDateLabel')}: <code className="text-xs bg-gray-100 px-1 rounded">{lastDay(year, month)}</code>
            </p>
            <button onClick={() => postDepMutation.mutate()} disabled={postDepMutation.isPending}
              className="btn-primary">
              <Play className="h-4 w-4" />
              {postDepMutation.isPending ? t('assets:depreciation_posting') : `${t('assets:depreciation_postButtonPrefix')} ${MONTHS.find(m => m.value === month)?.label} ${year}`}
            </button>

            {postResult && (
              <div className="mt-4 bg-green-50 border border-green-200 rounded-lg p-3 text-sm">
                <p className="font-semibold text-green-800 mb-1">{t('assets:depreciation_postResultLabel')}:</p>
                <div className="grid grid-cols-3 gap-3">
                  {Object.entries(postResult).filter(([k]) => !['success','error'].includes(k)).map(([k, v]) => (
                    <div key={k}>
                      <p className="text-xs text-gray-500">{k}</p>
                      <p className="font-medium">{String(v)}</p>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </Card>

          {/* Asset list + schedule viewer */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <Card noPad className="md:col-span-1">
              <CardHeader title={t('assets:depreciation_assetListTitle')} subtitle={`${assets.length} ${t('assets:assetList_assetUnit')}`} />
              <div className="overflow-y-auto max-h-96">
                {assets.length === 0 ? (
                  <div className="p-4"><EmptyState title={t('assets:depreciation_noAssetsYet')} /></div>
                ) : (
                  <div className="divide-y divide-gray-100">
                    {assets.map((a: any) => (
                      <button key={a.id}
                        onClick={() => setSelectedAsset(a)}
                        className={`w-full text-left px-4 py-3 hover:bg-gray-50 transition-colors ${selectedAsset?.id === a.id ? 'bg-primary-50' : ''}`}>
                        <p className="text-sm font-medium text-gray-900">{a.asset_name}</p>
                        <p className="text-xs text-gray-400 mt-0.5">{a.category} · Rp {formatRupiah(a.acquisition_cost)}</p>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </Card>

            <Card noPad className="md:col-span-2">
              <CardHeader
                title={selectedAsset ? `${t('assets:depreciation_scheduleTitlePrefix')}: ${selectedAsset.asset_name}` : t('assets:depreciation_scheduleTitle')}
                subtitle={selectedAsset ? `${schedule.length} ${t('assets:depreciation_periodUnit')}` : t('assets:depreciation_selectAssetLeft')}
              />
              {!selectedAsset ? (
                <EmptyState title={t('assets:depreciation_selectAsset')} description={t('assets:depreciation_selectAssetDesc')} />
              ) : schLoading ? (
                <div className="flex justify-center py-12"><Spinner /></div>
              ) : (
                <div className="overflow-auto max-h-96">
                  <table className="data-table text-xs">
                    <thead>
                      <tr>
                        <th>{t('assets:depreciation_colPeriod')}</th>
                        <th className="right">{t('assets:depreciation_colCommercialDep')}</th>
                        <th className="right">{t('assets:depreciation_colFiscalDep')}</th>
                        <th className="right">{t('assets:depreciation_colBookValue')}</th>
                        <th>{t('common:status')}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {schedule.map((s: any, i: number) => (
                        <tr key={i} className={s.is_posted ? '' : 'text-gray-400'}>
                          <td>{s.period_label ?? formatDate(s.period_date)}</td>
                          <td className="right">{formatRupiah(s.depreciation_amount ?? s.commercial_dep)}</td>
                          <td className="right text-gray-500">{formatRupiah(s.fiscal_depreciation ?? s.fiscal_dep)}</td>
                          <td className="right font-medium">{formatRupiah(s.book_value)}</td>
                          <td><Badge status={s.is_posted ? 'posted' : 'pending'} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
          </div>
        </div>
      )}

      {/* Tab: Prepaid */}
      {tab === 'prepaid' && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <button onClick={() => postAmortiMutation.mutate()} disabled={postAmortiMutation.isPending}
              className="btn-secondary">
              <Play className="h-4 w-4" />
              {postAmortiMutation.isPending ? t('assets:depreciation_posting') : `${t('assets:depreciation_postAmortizationPrefix')} ${MONTHS.find(m => m.value === month)?.label} ${year}`}
            </button>
            <button onClick={() => setShowPpForm(true)} className="btn-primary">
              <Plus className="h-4 w-4" /> {t('assets:depreciation_addPrepaid')}
            </button>
          </div>

          {showPpForm && (
            <Card>
              <div className="flex items-center justify-between mb-3">
                <p className="text-sm font-semibold text-gray-700">{t('assets:depreciation_newPrepaidTitle')}</p>
                <button onClick={() => setShowPpForm(false)}><X className="h-4 w-4 text-gray-400" /></button>
              </div>
              <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
                <div className="col-span-2 md:col-span-3">
                  <label className="form-label">{t('common:description')}</label>
                  <input value={ppForm.description}
                    onChange={e => setPpForm({ ...ppForm, description: e.target.value })}
                    className="form-input" placeholder={t('assets:depreciation_prepaidDescPlaceholder')} />
                </div>
                <div>
                  <label className="form-label">{t('assets:depreciation_startDate')}</label>
                  <input type="date" value={ppForm.start_date}
                    onChange={e => setPpForm({ ...ppForm, start_date: e.target.value })}
                    className="form-input" />
                </div>
                <div>
                  <label className="form-label">{t('assets:depreciation_endDate')}</label>
                  <input type="date" value={ppForm.end_date}
                    onChange={e => setPpForm({ ...ppForm, end_date: e.target.value })}
                    className="form-input" />
                </div>
                <div>
                  <label className="form-label">{t('assets:depreciation_totalAmount')} (Rp)</label>
                  <input type="number" value={ppForm.total_amount}
                    onChange={e => setPpForm({ ...ppForm, total_amount: e.target.value })}
                    className="form-input" placeholder="0" />
                </div>
                <div>
                  <label className="form-label">{t('assets:depreciation_coaPrepaidAccount')} (COA)</label>
                  <input value={ppForm.coa_prepaid}
                    onChange={e => setPpForm({ ...ppForm, coa_prepaid: e.target.value })}
                    className="form-input font-mono text-sm" />
                </div>
                <div>
                  <label className="form-label">{t('assets:depreciation_coaExpenseAccount')} (COA)</label>
                  <input value={ppForm.coa_expense}
                    onChange={e => setPpForm({ ...ppForm, coa_expense: e.target.value })}
                    className="form-input font-mono text-sm" />
                </div>
              </div>
              <div className="flex gap-3 mt-4">
                <button onClick={() => createPpMutation.mutate()}
                  disabled={createPpMutation.isPending || !ppForm.description || !ppForm.end_date || !ppForm.total_amount}
                  className="btn-primary">
                  {createPpMutation.isPending ? t('assets:depreciation_savingEllipsis') : t('common:save')}
                </button>
                <button onClick={() => setShowPpForm(false)} className="btn-secondary">{t('common:cancel')}</button>
              </div>
            </Card>
          )}

          <Card noPad>
            <CardHeader title={t('assets:depreciation_prepaidTitle')} subtitle={`${prepaids.length} ${t('assets:depreciation_itemUnit')}`} />
            {ppLoading ? (
              <div className="flex justify-center py-16"><Spinner size="lg" /></div>
            ) : prepaids.length === 0 ? (
              <EmptyState title={t('assets:depreciation_noPrepaidYet')} />
            ) : (
              <div className="overflow-x-auto">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>{t('common:description')}</th>
                      <th>{t('assets:depreciation_colPeriod')}</th>
                      <th className="right">{t('common:total')}</th>
                      <th className="right">{t('assets:depreciation_colCharged')}</th>
                      <th className="right">{t('assets:depreciation_colRemaining')}</th>
                      <th>{t('common:status')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {prepaids.map((p: any) => (
                      <tr key={p.id}>
                        <td className="text-sm font-medium">{p.description}</td>
                        <td className="text-xs text-gray-500 whitespace-nowrap">
                          {formatDate(p.start_date)} – {formatDate(p.end_date)}
                        </td>
                        <td className="right">Rp {formatRupiah(p.total_amount)}</td>
                        <td className="right text-red-600">Rp {formatRupiah(p.amortized_amount ?? p.expensed_amount)}</td>
                        <td className="right font-medium">Rp {formatRupiah((p.total_amount ?? 0) - (p.amortized_amount ?? p.expensed_amount ?? 0))}</td>
                        <td><Badge status={p.status ?? 'active'} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </div>
      )}

      {/* Tab: Fiscal correction */}
      {tab === 'fiscal' && (
        <div className="space-y-4">
          <div className="flex items-center gap-3">
            <label className="form-label mb-0">{t('assets:depreciation_fiscalYearLabel')}:</label>
            <select value={fiscalYear} onChange={e => setFiscalYear(+e.target.value)} className="form-select w-24">
              {years.map(y => <option key={y} value={y}>{y}</option>)}
            </select>
          </div>

          {fiscalSummary && (
            <div className="grid grid-cols-3 gap-4">
              <div className="card p-4">
                <p className="text-xs text-gray-500 uppercase">{t('assets:depreciation_totalCommercialDep')}</p>
                <p className="text-xl font-bold text-gray-900 mt-1">Rp {formatRupiah(fiscalSummary.total_commercial)}</p>
              </div>
              <div className="card p-4">
                <p className="text-xs text-gray-500 uppercase">{t('assets:depreciation_totalFiscalDep')}</p>
                <p className="text-xl font-bold text-gray-900 mt-1">Rp {formatRupiah(fiscalSummary.total_fiscal)}</p>
              </div>
              <div className={`card p-4 border-l-4 ${(fiscalSummary.net_correction ?? 0) > 0 ? 'border-red-400' : 'border-green-400'}`}>
                <p className="text-xs text-gray-500 uppercase">{t('assets:depreciation_netCorrection')}</p>
                <p className={`text-xl font-bold mt-1 ${(fiscalSummary.net_correction ?? 0) > 0 ? 'text-red-600' : 'text-green-700'}`}>
                  Rp {formatRupiah(Math.abs(fiscalSummary.net_correction ?? 0))}
                </p>
                <p className="text-xs text-gray-400">{(fiscalSummary.net_correction ?? 0) > 0 ? t('assets:depreciation_correctionPositive') : t('assets:depreciation_correctionNegative')}</p>
              </div>
            </div>
          )}

          <Card noPad>
            <CardHeader title={`${t('assets:depreciation_fiscalCorrectionTitle')} ${fiscalYear}`} subtitle={`${fiscalRows.length} ${t('assets:assetList_assetUnit')}`} />
            {fiscalLoading ? (
              <div className="flex justify-center py-16"><Spinner size="lg" /></div>
            ) : fiscalRows.length === 0 ? (
              <EmptyState title={t('assets:depreciation_noFiscalData')} />
            ) : (
              <div className="overflow-x-auto">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>{t('assets:depreciation_colAsset')}</th>
                      <th>{t('assets:depreciation_colFiscalCategory')}</th>
                      <th className="right">{t('assets:depreciation_colCommercialDep')}</th>
                      <th className="right">{t('assets:depreciation_colFiscalDep')}</th>
                      <th className="right">{t('assets:depreciation_colCorrection')}</th>
                      <th>{t('assets:depreciation_colCorrectionType')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {fiscalRows.map((r: any, i: number) => {
                      const koreksi = (r.fiscal_depreciation ?? 0) - (r.commercial_depreciation ?? r.depreciation_amount ?? 0)
                      return (
                        <tr key={r.id ?? i}>
                          <td className="text-sm font-medium">{r.asset_name}</td>
                          <td className="text-xs text-gray-500">{r.fiscal_category ?? r.category}</td>
                          <td className="right">Rp {formatRupiah(r.commercial_depreciation ?? r.depreciation_amount)}</td>
                          <td className="right">Rp {formatRupiah(r.fiscal_depreciation)}</td>
                          <td className={`right font-semibold ${koreksi > 0 ? 'text-red-600' : koreksi < 0 ? 'text-green-700' : ''}`}>
                            Rp {formatRupiah(Math.abs(koreksi))}
                          </td>
                          <td className="text-xs">
                            {koreksi > 0
                              ? <span className="text-red-600">{t('assets:depreciation_correctionTypePositive')}</span>
                              : koreksi < 0
                              ? <span className="text-green-700">{t('assets:depreciation_correctionTypeNegative')}</span>
                              : <span className="text-gray-400">{t('assets:depreciation_correctionTypeNil')}</span>}
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
      )}
    </div>
  )
}
