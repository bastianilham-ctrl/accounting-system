import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { RefreshCw, Play, CheckCircle, RotateCcw } from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext'
import api from '../../lib/api'
import { Card, CardHeader } from '../../components/ui/Card'
import Spinner from '../../components/ui/Spinner'
import EmptyState from '../../components/ui/EmptyState'
import Badge from '../../components/ui/Badge'
import { formatRupiah, formatDate, todayISO } from '../../lib/utils'
import { showToast } from '../../components/ui/Toast'

export default function RevaluationPage() {
  const { entityId } = useAuth()
  const { t } = useTranslation(['multicurrency', 'common'])
  const qc = useQueryClient()
  const [tab, setTab] = useState<'run' | 'history' | 'exposure'>('run')

  // ── Revaluation form ─────────────────────────────────────────────────────────
  const [form, setForm] = useState({
    revaluation_date: todayISO(),
    rate_type: 'middle',
    gl_gain_account: '7-1000',
    gl_loss_account: '8-1000',
    auto_reverse: false,
    notes: '',
  })
  const [previewData, setPreviewData] = useState<any>(null)
  const [runId, setRunId] = useState<string | null>(null)

  // Preview
  const previewQuery = useQuery({
    queryKey: ['reval-preview', entityId, form.revaluation_date, form.rate_type],
    queryFn: () =>
      api.get(`/multicurrency/revaluation/${entityId}/preview`, {
        params: { revaluation_date: form.revaluation_date, rate_type: form.rate_type },
      }).then(r => r.data),
    enabled: false,
  })

  const loadPreview = async () => {
    try {
      const r = await api.get(`/multicurrency/revaluation/${entityId}/preview`, {
        params: { revaluation_date: form.revaluation_date, rate_type: form.rate_type },
      })
      setPreviewData(r.data)
    } catch (e: any) {
      showToast(e?.response?.data?.detail ?? t('multicurrency:revaluation_previewLoadFailed'), 'error')
    }
  }

  // Create & run revaluation
  const createMutation = useMutation({
    mutationFn: () =>
      api.post('/multicurrency/revaluation', {
        entity_id:        entityId,
        revaluation_date: form.revaluation_date,
        rate_type:        form.rate_type,
        gl_gain_account:  form.gl_gain_account,
        gl_loss_account:  form.gl_loss_account,
        auto_reverse:     form.auto_reverse,
        notes:            form.notes || undefined,
      }),
    onSuccess: (r) => {
      setRunId(r.data?.run_id ?? r.data?.id)
      showToast(t('multicurrency:revaluation_createSuccess'))
      qc.invalidateQueries({ queryKey: ['reval-history'] })
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('multicurrency:revaluation_createFailed'), 'error'),
  })

  // Post revaluation
  const postMutation = useMutation({
    mutationFn: (id: string) =>
      api.post(`/multicurrency/revaluation/${id}/post`, { entity_id: entityId }),
    onSuccess: () => {
      showToast(t('multicurrency:revaluation_postSuccess'))
      setRunId(null)
      qc.invalidateQueries({ queryKey: ['reval-history'] })
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('multicurrency:revaluation_postFailed'), 'error'),
  })

  // Reverse revaluation
  const reverseMutation = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) =>
      api.post(`/multicurrency/revaluation/${id}/reverse`, { entity_id: entityId, reason }),
    onSuccess: () => {
      showToast(t('multicurrency:revaluation_reverseSuccess'), 'warning')
      qc.invalidateQueries({ queryKey: ['reval-history'] })
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('multicurrency:revaluation_reverseFailed'), 'error'),
  })

  // History
  const { data: histData, isLoading: histLoading, refetch: refetchHist } = useQuery({
    queryKey: ['reval-history', entityId],
    queryFn: () => api.get(`/multicurrency/revaluation/${entityId}/history`).then(r => r.data),
    enabled: !!entityId && tab === 'history',
  })
  const history: any[] = Array.isArray(histData) ? histData : (histData?.items ?? [])

  // Exposure
  const { data: exposureData, isLoading: expLoading } = useQuery({
    queryKey: ['fx-exposure', entityId],
    queryFn: () => api.get(`/multicurrency/exposure/${entityId}`).then(r => r.data),
    enabled: !!entityId && tab === 'exposure',
  })
  const exposures: any[] = Array.isArray(exposureData) ? exposureData : (exposureData?.items ?? [])

  const previewItems: any[] = previewData?.items ?? previewData?.accounts ?? []

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">{t('multicurrency:revaluation_pageTitle')}</h1>
        <p className="text-sm text-gray-500 mt-0.5">{t('multicurrency:revaluation_pageSubtitle')}</p>
      </div>

      {/* Tabs */}
      <div className="border-b border-gray-200">
        <nav className="flex gap-1">
          {[
            { key: 'run',      label: t('multicurrency:revaluation_tabRun') },
            { key: 'history',  label: t('multicurrency:revaluation_tabHistory') },
            { key: 'exposure', label: t('multicurrency:revaluation_tabExposure') },
          ].map(tabItem => (
            <button key={tabItem.key}
              onClick={() => setTab(tabItem.key as any)}
              className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                tab === tabItem.key ? 'border-primary-600 text-primary-700' : 'border-transparent text-gray-500 hover:text-gray-700'
              }`}>
              {tabItem.label}
            </button>
          ))}
        </nav>
      </div>

      {/* Tab: Run */}
      {tab === 'run' && (
        <div className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <Card>
              <p className="text-sm font-semibold text-gray-800 mb-4">{t('multicurrency:revaluation_runParams')}</p>
              <div className="space-y-3">
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="form-label">{t('multicurrency:revaluation_dateLabel')}</label>
                    <input type="date" value={form.revaluation_date}
                      onChange={e => setForm({ ...form, revaluation_date: e.target.value })}
                      className="form-input" />
                  </div>
                  <div>
                    <label className="form-label">{t('multicurrency:revaluation_rateTypeLabel')}</label>
                    <select value={form.rate_type}
                      onChange={e => setForm({ ...form, rate_type: e.target.value })}
                      className="form-select">
                      <option value="middle">{t('multicurrency:revaluation_typeMiddle')}</option>
                      <option value="buying">{t('multicurrency:revaluation_typeBuying')}</option>
                      <option value="selling">{t('multicurrency:revaluation_typeSelling')}</option>
                    </select>
                  </div>
                  <div>
                    <label className="form-label">{t('multicurrency:revaluation_gainAccountLabel')}</label>
                    <input value={form.gl_gain_account}
                      onChange={e => setForm({ ...form, gl_gain_account: e.target.value })}
                      className="form-input font-mono text-sm" />
                  </div>
                  <div>
                    <label className="form-label">{t('multicurrency:revaluation_lossAccountLabel')}</label>
                    <input value={form.gl_loss_account}
                      onChange={e => setForm({ ...form, gl_loss_account: e.target.value })}
                      className="form-input font-mono text-sm" />
                  </div>
                  <div className="col-span-2">
                    <label className="form-label">{t('multicurrency:revaluation_notesLabel')}</label>
                    <input value={form.notes}
                      onChange={e => setForm({ ...form, notes: e.target.value })}
                      className="form-input" placeholder={t('multicurrency:revaluation_notesPlaceholder')} />
                  </div>
                  <div className="col-span-2 flex items-center gap-2">
                    <input type="checkbox" id="auto_reverse" checked={form.auto_reverse}
                      onChange={e => setForm({ ...form, auto_reverse: e.target.checked })}
                      className="h-4 w-4 rounded border-gray-300 text-primary-600" />
                    <label htmlFor="auto_reverse" className="text-sm text-gray-700">
                      {t('multicurrency:revaluation_autoReverseLabel')}
                    </label>
                  </div>
                </div>
                <div className="flex gap-3 pt-2">
                  <button onClick={loadPreview} className="btn-secondary">
                    {t('multicurrency:revaluation_preview')}
                  </button>
                  <button onClick={() => createMutation.mutate()}
                    disabled={createMutation.isPending}
                    className="btn-primary">
                    <Play className="h-4 w-4" />
                    {createMutation.isPending ? t('multicurrency:revaluation_processing') : t('multicurrency:revaluation_runButton')}
                  </button>
                  {runId && (
                    <button onClick={() => postMutation.mutate(runId)}
                      disabled={postMutation.isPending}
                      className="btn-primary bg-green-600 hover:bg-green-700">
                      <CheckCircle className="h-4 w-4" />
                      {postMutation.isPending ? t('multicurrency:revaluation_posting') : t('multicurrency:revaluation_postJournal')}
                    </button>
                  )}
                </div>
              </div>
            </Card>

            {/* Preview panel */}
            <Card>
              <p className="text-sm font-semibold text-gray-800 mb-3">{t('multicurrency:revaluation_previewTitle')}</p>
              {!previewData ? (
                <p className="text-sm text-gray-400 italic">{t('multicurrency:revaluation_previewHint')}</p>
              ) : (
                <>
                  <div className="grid grid-cols-2 gap-3 mb-3">
                    <div className="bg-green-50 rounded-lg p-3">
                      <p className="text-xs text-gray-500">{t('multicurrency:revaluation_gainLabel')}</p>
                      <p className="text-lg font-bold text-green-700">
                        Rp {formatRupiah(previewData.total_gain ?? 0)}
                      </p>
                    </div>
                    <div className="bg-red-50 rounded-lg p-3">
                      <p className="text-xs text-gray-500">{t('multicurrency:revaluation_lossLabel')}</p>
                      <p className="text-lg font-bold text-red-600">
                        Rp {formatRupiah(previewData.total_loss ?? 0)}
                      </p>
                    </div>
                  </div>
                  <div className="overflow-auto max-h-56">
                    <table className="data-table text-xs">
                      <thead>
                        <tr>
                          <th>{t('multicurrency:revaluation_colAccount')}</th>
                          <th>{t('multicurrency:revaluation_colCcy')}</th>
                          <th className="right">{t('multicurrency:revaluation_colBalanceFcy')}</th>
                          <th className="right">{t('multicurrency:revaluation_colBookIdr')}</th>
                          <th className="right">{t('multicurrency:revaluation_colRevalIdr')}</th>
                          <th className="right">{t('multicurrency:revaluation_colDiff')}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {previewItems.map((item: any, i: number) => {
                          const diff = (item.revalued_idr ?? 0) - (item.book_value_idr ?? 0)
                          return (
                            <tr key={i}>
                              <td className="font-mono">{item.account_code}</td>
                              <td className="font-bold">{item.currency}</td>
                              <td className="right">{item.balance_fcy?.toLocaleString('id-ID', { maximumFractionDigits: 2 })}</td>
                              <td className="right">{formatRupiah(item.book_value_idr)}</td>
                              <td className="right">{formatRupiah(item.revalued_idr)}</td>
                              <td className={`right font-semibold ${diff > 0 ? 'text-green-600' : diff < 0 ? 'text-red-600' : ''}`}>
                                {diff >= 0 ? '+' : ''}{formatRupiah(diff)}
                              </td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
            </Card>
          </div>
        </div>
      )}

      {/* Tab: History */}
      {tab === 'history' && (
        <Card noPad>
          <CardHeader
            title={t('multicurrency:revaluation_historyTitle')}
            subtitle={`${history.length} ${t('multicurrency:revaluation_runCountSuffix')}`}
            actions={<button onClick={() => refetchHist()} className="btn-secondary"><RefreshCw className="h-4 w-4" /></button>}
          />
          {histLoading ? (
            <div className="flex justify-center py-16"><Spinner size="lg" /></div>
          ) : history.length === 0 ? (
            <EmptyState title={t('multicurrency:revaluation_emptyHistoryTitle')} />
          ) : (
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>{t('multicurrency:revaluation_colDate')}</th>
                    <th>{t('multicurrency:revaluation_colRateType')}</th>
                    <th className="right">{t('multicurrency:revaluation_colGain')}</th>
                    <th className="right">{t('multicurrency:revaluation_colLoss')}</th>
                    <th>{t('multicurrency:revaluation_colStatus')}</th>
                    <th>{t('multicurrency:revaluation_colAutoReverse')}</th>
                    <th>{t('multicurrency:revaluation_colNotes')}</th>
                    <th>{t('multicurrency:revaluation_colAction')}</th>
                  </tr>
                </thead>
                <tbody>
                  {history.map((h: any) => (
                    <tr key={h.id ?? h.run_id}>
                      <td className="text-sm text-gray-500 whitespace-nowrap">{formatDate(h.revaluation_date)}</td>
                      <td className="text-sm capitalize">{h.rate_type}</td>
                      <td className="right text-green-700">Rp {formatRupiah(h.total_gain)}</td>
                      <td className="right text-red-600">Rp {formatRupiah(h.total_loss)}</td>
                      <td><Badge status={h.status ?? 'draft'} /></td>
                      <td className="text-center text-sm">{h.auto_reverse ? t('multicurrency:revaluation_yes') : '—'}</td>
                      <td className="text-xs text-gray-400">{h.notes ?? '—'}</td>
                      <td>
                        {(h.status === 'posted') && (
                          <button
                            onClick={() => {
                              const reason = prompt(t('multicurrency:revaluation_reversePrompt')) || t('multicurrency:revaluation_reverseDefaultReason')
                              reverseMutation.mutate({ id: h.id ?? h.run_id, reason })
                            }}
                            className="inline-flex items-center gap-1 text-xs px-2 py-1 bg-gray-50 text-gray-600 hover:bg-gray-100 rounded-md">
                            <RotateCcw className="h-3 w-3" /> {t('multicurrency:revaluation_reverseButton')}
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      )}

      {/* Tab: Exposure */}
      {tab === 'exposure' && (
        <Card noPad>
          <CardHeader title={t('multicurrency:revaluation_exposureTitle')} />
          {expLoading ? (
            <div className="flex justify-center py-16"><Spinner size="lg" /></div>
          ) : exposures.length === 0 ? (
            <EmptyState title={t('multicurrency:revaluation_emptyExposureTitle')} />
          ) : (
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>{t('multicurrency:revaluation_colAccount')}</th>
                    <th>{t('multicurrency:revaluation_colAccountName')}</th>
                    <th>{t('multicurrency:revaluation_colCurrency')}</th>
                    <th className="right">{t('multicurrency:revaluation_colBalanceFcy')}</th>
                    <th className="right">{t('multicurrency:revaluation_colBookValueIdr')}</th>
                    <th className="right">{t('multicurrency:revaluation_colCurrentValueIdr')}</th>
                    <th className="right">{t('multicurrency:revaluation_colUnrealizedGl')}</th>
                  </tr>
                </thead>
                <tbody>
                  {exposures.map((e: any, i: number) => {
                    const gl = (e.current_idr ?? 0) - (e.book_idr ?? 0)
                    return (
                      <tr key={i}>
                        <td className="font-mono text-xs">{e.account_code}</td>
                        <td className="text-sm">{e.account_name}</td>
                        <td className="font-bold font-mono text-sm">{e.currency}</td>
                        <td className="right">{e.balance_fcy?.toLocaleString('id-ID', { maximumFractionDigits: 2 })}</td>
                        <td className="right">{formatRupiah(e.book_idr)}</td>
                        <td className="right">{formatRupiah(e.current_idr)}</td>
                        <td className={`right font-semibold ${gl > 0 ? 'text-green-700' : gl < 0 ? 'text-red-600' : ''}`}>
                          {gl >= 0 ? '+' : ''}{formatRupiah(gl)}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      )}
    </div>
  )
}
