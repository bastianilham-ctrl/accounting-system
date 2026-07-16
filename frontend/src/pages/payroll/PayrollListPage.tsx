import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { RefreshCw, Play, CheckCircle, FileDown } from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext'
import api from '../../lib/api'
import { Card, CardHeader } from '../../components/ui/Card'
import Spinner from '../../components/ui/Spinner'
import EmptyState from '../../components/ui/EmptyState'
import Badge from '../../components/ui/Badge'
import { formatRupiah, formatDate, currentYear, currentMonth, MONTHS } from '../../lib/utils'
import { showToast } from '../../components/ui/Toast'

export default function PayrollListPage() {
  const { entityId } = useAuth()
  const { t } = useTranslation(['payroll', 'common'])
  const qc = useQueryClient()
  const [year, setYear] = useState(currentYear())
  const [showRunForm, setShowRunForm] = useState(false)
  const [runMonth, setRunMonth] = useState(currentMonth())
  const [runYear, setRunYear] = useState(currentYear())

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['payroll-runs', entityId, year],
    queryFn: () =>
      api.get('/payroll/runs', { params: { entity_id: entityId, year } }).then((r) => r.data),
    enabled: !!entityId,
  })

  const runs: any[] = Array.isArray(data) ? data : (data?.items ?? data?.runs ?? [])

  const createMutation = useMutation({
    mutationFn: () => api.post('/payroll/runs', { entity_id: entityId, year: runYear, month: runMonth }),
    onSuccess: () => {
      showToast(t('payroll:createSuccess', { month: MONTHS.find(m => m.value === runMonth)?.label, year: runYear }))
      setShowRunForm(false)
      qc.invalidateQueries({ queryKey: ['payroll-runs'] })
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('payroll:createFailed'), 'error'),
  })

  const approveMutation = useMutation({
    mutationFn: (id: string) => api.post(`/payroll/runs/${id}/approve`),
    onSuccess: () => { showToast(t('payroll:approveSuccess')); qc.invalidateQueries({ queryKey: ['payroll-runs'] }) },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('payroll:approveFailed'), 'error'),
  })

  const totalNet = runs.reduce((s, r) => s + (r.total_net_pay ?? r.total_take_home ?? 0), 0)
  const totalGross = runs.reduce((s, r) => s + (r.total_gross ?? r.total_salary ?? 0), 0)

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">{t('payroll:title')}</h1>
          <p className="text-sm text-gray-500 mt-0.5">{t('payroll:subtitle')}</p>
        </div>
        <button onClick={() => setShowRunForm(true)} className="btn-primary">
          <Play className="h-4 w-4" /> {t('payroll:processPayroll')}
        </button>
      </div>

      {/* Run form */}
      {showRunForm && (
        <Card>
          <p className="text-sm font-semibold text-gray-700 mb-3">{t('payroll:newRunTitle')}</p>
          <div className="flex items-end gap-4">
            <div>
              <label className="form-label">{t('payroll:month')}</label>
              <select value={runMonth} onChange={(e) => setRunMonth(+e.target.value)} className="form-select w-36">
                {MONTHS.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
              </select>
            </div>
            <div>
              <label className="form-label">{t('payroll:year')}</label>
              <input type="number" value={runYear} onChange={(e) => setRunYear(+e.target.value)}
                className="form-input w-24" min={2020} max={2099} />
            </div>
            <button onClick={() => createMutation.mutate()} disabled={createMutation.isPending}
              className="btn-primary">
              {createMutation.isPending ? t('payroll:processing') : t('payroll:startProcess')}
            </button>
            <button onClick={() => setShowRunForm(false)} className="btn-secondary">{t('payroll:cancel')}</button>
          </div>
        </Card>
      )}

      {/* Summary */}
      {runs.length > 0 && (
        <div className="grid grid-cols-3 gap-4">
          <div className="card p-4">
            <p className="text-xs font-medium text-gray-500 uppercase">{t('payroll:totalRun')}</p>
            <p className="text-2xl font-bold text-gray-900 mt-1">{runs.length}</p>
          </div>
          <div className="card p-4">
            <p className="text-xs font-medium text-gray-500 uppercase">{t('payroll:totalGross')}</p>
            <p className="text-xl font-bold text-gray-900 mt-1">Rp {formatRupiah(totalGross)}</p>
          </div>
          <div className="card p-4">
            <p className="text-xs font-medium text-gray-500 uppercase">{t('payroll:totalNet')}</p>
            <p className="text-xl font-bold text-green-700 mt-1">Rp {formatRupiah(totalNet)}</p>
          </div>
        </div>
      )}

      <Card noPad>
        <CardHeader
          title={t('payroll:runsTitle', { year })}
          subtitle={t('payroll:runsSubtitle', { count: runs.length })}
          actions={
            <div className="flex items-center gap-2">
              <select value={year} onChange={(e) => setYear(+e.target.value)} className="form-select w-24">
                {[currentYear(), currentYear() - 1, currentYear() - 2].map((y) => (
                  <option key={y} value={y}>{y}</option>
                ))}
              </select>
              <button onClick={() => refetch()} className="btn-secondary">
                <RefreshCw className="h-4 w-4" />
              </button>
            </div>
          }
        />

        {isLoading ? (
          <div className="flex justify-center py-16"><Spinner size="lg" /></div>
        ) : runs.length === 0 ? (
          <EmptyState title={t('payroll:emptyTitle')} description={t('payroll:emptyDescription')} />
        ) : (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>{t('payroll:colPeriod')}</th>
                  <th>{t('payroll:colProcessDate')}</th>
                  <th className="right">{t('payroll:colEmployees')}</th>
                  <th className="right">{t('payroll:colTotalGross')}</th>
                  <th className="right">{t('payroll:colTotalPph21')}</th>
                  <th className="right">{t('payroll:colTotalNet')}</th>
                  <th>{t('payroll:colStatus')}</th>
                  <th>{t('payroll:colAction')}</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((r) => (
                  <tr key={r.id}>
                    <td className="font-medium">
                      {MONTHS.find((m) => m.value === r.month)?.label ?? r.month} {r.year}
                    </td>
                    <td className="text-sm text-gray-500">{formatDate(r.created_at ?? r.run_date)}</td>
                    <td className="right">{r.employee_count ?? '-'}</td>
                    <td className="right">Rp {formatRupiah(r.total_gross ?? r.total_salary)}</td>
                    <td className="right text-red-600">Rp {formatRupiah(r.total_pph21)}</td>
                    <td className="right font-semibold text-green-700">
                      Rp {formatRupiah(r.total_net_pay ?? r.total_take_home)}
                    </td>
                    <td><Badge status={r.status ?? 'draft'} /></td>
                    <td>
                      <div className="flex items-center gap-1.5">
                        {r.status === 'draft' && (
                          <button onClick={() => approveMutation.mutate(r.id)}
                            disabled={approveMutation.isPending}
                            className="inline-flex items-center gap-1 text-xs px-2 py-1 bg-green-50 text-green-600 hover:bg-green-100 rounded-md">
                            <CheckCircle className="h-3 w-3" /> {t('payroll:approve')}
                          </button>
                        )}
                        {(r.status === 'approved' || r.status === 'posted') && (
                          <button
                            onClick={() => api.get(`/payroll/runs/${r.id}/slip-pdf`, { responseType: 'blob' })}
                            className="inline-flex items-center gap-1 text-xs px-2 py-1 bg-gray-50 text-gray-600 hover:bg-gray-100 rounded-md">
                            <FileDown className="h-3 w-3" /> {t('payroll:slip')}
                          </button>
                        )}
                      </div>
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
