import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Plus, RefreshCw, CheckCircle, FileText, XCircle, Filter } from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext'
import api from '../../lib/api'
import { Card, CardHeader } from '../../components/ui/Card'
import Spinner from '../../components/ui/Spinner'
import EmptyState from '../../components/ui/EmptyState'
import Badge from '../../components/ui/Badge'
import { formatRupiah, formatDate, currentYear, currentMonth, MONTHS, todayISO } from '../../lib/utils'
import { showToast } from '../../components/ui/Toast'

export default function WithholdingPage() {
  const { t } = useTranslation(['tax', 'common'])
  const { entityId } = useAuth()
  const qc = useQueryClient()

  const TAX_TYPE_LABELS: Record<string, string> = {
    PPh23:  t('tax:withholdingPage_taxTypePPh23'),
    PPh4_2: t('tax:withholdingPage_taxTypePPh4_2'),
  }

  const [taxTypeFilter, setTaxTypeFilter] = useState('')
  const [statusFilter, setStatusFilter]   = useState('')
  const [yearFilter, setYearFilter]       = useState(currentYear())
  const [monthFilter, setMonthFilter]     = useState<number | ''>('')
  const [showForm, setShowForm]           = useState(false)
  const [activeTab, setActiveTab]         = useState<'transactions' | 'spt' | 'rates'>('transactions')

  // WHT transactions
  const { data: txData, isLoading, refetch } = useQuery({
    queryKey: ['wht-tx', entityId, taxTypeFilter, statusFilter, yearFilter, monthFilter],
    queryFn: () =>
      api.get('/wht/transactions', {
        params: {
          entity_id:    entityId,
          tax_type:     taxTypeFilter || undefined,
          status:       statusFilter  || undefined,
          period_year:  yearFilter    || undefined,
          period_month: monthFilter   || undefined,
          size: 100,
        },
      }).then(r => r.data),
    enabled: !!entityId,
  })
  const transactions: any[] = Array.isArray(txData) ? txData : []

  // WHT SPT Masa
  const { data: sptData, isLoading: sptLoading } = useQuery({
    queryKey: ['wht-spt', entityId, taxTypeFilter, yearFilter],
    queryFn: () =>
      api.get('/wht/spt-masa', {
        params: {
          entity_id:   entityId,
          tax_type:    taxTypeFilter || undefined,
          period_year: yearFilter    || undefined,
        },
      }).then(r => r.data),
    enabled: !!entityId && activeTab === 'spt',
  })
  const sptList: any[] = Array.isArray(sptData) ? sptData : (sptData?.items ?? [])

  // WHT rates
  const { data: ratesData } = useQuery({
    queryKey: ['wht-rates', taxTypeFilter],
    queryFn: () =>
      api.get('/wht/rates', { params: taxTypeFilter ? { tax_type: taxTypeFilter } : {} }).then(r => r.data),
    enabled: activeTab === 'rates',
  })
  const rates: any[] = Array.isArray(ratesData) ? ratesData : []

  // Create manual WHT form
  const [form, setForm] = useState({
    tax_type: 'PPh23',
    income_type_code: '',
    vendor_id: '',
    transaction_date: todayISO(),
    dpp: '',
    description: '',
    has_npwp: true,
  })

  const createMutation = useMutation({
    mutationFn: () =>
      api.post('/wht/transactions/manual', {
        entity_id:        entityId,
        vendor_id:        form.vendor_id,
        tax_type:         form.tax_type,
        income_type_code: form.income_type_code,
        transaction_date: form.transaction_date,
        dpp:              parseFloat(form.dpp) || 0,
        description:      form.description || undefined,
        has_npwp:         form.has_npwp,
      }),
    onSuccess: () => {
      showToast(t('tax:withholdingPage_createSuccess'))
      setShowForm(false)
      qc.invalidateQueries({ queryKey: ['wht-tx'] })
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('tax:withholdingPage_createFailed'), 'error'),
  })

  // Confirm
  const confirmMutation = useMutation({
    mutationFn: ({ id, username }: { id: string; username: string }) =>
      api.post(`/wht/transactions/${id}/confirm`, { confirmed_by: username }),
    onSuccess: () => {
      showToast(t('tax:withholdingPage_confirmSuccess'))
      qc.invalidateQueries({ queryKey: ['wht-tx'] })
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('tax:withholdingPage_confirmFailed'), 'error'),
  })

  // Issue bukti potong
  const bupotMutation = useMutation({
    mutationFn: ({ id, username }: { id: string; username: string }) =>
      api.post(`/wht/transactions/${id}/issue-bukti-potong`, {
        bukti_potong_date: todayISO(),
        issued_by: username,
      }),
    onSuccess: () => {
      showToast(t('tax:withholdingPage_bupotSuccess'))
      qc.invalidateQueries({ queryKey: ['wht-tx'] })
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('tax:withholdingPage_bupotFailed'), 'error'),
  })

  // Create SPT Masa
  const [sptForm, setSptForm] = useState({
    tax_type: 'PPh23',
    period_year:  currentYear(),
    period_month: currentMonth(),
  })
  const createSptMutation = useMutation({
    mutationFn: () =>
      api.post('/wht/spt-masa', {
        entity_id:    entityId,
        tax_type:     sptForm.tax_type,
        period_year:  sptForm.period_year,
        period_month: sptForm.period_month,
        created_by:   'user',
      }),
    onSuccess: () => {
      showToast(t('tax:withholdingPage_sptCreateSuccess'))
      qc.invalidateQueries({ queryKey: ['wht-spt'] })
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('tax:withholdingPage_sptCreateFailed'), 'error'),
  })

  const years = [currentYear(), currentYear() - 1, currentYear() - 2]
  const totalTax = transactions.reduce((s, tx) => s + (tx.tax_amount ?? 0), 0)
  const totalDpp = transactions.reduce((s, tx) => s + (tx.dpp ?? 0), 0)

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">{t('tax:withholdingPage_title')}</h1>
          <p className="text-sm text-gray-500 mt-0.5">{t('tax:withholdingPage_subtitle')}</p>
        </div>
        <button onClick={() => setShowForm(true)} className="btn-primary">
          <Plus className="h-4 w-4" /> {t('tax:withholdingPage_newManual')}
        </button>
      </div>

      {/* Create form */}
      {showForm && (
        <Card>
          <p className="text-sm font-semibold text-gray-700 mb-4">{t('tax:withholdingPage_formTitle')}</p>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
            <div>
              <label className="form-label">{t('tax:withholdingPage_formTaxType')}</label>
              <select value={form.tax_type} onChange={e => setForm({ ...form, tax_type: e.target.value })} className="form-select">
                <option value="PPh23">{t('tax:withholdingPage_taxTypePPh23')}</option>
                <option value="PPh4_2">{t('tax:withholdingPage_taxTypePPh4_2')}</option>
              </select>
            </div>
            <div>
              <label className="form-label">{t('tax:withholdingPage_formIncomeTypeCode')}</label>
              <input value={form.income_type_code}
                onChange={e => setForm({ ...form, income_type_code: e.target.value })}
                className="form-input" placeholder="mis. 23-401-01" />
            </div>
            <div>
              <label className="form-label">{t('tax:withholdingPage_formVendorId')}</label>
              <input value={form.vendor_id}
                onChange={e => setForm({ ...form, vendor_id: e.target.value })}
                className="form-input font-mono text-sm" placeholder={t('tax:withholdingPage_formVendorIdPlaceholder')} />
            </div>
            <div>
              <label className="form-label">{t('tax:withholdingPage_formTransactionDate')}</label>
              <input type="date" value={form.transaction_date}
                onChange={e => setForm({ ...form, transaction_date: e.target.value })}
                className="form-input" />
            </div>
            <div>
              <label className="form-label">{t('tax:withholdingPage_formDpp')}</label>
              <input type="number" value={form.dpp}
                onChange={e => setForm({ ...form, dpp: e.target.value })}
                className="form-input" placeholder="0" />
            </div>
            <div>
              <label className="form-label">{t('tax:withholdingPage_formDescription')}</label>
              <input value={form.description}
                onChange={e => setForm({ ...form, description: e.target.value })}
                className="form-input" placeholder={t('common:optional')} />
            </div>
            <div className="flex items-center gap-2 mt-5">
              <input type="checkbox" id="has_npwp" checked={form.has_npwp}
                onChange={e => setForm({ ...form, has_npwp: e.target.checked })}
                className="h-4 w-4 rounded border-gray-300 text-primary-600" />
              <label htmlFor="has_npwp" className="text-sm text-gray-700">{t('tax:withholdingPage_formHasNpwp')}</label>
            </div>
          </div>
          <div className="flex gap-3 mt-4">
            <button onClick={() => createMutation.mutate()}
              disabled={createMutation.isPending || !form.vendor_id || !form.income_type_code || !form.dpp}
              className="btn-primary">
              {createMutation.isPending ? t('tax:withholdingPage_saving') : t('common:save')}
            </button>
            <button onClick={() => setShowForm(false)} className="btn-secondary">{t('common:cancel')}</button>
          </div>
        </Card>
      )}

      {/* Tabs */}
      <div className="border-b border-gray-200">
        <nav className="flex gap-1">
          {[
            { key: 'transactions', label: t('tax:withholdingPage_tabTransactions') },
            { key: 'spt',          label: t('tax:withholdingPage_tabSpt') },
            { key: 'rates',        label: t('tax:withholdingPage_tabRates') },
          ].map(tb => (
            <button key={tb.key}
              onClick={() => setActiveTab(tb.key as any)}
              className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                activeTab === tb.key
                  ? 'border-primary-600 text-primary-700'
                  : 'border-transparent text-gray-500 hover:text-gray-700'
              }`}>
              {tb.label}
            </button>
          ))}
        </nav>
      </div>

      {/* Filters */}
      {activeTab !== 'rates' && (
        <div className="flex items-center gap-2 flex-wrap">
          <Filter className="h-4 w-4 text-gray-400" />
          <select value={taxTypeFilter} onChange={e => setTaxTypeFilter(e.target.value)} className="form-select w-40">
            <option value="">{t('tax:withholdingPage_filterAllTax')}</option>
            <option value="PPh23">{t('tax:withholdingPage_taxTypePPh23')}</option>
            <option value="PPh4_2">{t('tax:withholdingPage_taxTypePPh4_2')}</option>
          </select>
          <select value={yearFilter} onChange={e => setYearFilter(+e.target.value)} className="form-select w-24">
            {years.map(y => <option key={y} value={y}>{y}</option>)}
          </select>
          {activeTab === 'transactions' && (
            <>
              <select value={monthFilter} onChange={e => setMonthFilter(e.target.value ? +e.target.value : '')} className="form-select w-36">
                <option value="">{t('tax:withholdingPage_filterAllMonth')}</option>
                {MONTHS.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
              </select>
              <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)} className="form-select w-40">
                <option value="">{t('tax:withholdingPage_filterAllStatus')}</option>
                <option value="draft">{t('common:draft')}</option>
                <option value="confirmed">{t('tax:withholdingPage_statusConfirmed')}</option>
                <option value="bukti_potong_issued">{t('tax:withholdingPage_statusBupotIssued')}</option>
                <option value="void">{t('tax:withholdingPage_statusVoid')}</option>
              </select>
            </>
          )}
          <button onClick={() => refetch()} className="btn-secondary"><RefreshCw className="h-4 w-4" /></button>
        </div>
      )}

      {/* Tab: Transactions */}
      {activeTab === 'transactions' && (
        <Card noPad>
          <CardHeader
            title={t('tax:withholdingPage_listTitle')}
            subtitle={t('tax:withholdingPage_listSubtitle', { count: transactions.length, total: formatRupiah(totalTax) })}
          />
          {isLoading ? (
            <div className="flex justify-center py-16"><Spinner size="lg" /></div>
          ) : transactions.length === 0 ? (
            <EmptyState title={t('tax:withholdingPage_emptyTitle')} description={t('tax:withholdingPage_emptyDescription')} />
          ) : (
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>{t('tax:withholdingPage_colTaxType')}</th>
                    <th>{t('tax:withholdingPage_colIncomeCode')}</th>
                    <th>{t('tax:withholdingPage_colVendor')}</th>
                    <th>{t('tax:withholdingPage_colNpwp')}</th>
                    <th>{t('tax:withholdingPage_colTransactionDate')}</th>
                    <th className="right">{t('tax:withholdingPage_colDpp')}</th>
                    <th className="right">{t('tax:withholdingPage_colRate')}</th>
                    <th className="right">{t('tax:withholdingPage_colTax')}</th>
                    <th>{t('tax:withholdingPage_colStatus')}</th>
                    <th>{t('tax:withholdingPage_colBupotNo')}</th>
                    <th>{t('tax:withholdingPage_colAction')}</th>
                  </tr>
                </thead>
                <tbody>
                  {transactions.map((tx: any) => (
                    <tr key={tx.id}>
                      <td>
                        <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${
                          tx.tax_type === 'PPh23' ? 'bg-blue-50 text-blue-700' : 'bg-purple-50 text-purple-700'
                        }`}>
                          {TAX_TYPE_LABELS[tx.tax_type] ?? tx.tax_type}
                        </span>
                      </td>
                      <td className="font-mono text-xs text-gray-500">{tx.income_type_code}</td>
                      <td className="text-sm">{tx.vendor_name}</td>
                      <td className="font-mono text-xs text-gray-400">
                        {tx.npwp || (!tx.has_npwp && <span className="text-amber-500">{t('tax:withholdingPage_noNpwp')}</span>)}
                      </td>
                      <td className="text-sm text-gray-500 whitespace-nowrap">{formatDate(tx.transaction_date)}</td>
                      <td className="right">Rp {formatRupiah(tx.dpp)}</td>
                      <td className="right text-sm text-gray-500">{tx.rate_pct}%</td>
                      <td className="right font-semibold text-red-600">Rp {formatRupiah(tx.tax_amount)}</td>
                      <td><Badge status={tx.status} /></td>
                      <td className="font-mono text-xs text-gray-400">{tx.bukti_potong_no ?? '—'}</td>
                      <td>
                        <div className="flex items-center gap-1">
                          {tx.status === 'draft' && (
                            <button
                              onClick={() => confirmMutation.mutate({ id: tx.id, username: 'user' })}
                              disabled={confirmMutation.isPending}
                              className="inline-flex items-center gap-1 text-xs px-2 py-1 bg-green-50 text-green-600 hover:bg-green-100 rounded-md">
                              <CheckCircle className="h-3 w-3" /> {t('tax:withholdingPage_actionConfirm')}
                            </button>
                          )}
                          {tx.status === 'confirmed' && (
                            <button
                              onClick={() => bupotMutation.mutate({ id: tx.id, username: 'user' })}
                              disabled={bupotMutation.isPending}
                              className="inline-flex items-center gap-1 text-xs px-2 py-1 bg-blue-50 text-blue-600 hover:bg-blue-100 rounded-md">
                              <FileText className="h-3 w-3" /> {t('tax:withholdingPage_actionIssueBupot')}
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
      )}

      {/* Tab: SPT Masa */}
      {activeTab === 'spt' && (
        <div className="space-y-4">
          <Card>
            <p className="text-sm font-semibold text-gray-700 mb-3">{t('tax:withholdingPage_sptFormTitle')}</p>
            <div className="flex items-end gap-3 flex-wrap">
              <div>
                <label className="form-label">{t('tax:withholdingPage_sptFormTaxType')}</label>
                <select value={sptForm.tax_type}
                  onChange={e => setSptForm({ ...sptForm, tax_type: e.target.value })}
                  className="form-select w-40">
                  <option value="PPh23">{t('tax:withholdingPage_taxTypePPh23')}</option>
                  <option value="PPh4_2">{t('tax:withholdingPage_taxTypePPh4_2')}</option>
                </select>
              </div>
              <div>
                <label className="form-label">{t('tax:withholdingPage_sptFormMonth')}</label>
                <select value={sptForm.period_month}
                  onChange={e => setSptForm({ ...sptForm, period_month: +e.target.value })}
                  className="form-select w-36">
                  {MONTHS.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
                </select>
              </div>
              <div>
                <label className="form-label">{t('tax:withholdingPage_sptFormYear')}</label>
                <input type="number" value={sptForm.period_year}
                  onChange={e => setSptForm({ ...sptForm, period_year: +e.target.value })}
                  className="form-input w-24" min={2020} />
              </div>
              <button onClick={() => createSptMutation.mutate()}
                disabled={createSptMutation.isPending}
                className="btn-primary">
                {createSptMutation.isPending ? t('tax:withholdingPage_sptCreating') : t('tax:withholdingPage_sptCreateButton')}
              </button>
            </div>
          </Card>

          <Card noPad>
            <CardHeader title={t('tax:withholdingPage_sptListTitle')} subtitle={t('tax:withholdingPage_sptListSubtitle', { count: sptList.length })} />
            {sptLoading ? (
              <div className="flex justify-center py-16"><Spinner size="lg" /></div>
            ) : sptList.length === 0 ? (
              <EmptyState title={t('tax:withholdingPage_sptEmptyTitle')} />
            ) : (
              <div className="overflow-x-auto">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>{t('tax:withholdingPage_sptColTaxType')}</th>
                      <th>{t('tax:withholdingPage_sptColPeriod')}</th>
                      <th className="right">{t('tax:withholdingPage_sptColTotalDpp')}</th>
                      <th className="right">{t('tax:withholdingPage_sptColTotalTax')}</th>
                      <th className="right">{t('tax:withholdingPage_sptColBupotCount')}</th>
                      <th>{t('tax:withholdingPage_sptColStatus')}</th>
                      <th>{t('tax:withholdingPage_sptColPaymentDate')}</th>
                      <th>{t('tax:withholdingPage_sptColNtpn')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sptList.map((s: any) => (
                      <tr key={s.id}>
                        <td>
                          <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${
                            s.tax_type === 'PPh23' ? 'bg-blue-50 text-blue-700' : 'bg-purple-50 text-purple-700'
                          }`}>
                            {TAX_TYPE_LABELS[s.tax_type] ?? s.tax_type}
                          </span>
                        </td>
                        <td className="font-medium">
                          {MONTHS.find(m => m.value === s.period_month)?.label} {s.period_year}
                        </td>
                        <td className="right">Rp {formatRupiah(s.total_dpp)}</td>
                        <td className="right font-semibold text-red-600">Rp {formatRupiah(s.total_tax)}</td>
                        <td className="right">{s.total_bukti_potong}</td>
                        <td><Badge status={s.status} /></td>
                        <td className="text-sm text-gray-500">{s.payment_date ? formatDate(s.payment_date) : '—'}</td>
                        <td className="font-mono text-xs text-gray-400">{s.payment_ntpn ?? '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </div>
      )}

      {/* Tab: Rates reference */}
      {activeTab === 'rates' && (
        <Card noPad>
          <CardHeader title={t('tax:withholdingPage_ratesTitle')} subtitle={t('tax:withholdingPage_ratesSubtitle')} />
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>{t('tax:withholdingPage_ratesColTaxType')}</th>
                  <th>{t('tax:withholdingPage_ratesColCode')}</th>
                  <th>{t('tax:withholdingPage_ratesColIncomeType')}</th>
                  <th className="right">{t('tax:withholdingPage_ratesColRate')}</th>
                  <th className="right">{t('tax:withholdingPage_ratesColRateNoNpwp')}</th>
                  <th>{t('tax:withholdingPage_ratesColEffectiveDate')}</th>
                  <th>{t('tax:withholdingPage_ratesColNotes')}</th>
                </tr>
              </thead>
              <tbody>
                {rates.map((r: any, i: number) => (
                  <tr key={i}>
                    <td>
                      <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${
                        r.tax_type === 'PPh23' ? 'bg-blue-50 text-blue-700' : 'bg-purple-50 text-purple-700'
                      }`}>
                        {TAX_TYPE_LABELS[r.tax_type] ?? r.tax_type}
                      </span>
                    </td>
                    <td className="font-mono text-xs text-gray-500">{r.income_type_code}</td>
                    <td className="text-sm">{r.income_type}</td>
                    <td className="right font-semibold">{r.rate_pct}%</td>
                    <td className="right text-amber-600">{r.rate_npwp_pct ? `${r.rate_npwp_pct}%` : '—'}</td>
                    <td className="text-sm text-gray-500">{r.effective_date ? formatDate(r.effective_date) : '—'}</td>
                    <td className="text-xs text-gray-400 max-w-xs truncate" title={r.notes}>{r.notes ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  )
}
