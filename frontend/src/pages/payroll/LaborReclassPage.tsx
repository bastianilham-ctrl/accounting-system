import { useState } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { RefreshCw, ArrowRightLeft, TrendingUp } from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext'
import api from '../../lib/api'
import { Card } from '../../components/ui/Card'
import Spinner from '../../components/ui/Spinner'
import EmptyState from '../../components/ui/EmptyState'
import { formatRupiah, currentYear, currentMonth, MONTHS } from '../../lib/utils'
import { showToast } from '../../components/ui/Toast'

export default function LaborReclassPage() {
  const { entityId } = useAuth()
  const { t } = useTranslation(['payroll', 'common'])

  const [year, setYear] = useState(currentYear())
  const [month, setMonth] = useState(currentMonth())
  const [bebanGajiCode, setBebanGajiCode] = useState('6-9-001')

  const { data: varianceData, isLoading, refetch } = useQuery({
    queryKey: ['payroll-variance', entityId, year, month],
    queryFn: () =>
      api
        .get('/costing/payroll-variance', { params: { entity_id: entityId, year, month } })
        .then((r) => r.data),
    enabled: !!entityId,
  })

  const rows: any[] = Array.isArray(varianceData) ? varianceData : []

  const reclassMutation = useMutation({
    mutationFn: () =>
      api.post('/costing/labor-reclass', {
        entity_id: entityId,
        year,
        month,
        beban_gaji_code: bebanGajiCode,
      }),
    onSuccess: (r) => {
      const d = r.data
      showToast(
        t('payroll:lr_postSuccess', {
          journal_no: d.journal_no,
          total: formatRupiah(d.total_reclassed),
          count: d.employees_count,
        }),
      )
      refetch()
    },
    onError: (e: any) =>
      showToast(e?.response?.data?.detail ?? t('payroll:lr_postFailed'), 'error'),
  })

  const totalActual = rows.reduce((s, r) => s + (r.actual_payroll ?? 0), 0)
  const totalEstimate = rows.reduce((s, r) => s + (r.estimate_cost ?? 0), 0)
  const totalVariance = rows.reduce((s, r) => s + (r.variance ?? 0), 0)

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">{t('payroll:lr_title')}</h1>
          <p className="text-sm text-gray-500 mt-0.5">{t('payroll:lr_subtitle')}</p>
        </div>
      </div>

      {/* Filter bar */}
      <Card>
        <div className="flex flex-wrap items-end gap-4">
          <div>
            <label className="form-label">{t('payroll:month')}</label>
            <select
              value={month}
              onChange={(e) => setMonth(+e.target.value)}
              className="form-select w-36"
            >
              {MONTHS.map((m) => (
                <option key={m.value} value={m.value}>
                  {m.label}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="form-label">{t('payroll:year')}</label>
            <input
              type="number"
              value={year}
              onChange={(e) => setYear(+e.target.value)}
              className="form-input w-24"
              min={2020}
              max={2099}
            />
          </div>
          <div>
            <label className="form-label">{t('payroll:lr_bebanGajiCode')}</label>
            <input
              type="text"
              value={bebanGajiCode}
              onChange={(e) => setBebanGajiCode(e.target.value)}
              className="form-input w-36"
              placeholder="e.g. 6-9-001"
            />
          </div>
          <button
            onClick={() => refetch()}
            disabled={isLoading}
            className="btn-secondary flex items-center gap-2"
          >
            <RefreshCw className={`h-4 w-4 ${isLoading ? 'animate-spin' : ''}`} />
            {t('common:refresh')}
          </button>
          <button
            onClick={() => reclassMutation.mutate()}
            disabled={reclassMutation.isPending || rows.length === 0}
            className="btn-primary flex items-center gap-2"
          >
            <ArrowRightLeft className="h-4 w-4" />
            {reclassMutation.isPending ? t('common:processing') : t('payroll:lr_postBtn')}
          </button>
        </div>
      </Card>

      {/* Summary cards */}
      {rows.length > 0 && (
        <div className="grid grid-cols-3 gap-4">
          <Card>
            <p className="text-sm text-gray-500">{t('payroll:lr_totalActual')}</p>
            <p className="text-2xl font-bold text-gray-900 mt-1">{formatRupiah(totalActual)}</p>
          </Card>
          <Card>
            <p className="text-sm text-gray-500">{t('payroll:lr_totalEstimate')}</p>
            <p className="text-2xl font-bold text-gray-900 mt-1">{formatRupiah(totalEstimate)}</p>
          </Card>
          <Card>
            <p className="text-sm text-gray-500">{t('payroll:lr_totalVariance')}</p>
            <p
              className={`text-2xl font-bold mt-1 ${totalVariance > 0 ? 'text-red-600' : 'text-green-600'}`}
            >
              {formatRupiah(totalVariance)}
            </p>
          </Card>
        </div>
      )}

      {/* Variance table */}
      <Card>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-base font-semibold text-gray-900">
            {t('payroll:lr_varianceTitle', {
              month: MONTHS.find((m) => m.value === month)?.label,
              year,
            })}
          </h2>
          <TrendingUp className="h-5 w-5 text-gray-400" />
        </div>

        {isLoading ? (
          <div className="flex justify-center py-8">
            <Spinner />
          </div>
        ) : rows.length === 0 ? (
          <EmptyState
            title={t('payroll:lr_emptyTitle')}
            description={t('payroll:lr_emptyDescription')}
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="border-b border-gray-200">
                  <th className="text-left py-2 pr-4 font-medium text-gray-600">
                    {t('payroll:lr_colEmployee')}
                  </th>
                  <th className="text-right py-2 px-4 font-medium text-gray-600">
                    {t('payroll:lr_colHours')}
                  </th>
                  <th className="text-right py-2 px-4 font-medium text-gray-600">
                    {t('payroll:lr_colEstimate')}
                  </th>
                  <th className="text-right py-2 px-4 font-medium text-gray-600">
                    {t('payroll:lr_colActual')}
                  </th>
                  <th className="text-right py-2 pl-4 font-medium text-gray-600">
                    {t('payroll:lr_colVariance')}
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <>
                    <tr
                      key={row.employee_id}
                      className="border-b border-gray-100 bg-gray-50 font-medium"
                    >
                      <td className="py-2 pr-4 text-gray-900">{row.employee_name}</td>
                      <td className="text-right py-2 px-4 text-gray-700">
                        {(row.total_hours ?? 0).toFixed(1)}h
                      </td>
                      <td className="text-right py-2 px-4 text-gray-700">
                        {formatRupiah(row.estimate_cost)}
                      </td>
                      <td className="text-right py-2 px-4 text-gray-700">
                        {formatRupiah(row.actual_payroll)}
                      </td>
                      <td
                        className={`text-right py-2 pl-4 font-semibold ${(row.variance ?? 0) > 0 ? 'text-red-600' : 'text-green-600'}`}
                      >
                        {formatRupiah(row.variance)}
                        {row.variance_pct != null && (
                          <span className="ml-1 text-xs font-normal text-gray-400">
                            ({row.variance_pct.toFixed(1)}%)
                          </span>
                        )}
                      </td>
                    </tr>
                    {(row.by_project ?? []).map((p: any) => (
                      <tr key={`${row.employee_id}-${p.project_code}`} className="border-b border-gray-50">
                        <td className="py-1.5 pr-4 pl-6 text-gray-500 text-xs">
                          ↳ {p.project_code}
                          {p.cost_center ? (
                            <span className="ml-2 text-gray-400">({p.cost_center})</span>
                          ) : null}
                        </td>
                        <td className="text-right py-1.5 px-4 text-xs text-gray-500">
                          {(p.hours ?? 0).toFixed(1)}h
                        </td>
                        <td className="text-right py-1.5 px-4 text-xs text-gray-500">
                          {formatRupiah(p.estimate_cost)}
                        </td>
                        <td className="text-right py-1.5 px-4 text-xs text-gray-500">
                          {formatRupiah(p.actual_cost)}
                        </td>
                        <td
                          className={`text-right py-1.5 pl-4 text-xs ${(p.variance ?? 0) > 0 ? 'text-red-500' : 'text-green-500'}`}
                        >
                          {formatRupiah(p.variance)}
                        </td>
                      </tr>
                    ))}
                  </>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}
