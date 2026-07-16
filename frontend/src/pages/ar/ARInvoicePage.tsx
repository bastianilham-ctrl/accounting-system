import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Search, RefreshCw, Filter, Plus, X, Send, Trash2 } from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext'
import api from '../../lib/api'
import { Card, CardHeader } from '../../components/ui/Card'
import Spinner from '../../components/ui/Spinner'
import EmptyState from '../../components/ui/EmptyState'
import Badge from '../../components/ui/Badge'
import CurrencySelect from '../../components/shared/CurrencySelect'
import { useLatestRate } from '../../lib/currency'
import { formatRupiah, formatCurrency, formatDate, todayISO } from '../../lib/utils'
import { showToast } from '../../components/ui/Toast'

type ARInvoiceLineForm = {
  account_code: string
  description: string
  amount: string
  cost_center: string
  project_id: string
}

const emptyLine = (): ARInvoiceLineForm => ({
  account_code: '', description: '', amount: '', cost_center: '', project_id: '',
})

const emptyForm = {
  customer_name: '', customer_npwp: '', invoice_no: '', invoice_date: todayISO(),
  due_date: '', ppn_rate: '11', contract_ref: '',
  lines: [emptyLine()] as ARInvoiceLineForm[],
  currency: 'IDR', exchange_rate: '1',
}

export default function ARInvoicePage() {
  const { t } = useTranslation(['ar', 'common', 'multicurrency'])
  const { entityId } = useAuth()
  const qc = useQueryClient()

  const STATUS_OPTS = [
    { value: '', label: t('ar:statusAll') },
    { value: 'draft', label: t('ar:statusDraft') },
    { value: 'posted', label: t('ar:statusPosted') },
    { value: 'partial', label: t('ar:statusPartial') },
    { value: 'paid', label: t('ar:statusPaid') },
    { value: 'overdue', label: t('ar:statusOverdue') },
    { value: 'cancelled', label: t('ar:statusCancelled') },
  ]

  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('')
  const [page, setPage] = useState(1)
  const pageSize = 50
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState(emptyForm)

  const isFcy = form.currency !== 'IDR'
  const exchangeRateNum = parseFloat(form.exchange_rate) || 1

  // Auto-fill kurs terbaru saat ganti mata uang (tetap bisa dioverride manual)
  const { rate: latestRate } = useLatestRate(form.currency)
  useEffect(() => {
    if (form.currency === 'IDR') { setForm((f) => ({ ...f, exchange_rate: '1' })); return }
    if (latestRate != null) setForm((f) => ({ ...f, exchange_rate: String(latestRate) }))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [form.currency, latestRate])

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['ar-invoices', entityId, status, page],
    queryFn: () =>
      api.get(`/ar/invoices`, {
        params: {
          entity_id: entityId,
          status: status || undefined,
          page,
          size: pageSize,
        },
      }).then((r) => r.data),
    enabled: !!entityId,
  })

  const invoices: any[] = Array.isArray(data) ? data : (data?.items ?? data?.invoices ?? [])
  const total: number = data?.total ?? invoices.length

  // Cost Centers & Projects (analytic accounting)
  const { data: ccData } = useQuery({
    queryKey: ['cost-centers', entityId],
    queryFn: () => api.get('/projects/cost-centers', { params: { entity_id: entityId } }).then((r) => r.data),
    enabled: !!entityId,
  })
  const costCenters: any[] = Array.isArray(ccData) ? ccData : (ccData?.items ?? [])

  const { data: projData } = useQuery({
    queryKey: ['projects-list', entityId],
    queryFn: () => api.get('/projects', { params: { entity_id: entityId, size: 100 } }).then((r) => r.data),
    enabled: !!entityId,
  })
  const projects: any[] = Array.isArray(projData) ? projData : (projData?.items ?? [])

  // COA (untuk datalist akun pendapatan per baris)
  const { data: coaData } = useQuery({
    queryKey: ['coa', entityId],
    queryFn: () => api.get('/coa/', { params: { entity_id: entityId, limit: 1000 } }).then((r) => r.data),
    enabled: !!entityId && showForm,
  })
  const accounts: any[] = Array.isArray(coaData) ? coaData : (coaData?.accounts ?? [])
  const revenueAccounts = accounts.filter((a: any) => !a.is_header && a.account_type === 'revenue')

  const lineTotalFcy = form.lines.reduce((s, l) => s + (parseFloat(l.amount) || 0), 0)
  const lineTotal = isFcy ? lineTotalFcy * exchangeRateNum : lineTotalFcy
  const linesValid = form.lines.length > 0 &&
    form.lines.every((l) => l.account_code && parseFloat(l.amount) > 0)

  const updateLine = (idx: number, patch: Partial<ARInvoiceLineForm>) => {
    setForm((f) => ({
      ...f,
      lines: f.lines.map((l, i) => (i === idx ? { ...l, ...patch } : l)),
    }))
  }
  const addLine = () => setForm((f) => ({ ...f, lines: [...f.lines, emptyLine()] }))
  const removeLine = (idx: number) => setForm((f) => ({ ...f, lines: f.lines.filter((_, i) => i !== idx) }))

  const createMutation = useMutation({
    mutationFn: () =>
      api.post('/ar/invoices', {
        entity_id: entityId,
        customer_name: form.customer_name,
        customer_npwp: form.customer_npwp || undefined,
        invoice_no: form.invoice_no,
        invoice_date: form.invoice_date,
        due_date: form.due_date || undefined,
        ppn_rate: parseInt(form.ppn_rate, 10),
        contract_ref: form.contract_ref || undefined,
        lines: form.lines
          .filter((l) => l.account_code && parseFloat(l.amount) > 0)
          .map((l) => {
            const fcy = parseFloat(l.amount) || 0
            return {
              account_code: l.account_code,
              description: l.description || undefined,
              amount: isFcy ? fcy * exchangeRateNum : fcy,
              cost_center: l.cost_center || undefined,
              project_id: l.project_id || undefined,
              amount_fcy: isFcy ? fcy : undefined,
            }
          }),
        currency: form.currency,
        exchange_rate: exchangeRateNum,
      }),
    onSuccess: () => {
      showToast(t('ar:createSuccess'))
      setShowForm(false)
      setForm(emptyForm)
      qc.invalidateQueries({ queryKey: ['ar-invoices'] })
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('ar:createFailed'), 'error'),
  })

  const postJournalMutation = useMutation({
    mutationFn: (invoiceId: string) => api.post(`/ar/invoices/${invoiceId}/post-journal`),
    onSuccess: () => {
      showToast(t('ar:postJournalSuccess'))
      qc.invalidateQueries({ queryKey: ['ar-invoices'] })
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('ar:postJournalFailed'), 'error'),
  })

  const filtered = invoices.filter((inv) =>
    !search ||
    inv.invoice_no?.toLowerCase().includes(search.toLowerCase()) ||
    inv.customer_name?.toLowerCase().includes(search.toLowerCase()),
  )

  const totalOutstanding = filtered.reduce(
    (s, i) => s + ((i.total_amount ?? 0) - (i.paid_amount ?? 0)), 0,
  )

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">{t('ar:invoicePageTitle')}</h1>
          <p className="text-sm text-gray-500 mt-0.5">{t('ar:invoicePageSubtitle')}</p>
        </div>
        <button onClick={() => setShowForm(true)} className="btn-primary">
          <Plus className="h-4 w-4" /> {t('ar:createInvoiceBtn')}
        </button>
      </div>

      {/* Create form */}
      {showForm && (
        <Card>
          <div className="flex items-center justify-between mb-4">
            <p className="text-sm font-semibold text-gray-700">{t('ar:newInvoiceFormTitle')}</p>
            <button onClick={() => setShowForm(false)}><X className="h-4 w-4 text-gray-400" /></button>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div>
              <label className="form-label">{t('ar:customerNameLabel')}</label>
              <input value={form.customer_name}
                onChange={(e) => setForm({ ...form, customer_name: e.target.value })}
                className="form-input" placeholder={t('ar:customerNamePlaceholder')} />
            </div>
            <div>
              <label className="form-label">{t('ar:customerNpwpLabel')}</label>
              <input value={form.customer_npwp}
                onChange={(e) => setForm({ ...form, customer_npwp: e.target.value })}
                className="form-input" placeholder={t('common:optional')} />
            </div>
            <div>
              <label className="form-label">{t('ar:invoiceNoLabel')}</label>
              <input value={form.invoice_no}
                onChange={(e) => setForm({ ...form, invoice_no: e.target.value })}
                className="form-input" placeholder={t('ar:invoiceNoPlaceholder')} />
            </div>
            <div>
              <label className="form-label">{t('ar:contractRefLabel')}</label>
              <input value={form.contract_ref}
                onChange={(e) => setForm({ ...form, contract_ref: e.target.value })}
                className="form-input" placeholder={t('common:optional')} />
            </div>
            <div>
              <label className="form-label">{t('ar:invoiceDateLabel')}</label>
              <input type="date" value={form.invoice_date}
                onChange={(e) => setForm({ ...form, invoice_date: e.target.value })}
                className="form-input" />
            </div>
            <div>
              <label className="form-label">{t('ar:dueDateLabel')}</label>
              <input type="date" value={form.due_date}
                onChange={(e) => setForm({ ...form, due_date: e.target.value })}
                className="form-input" />
            </div>
            <div>
              <label className="form-label">{t('ar:ppnRateLabel')}</label>
              <select value={form.ppn_rate}
                onChange={(e) => setForm({ ...form, ppn_rate: e.target.value })}
                className="form-select">
                <option value="11">11%</option>
                <option value="0">0%</option>
              </select>
            </div>
            <div>
              <label className="form-label">{t('multicurrency:invoice_currencyLabel')}</label>
              <CurrencySelect value={form.currency} onChange={(c) => setForm({ ...form, currency: c })} />
            </div>
            {isFcy && (
              <div>
                <label className="form-label">{t('multicurrency:invoice_exchangeRateLabel')}</label>
                <input type="number" value={form.exchange_rate}
                  onChange={(e) => setForm({ ...form, exchange_rate: e.target.value })}
                  min={0} step="0.000001" className="form-input" />
              </div>
            )}
          </div>

          {/* Rincian Pendapatan — multi-baris (account + cost center + project per baris) */}
          <div className="mt-4 border border-gray-200 rounded-lg overflow-hidden">
            <div className="px-3 py-2 bg-gray-50 border-b border-gray-200">
              <p className="text-sm font-semibold text-gray-700">{t('ar:revenueLinesTitle')}</p>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-xs text-gray-500 border-b border-gray-100">
                    <th className="text-left font-medium px-3 py-2 min-w-40">{t('ar:coaRevenueLabel')}</th>
                    <th className="text-left font-medium px-3 py-2 min-w-32">{t('common:description')}</th>
                    <th className="text-left font-medium px-3 py-2 min-w-32">{t('ar:costCenterLabel')}</th>
                    <th className="text-left font-medium px-3 py-2 min-w-32">{t('ar:projectLabel')}</th>
                    <th className="text-right font-medium px-3 py-2 min-w-32">
                      {isFcy ? t('multicurrency:invoice_fcyAmountLabel') : t('ar:lineAmountLabel')}
                    </th>
                    {isFcy && (
                      <th className="text-right font-medium px-3 py-2 min-w-32">{t('multicurrency:invoice_idrEquivalentLabel')}</th>
                    )}
                    <th className="px-2 py-2 w-8"></th>
                  </tr>
                </thead>
                <tbody>
                  {form.lines.map((line, idx) => (
                    <tr key={idx} className="border-b border-gray-50 last:border-0">
                      <td className="px-3 py-1.5">
                        <input
                          list={`coa-revenue-list-${idx}`}
                          value={line.account_code}
                          onChange={(e) => updateLine(idx, { account_code: e.target.value })}
                          className="form-input font-mono text-xs py-1" placeholder="4-1-001"
                        />
                        <datalist id={`coa-revenue-list-${idx}`}>
                          {revenueAccounts.map((a: any) => (
                            <option key={a.account_code} value={a.account_code}>{a.account_name}</option>
                          ))}
                        </datalist>
                      </td>
                      <td className="px-3 py-1.5">
                        <input
                          value={line.description}
                          onChange={(e) => updateLine(idx, { description: e.target.value })}
                          className="form-input text-xs py-1" placeholder={t('common:optional')}
                        />
                      </td>
                      <td className="px-3 py-1.5">
                        <select value={line.cost_center}
                          onChange={(e) => updateLine(idx, { cost_center: e.target.value })}
                          className="form-select text-xs py-1">
                          <option value="">—</option>
                          {costCenters.map((cc: any) => (
                            <option key={cc.cc_code} value={cc.cc_code}>{cc.cc_name}</option>
                          ))}
                        </select>
                      </td>
                      <td className="px-3 py-1.5">
                        <select value={line.project_id}
                          onChange={(e) => updateLine(idx, { project_id: e.target.value })}
                          className="form-select text-xs py-1">
                          <option value="">—</option>
                          {projects.map((p: any) => (
                            <option key={p.id} value={p.id}>{p.project_name}</option>
                          ))}
                        </select>
                      </td>
                      <td className="px-3 py-1.5">
                        <input type="number" value={line.amount}
                          onChange={(e) => updateLine(idx, { amount: e.target.value })}
                          className="form-input text-xs py-1 text-right" placeholder="0" />
                      </td>
                      {isFcy && (
                        <td className="px-3 py-1.5 text-right text-xs text-gray-400">
                          Rp {formatRupiah((parseFloat(line.amount) || 0) * exchangeRateNum)}
                        </td>
                      )}
                      <td className="px-2 py-1.5 text-center">
                        <button type="button" onClick={() => removeLine(idx)}
                          disabled={form.lines.length <= 1}
                          className="text-gray-400 hover:text-red-500 disabled:opacity-30 disabled:hover:text-gray-400">
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr className="bg-gray-50">
                    <td colSpan={4} className="px-3 py-2">
                      <button type="button" onClick={addLine}
                        className="inline-flex items-center gap-1 text-xs text-primary-600 hover:text-primary-700 font-medium">
                        <Plus className="h-3.5 w-3.5" /> {t('ar:addLineBtn')}
                      </button>
                    </td>
                    <td className="px-3 py-2 text-right text-sm font-semibold text-gray-800">
                      {isFcy ? formatCurrency(lineTotalFcy, form.currency) : `Rp ${formatRupiah(lineTotal)}`}
                    </td>
                    {isFcy && (
                      <td className="px-3 py-2 text-right text-sm font-semibold text-gray-500">
                        Rp {formatRupiah(lineTotal)}
                      </td>
                    )}
                    <td></td>
                  </tr>
                </tfoot>
              </table>
            </div>
          </div>

          <div className="flex gap-3 mt-4">
            <button onClick={() => createMutation.mutate()}
              disabled={createMutation.isPending || !form.customer_name || !form.invoice_no || !linesValid}
              className="btn-primary">
              {createMutation.isPending ? t('ar:savingBtn') : t('ar:saveInvoiceBtn')}
            </button>
            <button onClick={() => setShowForm(false)} className="btn-secondary">{t('ar:cancelBtn')}</button>
          </div>
        </Card>
      )}

      {/* Summary cards */}
      <div className="grid grid-cols-3 gap-4">
        {[
          { label: t('ar:summaryTotalInvoice'), value: filtered.length, isCurrency: false },
          { label: t('ar:summaryTotalBilled'), value: filtered.reduce((s, i) => s + (i.total_amount ?? 0), 0), isCurrency: true },
          { label: t('ar:summaryOutstanding'), value: totalOutstanding, isCurrency: true },
        ].map((item) => (
          <div key={item.label} className="card p-4">
            <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">{item.label}</p>
            <p className="text-xl font-bold text-gray-900 mt-1">
              {item.isCurrency ? `Rp ${formatRupiah(item.value as number)}` : item.value}
            </p>
          </div>
        ))}
      </div>

      <Card noPad>
        <CardHeader
          title={t('ar:invoiceListTitle')}
          subtitle={t('ar:invoiceListSubtitle', { count: filtered.length })}
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
              placeholder={t('ar:searchPlaceholder')}
              className="form-input pl-9"
            />
          </div>
          <div className="flex items-center gap-2">
            <Filter className="h-4 w-4 text-gray-400" />
            <select value={status} onChange={(e) => { setStatus(e.target.value); setPage(1) }}
              className="form-select w-40">
              {STATUS_OPTS.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
            </select>
          </div>
        </div>

        {isLoading ? (
          <div className="flex justify-center py-16"><Spinner size="lg" /></div>
        ) : filtered.length === 0 ? (
          <EmptyState title={t('ar:noInvoices')} />
        ) : (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>{t('ar:colInvoiceNo')}</th>
                  <th>{t('ar:colCustomer')}</th>
                  <th>{t('ar:colInvoiceDate')}</th>
                  <th>{t('ar:colDueDate')}</th>
                  <th className="right">{t('ar:colTotal')}</th>
                  <th className="right">{t('ar:colPaid')}</th>
                  <th className="right">{t('ar:colRemaining')}</th>
                  <th>{t('ar:colStatus')}</th>
                  <th>{t('ar:colAction')}</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((inv) => {
                  const sisa = (inv.total_amount ?? 0) - (inv.paid_amount ?? 0)
                  const isOverdue = sisa > 0 && inv.due_date && new Date(inv.due_date) < new Date()
                  return (
                    <tr key={inv.id}>
                      <td className="font-mono text-sm font-medium text-primary-600">
                        {inv.invoice_no ?? inv.invoice_number}
                      </td>
                      <td className="text-sm">{inv.customer_name ?? inv.customer_id}</td>
                      <td className="text-sm text-gray-500">{formatDate(inv.invoice_date)}</td>
                      <td className={`text-sm ${isOverdue ? 'text-red-600 font-medium' : 'text-gray-500'}`}>
                        {formatDate(inv.due_date)}
                      </td>
                      <td className="right">
                        Rp {formatRupiah(inv.total_amount)}
                        {inv.currency && inv.currency !== 'IDR' && (
                          <div className="text-xs text-gray-400">{formatCurrency(inv.amount_fcy, inv.currency)}</div>
                        )}
                      </td>
                      <td className="right text-green-600">Rp {formatRupiah(inv.paid_amount)}</td>
                      <td className={`right font-semibold ${sisa > 0 ? 'text-amber-600' : 'text-gray-400'}`}>
                        Rp {formatRupiah(sisa)}
                      </td>
                      <td>
                        <Badge
                          status={isOverdue && sisa > 0 ? 'overdue' : (inv.status ?? 'draft')}
                        />
                      </td>
                      <td>
                        {inv.status === 'draft' && (
                          <button
                            onClick={() => postJournalMutation.mutate(inv.id)}
                            disabled={postJournalMutation.isPending}
                            className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-md bg-primary-50 text-primary-700 hover:bg-primary-100">
                            <Send className="h-3 w-3" /> {t('ar:postJournalBtn')}
                          </button>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}

        {/* Pagination */}
        {total > pageSize && (
          <div className="px-6 py-3 border-t border-gray-100 flex items-center justify-between text-sm text-gray-500">
            <span>{t('ar:paginationPage', { page, total: Math.ceil(total / pageSize) })}</span>
            <div className="flex gap-2">
              <button disabled={page === 1} onClick={() => setPage(p => p - 1)} className="btn-secondary py-1">
                {t('ar:paginationPrev')}
              </button>
              <button disabled={page * pageSize >= total} onClick={() => setPage(p => p + 1)} className="btn-secondary py-1">
                {t('ar:paginationNext')}
              </button>
            </div>
          </div>
        )}
      </Card>
    </div>
  )
}
