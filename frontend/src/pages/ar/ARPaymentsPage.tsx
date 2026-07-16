import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { RefreshCw, DollarSign, X, AlertTriangle, CheckCircle } from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext'
import api from '../../lib/api'
import { Card, CardHeader } from '../../components/ui/Card'
import Spinner from '../../components/ui/Spinner'
import EmptyState from '../../components/ui/EmptyState'
import Badge from '../../components/ui/Badge'
import { useLatestRate } from '../../lib/currency'
import { formatRupiah, formatCurrency, formatDate, todayISO } from '../../lib/utils'
import { showToast } from '../../components/ui/Toast'

export default function ARPaymentsPage() {
  const { t } = useTranslation(['ar', 'common', 'multicurrency'])
  const { entityId } = useAuth()
  const qc = useQueryClient()
  const [tab, setTab] = useState<'outstanding' | 'receipts' | 'aging'>('outstanding')

  // ── Outstanding AR invoices ──────────────────────────────────────────────────
  const { data: outData, isLoading: outLoading, refetch: refetchOut } = useQuery({
    queryKey: ['ar-invoices-outstanding', entityId],
    queryFn: () =>
      api.get(`/ar/invoices/${entityId}`, {
        params: { status: 'draft' },  // also fetch partial below
      }).then(r => r.data),
    enabled: !!entityId,
  })
  // Include both draft (posted) and partial
  const { data: partialData } = useQuery({
    queryKey: ['ar-invoices-partial', entityId],
    queryFn: () =>
      api.get(`/ar/invoices/${entityId}`, { params: { status: 'partial' } }).then(r => r.data),
    enabled: !!entityId,
  })
  const outstanding: any[] = [
    ...(Array.isArray(outData) ? outData : []),
    ...(Array.isArray(partialData) ? partialData : []),
  ].filter(i => (i.outstanding ?? 0) > 0)

  // ── Receipts ─────────────────────────────────────────────────────────────────
  const { data: receiptsData, isLoading: rcpLoading, refetch: refetchRcp } = useQuery({
    queryKey: ['ar-receipts', entityId],
    queryFn: () => api.get(`/ar/receipts/${entityId}`).then(r => r.data),
    enabled: !!entityId && tab === 'receipts',
  })
  const receipts: any[] = Array.isArray(receiptsData) ? receiptsData : []

  // ── Aging ────────────────────────────────────────────────────────────────────
  const { data: agingData, isLoading: agingLoading } = useQuery({
    queryKey: ['ar-aging', entityId],
    queryFn: () => api.get(`/ar/aging/${entityId}`).then(r => r.data),
    enabled: !!entityId && tab === 'aging',
  })
  const aging: any[] = Array.isArray(agingData) ? agingData : []

  // ── Receipt form ─────────────────────────────────────────────────────────────
  const [receiptInvoice, setReceiptInvoice] = useState<any>(null)
  const [receiptForm, setReceiptForm] = useState({
    receipt_date: todayISO(),
    amount: '',
    bank_account: '1-1-001',
    reference_no: '',
    pph_withheld: '0',
    pph_type: 'PPh23',
    amount_fcy: '',
    payment_rate: '',
  })

  const isFcyReceipt = receiptInvoice?.currency && receiptInvoice.currency !== 'IDR'
  const invoiceRate = receiptInvoice?.exchange_rate ? Number(receiptInvoice.exchange_rate) : 1
  const { rate: latestPayRate } = useLatestRate(receiptInvoice?.currency ?? 'IDR')
  useEffect(() => {
    if (isFcyReceipt && latestPayRate != null && !receiptForm.payment_rate) {
      setReceiptForm((f) => ({ ...f, payment_rate: String(latestPayRate) }))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isFcyReceipt, latestPayRate])

  const estimatedRealizedGl = isFcyReceipt
    ? (parseFloat(receiptForm.amount_fcy) || 0) * ((parseFloat(receiptForm.payment_rate) || invoiceRate) - invoiceRate)
    : 0

  const openReceiptForm = (inv: any) => {
    setReceiptInvoice(inv)
    const outstandingIdr = inv.outstanding ?? inv.total_amount - inv.paid_amount
    const fcyOutstanding = inv.currency && inv.currency !== 'IDR' && inv.exchange_rate
      ? (outstandingIdr / Number(inv.exchange_rate)).toFixed(2)
      : ''
    setReceiptForm(f => ({
      ...f,
      amount: String(outstandingIdr),
      amount_fcy: fcyOutstanding,
      payment_rate: '',
      pph_withheld: '0',
    }))
  }

  const receiptMutation = useMutation({
    mutationFn: () =>
      api.post('/ar/receipts', {
        entity_id:    entityId,
        invoice_id:   receiptInvoice.id,
        receipt_date: receiptForm.receipt_date,
        amount:       parseFloat(receiptForm.amount) || 0,
        bank_account: receiptForm.bank_account,
        reference_no: receiptForm.reference_no || undefined,
        pph_withheld: parseFloat(receiptForm.pph_withheld) || 0,
        pph_type:     receiptForm.pph_type,
        received_by:  'user',
        amount_fcy:   isFcyReceipt ? (parseFloat(receiptForm.amount_fcy) || 0) : undefined,
        payment_rate: isFcyReceipt ? (parseFloat(receiptForm.payment_rate) || 0) : undefined,
      }),
    onSuccess: (res) => {
      showToast(t('ar:receiptSuccess', { status: res.data.invoice_status === 'paid' ? t('ar:invoicePaidStatus') : t('ar:invoicePartialStatus') }))
      setReceiptInvoice(null)
      qc.invalidateQueries({ queryKey: ['ar-invoices-outstanding', entityId] })
      qc.invalidateQueries({ queryKey: ['ar-invoices-partial', entityId] })
      qc.invalidateQueries({ queryKey: ['ar-receipts', entityId] })
      qc.invalidateQueries({ queryKey: ['ar-aging', entityId] })
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('ar:receiptFailed'), 'error'),
  })

  // ── Summaries ────────────────────────────────────────────────────────────────
  const totalOutstanding  = outstanding.reduce((s, i) => s + (i.outstanding ?? 0), 0)
  const totalReceipts     = receipts.reduce((s, r)  => s + (r.amount ?? 0), 0)
  const overdueCount      = outstanding.filter(i => {
    if (!i.due_date) return false
    return new Date(i.due_date) < new Date()
  }).length

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">{t('ar:paymentsPageTitle')}</h1>
          <p className="text-sm text-gray-500 mt-0.5">{t('ar:paymentsPageSubtitle')}</p>
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-3 gap-4">
        <div className={`card p-4 ${overdueCount > 0 ? 'border-l-4 border-red-400' : ''}`}>
          <p className="text-xs text-gray-500 uppercase">{t('ar:statTotalOutstanding')}</p>
          <p className="text-xl font-bold text-gray-900 mt-1">Rp {formatRupiah(totalOutstanding)}</p>
          {overdueCount > 0 && (
            <p className="text-xs text-red-500 mt-0.5 flex items-center gap-1">
              <AlertTriangle className="h-3 w-3" /> {t('ar:statOverdueCount', { count: overdueCount })}
            </p>
          )}
        </div>
        <div className="card p-4">
          <p className="text-xs text-gray-500 uppercase">{t('ar:statOutstandingInvoices')}</p>
          <p className="text-2xl font-bold text-gray-900 mt-1">{outstanding.length}</p>
        </div>
        <div className="card p-4">
          <p className="text-xs text-gray-500 uppercase">{t('ar:statTotalReceived')}</p>
          <p className="text-xl font-bold text-green-700 mt-1">Rp {formatRupiah(totalReceipts)}</p>
        </div>
      </div>

      {/* Tabs */}
      <div className="border-b border-gray-200">
        <nav className="flex gap-1">
          {[
            { key: 'outstanding', label: t('ar:tabOutstanding') },
            { key: 'receipts',    label: t('ar:tabReceiptHistory') },
            { key: 'aging',       label: t('ar:tabAging') },
          ].map(tb => (
            <button key={tb.key}
              onClick={() => setTab(tb.key as any)}
              className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                tab === tb.key
                  ? 'border-primary-600 text-primary-700'
                  : 'border-transparent text-gray-500 hover:text-gray-700'
              }`}>
              {tb.label}
            </button>
          ))}
        </nav>
      </div>

      {/* Tab: Outstanding */}
      {tab === 'outstanding' && (
        <Card noPad>
          <CardHeader
            title={t('ar:outstandingTableTitle')}
            subtitle={t('ar:outstandingTableSubtitle', { count: outstanding.length, amount: formatRupiah(totalOutstanding) })}
            actions={<button onClick={() => refetchOut()} className="btn-secondary"><RefreshCw className="h-4 w-4" /></button>}
          />
          {outLoading ? (
            <div className="flex justify-center py-16"><Spinner size="lg" /></div>
          ) : outstanding.length === 0 ? (
            <EmptyState title={t('ar:emptyOutstandingTitle')} description={t('ar:emptyOutstandingDescription')} />
          ) : (
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>{t('ar:colInvoiceNo')}</th>
                    <th>{t('ar:colCustomerName')}</th>
                    <th>{t('ar:colInvoiceDate')}</th>
                    <th>{t('ar:colDueDate')}</th>
                    <th className="right">{t('ar:colTotal')}</th>
                    <th className="right">{t('ar:colPaid')}</th>
                    <th className="right">{t('ar:colOutstanding')}</th>
                    <th>{t('ar:colStatus')}</th>
                    <th>{t('ar:colAction')}</th>
                  </tr>
                </thead>
                <tbody>
                  {outstanding.map((inv: any) => {
                    const overdue = inv.due_date && new Date(inv.due_date) < new Date()
                    const outAmt  = inv.outstanding ?? (inv.total_amount - inv.paid_amount)
                    return (
                      <tr key={inv.id} className={overdue ? 'bg-red-50/40' : ''}>
                        <td className="font-mono text-xs">{inv.invoice_no}</td>
                        <td className="text-sm font-medium">{inv.customer_name}</td>
                        <td className="text-sm text-gray-500 whitespace-nowrap">{formatDate(inv.invoice_date)}</td>
                        <td className="whitespace-nowrap">
                          <span className={`text-sm ${overdue ? 'text-red-600 font-medium' : 'text-gray-500'}`}>
                            {inv.due_date ? formatDate(inv.due_date) : '—'}
                          </span>
                          {overdue && <span className="ml-1 text-xs text-red-500">{t('ar:lateLabel')}</span>}
                        </td>
                        <td className="right">Rp {formatRupiah(inv.total_amount)}</td>
                        <td className="right text-green-700">Rp {formatRupiah(inv.paid_amount)}</td>
                        <td className="right font-semibold text-red-600">Rp {formatRupiah(outAmt)}</td>
                        <td><Badge status={inv.status} /></td>
                        <td>
                          <button
                            onClick={() => openReceiptForm(inv)}
                            className="inline-flex items-center gap-1 text-xs px-2 py-1 bg-green-50 text-green-700 hover:bg-green-100 rounded-md">
                            <DollarSign className="h-3 w-3" /> {t('ar:receivePaymentBtn')}
                          </button>
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

      {/* Tab: Receipts history */}
      {tab === 'receipts' && (
        <Card noPad>
          <CardHeader
            title={t('ar:receiptHistoryTitle')}
            subtitle={t('ar:receiptHistorySubtitle', { count: receipts.length })}
            actions={<button onClick={() => refetchRcp()} className="btn-secondary"><RefreshCw className="h-4 w-4" /></button>}
          />
          {rcpLoading ? (
            <div className="flex justify-center py-16"><Spinner size="lg" /></div>
          ) : receipts.length === 0 ? (
            <EmptyState title={t('ar:emptyReceiptsTitle')} />
          ) : (
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>{t('ar:colDate')}</th>
                    <th>{t('ar:colInvoice')}</th>
                    <th>{t('ar:colCustomerName')}</th>
                    <th>{t('ar:colReference')}</th>
                    <th>{t('ar:colBankAccount')}</th>
                    <th className="right">{t('ar:colAmountReceived')}</th>
                    <th>{t('ar:colJournal')}</th>
                  </tr>
                </thead>
                <tbody>
                  {receipts.map((r: any) => (
                    <tr key={r.id}>
                      <td className="text-sm text-gray-500 whitespace-nowrap">{formatDate(r.receipt_date)}</td>
                      <td className="font-mono text-xs">{r.invoice_no}</td>
                      <td className="text-sm">{r.customer_name}</td>
                      <td className="text-xs text-gray-400">{r.reference_no ?? '—'}</td>
                      <td className="font-mono text-xs text-gray-400">{r.bank_account}</td>
                      <td className="right font-semibold text-green-700">
                        Rp {formatRupiah(r.amount)}
                        {r.currency && r.currency !== 'IDR' && (
                          <div className="text-xs text-gray-400 font-normal">{formatCurrency(r.amount_fcy, r.currency)}</div>
                        )}
                        {r.realized_gl != null && (
                          <div className={`text-xs font-normal ${r.realized_gl > 0 ? 'text-green-600' : 'text-red-600'}`}>
                            {r.realized_gl > 0 ? '+' : ''}Rp {formatRupiah(r.realized_gl)}
                          </div>
                        )}
                      </td>
                      <td className="font-mono text-xs text-gray-400">{r.journal_id?.slice(0, 8)}</td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr className="bg-gray-50 font-semibold">
                    <td colSpan={5} className="pl-4 py-2 text-sm text-gray-600">{t('ar:totalReceivedFooter')}</td>
                    <td className="right pr-4 text-green-700">Rp {formatRupiah(totalReceipts)}</td>
                    <td />
                  </tr>
                </tfoot>
              </table>
            </div>
          )}
        </Card>
      )}

      {/* Tab: Aging */}
      {tab === 'aging' && (
        <Card noPad>
          <CardHeader title={t('ar:agingTitle')} subtitle={t('ar:agingSubtitle')} />
          {agingLoading ? (
            <div className="flex justify-center py-16"><Spinner size="lg" /></div>
          ) : aging.length === 0 ? (
            <EmptyState title={t('ar:emptyAgingTitle')} />
          ) : (
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>{t('ar:colCustomerName')}</th>
                    <th className="right">{t('ar:colInvoiceCount')}</th>
                    <th className="right">{t('ar:colCurrent')}</th>
                    <th className="right">{t('ar:colDays1to30')}</th>
                    <th className="right">{t('ar:colDays31to60')}</th>
                    <th className="right">{t('ar:colDays61to90')}</th>
                    <th className="right">{t('ar:colOver90')}</th>
                    <th className="right">{t('ar:colTotalOutstanding')}</th>
                  </tr>
                </thead>
                <tbody>
                  {aging.map((a: any, i: number) => (
                    <tr key={i}>
                      <td className="text-sm font-medium">{a.customer_name}</td>
                      <td className="right text-sm">{a.invoice_count}</td>
                      <td className="right text-sm text-green-700">{a.current_amount > 0 ? `Rp ${formatRupiah(a.current_amount)}` : '—'}</td>
                      <td className="right text-sm text-yellow-600">{a.days_1_30 > 0 ? `Rp ${formatRupiah(a.days_1_30)}` : '—'}</td>
                      <td className="right text-sm text-orange-600">{a.days_31_60 > 0 ? `Rp ${formatRupiah(a.days_31_60)}` : '—'}</td>
                      <td className="right text-sm text-red-500">{a.days_61_90 > 0 ? `Rp ${formatRupiah(a.days_61_90)}` : '—'}</td>
                      <td className="right text-sm font-semibold text-red-700">{a.over_90 > 0 ? `Rp ${formatRupiah(a.over_90)}` : '—'}</td>
                      <td className="right font-bold">Rp {formatRupiah(a.total_outstanding)}</td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr className="bg-gray-50 font-semibold">
                    <td className="pl-4 py-2 text-sm">{t('ar:totalFooter')}</td>
                    <td className="right pr-4">{aging.reduce((s, a) => s + a.invoice_count, 0)}</td>
                    <td className="right pr-4 text-green-700">Rp {formatRupiah(aging.reduce((s, a) => s + (a.current_amount ?? 0), 0))}</td>
                    <td className="right pr-4 text-yellow-600">Rp {formatRupiah(aging.reduce((s, a) => s + (a.days_1_30 ?? 0), 0))}</td>
                    <td className="right pr-4 text-orange-600">Rp {formatRupiah(aging.reduce((s, a) => s + (a.days_31_60 ?? 0), 0))}</td>
                    <td className="right pr-4 text-red-500">Rp {formatRupiah(aging.reduce((s, a) => s + (a.days_61_90 ?? 0), 0))}</td>
                    <td className="right pr-4 text-red-700">Rp {formatRupiah(aging.reduce((s, a) => s + (a.over_90 ?? 0), 0))}</td>
                    <td className="right pr-4">Rp {formatRupiah(aging.reduce((s, a) => s + (a.total_outstanding ?? 0), 0))}</td>
                  </tr>
                </tfoot>
              </table>
            </div>
          )}
        </Card>
      )}

      {/* Receipt form modal */}
      {receiptInvoice && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-xl w-full max-w-lg">
            <div className="flex items-center justify-between p-4 border-b">
              <div>
                <p className="font-semibold text-gray-900">{t('ar:receiptModalTitle')}</p>
                <p className="text-sm text-gray-500 mt-0.5">
                  {receiptInvoice.invoice_no} — {receiptInvoice.customer_name}
                </p>
              </div>
              <button onClick={() => setReceiptInvoice(null)} className="p-1.5 hover:bg-gray-100 rounded-lg">
                <X className="h-5 w-5 text-gray-400" />
              </button>
            </div>

            <div className="p-4 space-y-3">
              {/* Invoice summary */}
              <div className="bg-gray-50 rounded-lg p-3 text-sm grid grid-cols-2 gap-2">
                <div>
                  <p className="text-gray-500 text-xs">{t('ar:invoiceTotalLabel')}</p>
                  <p className="font-medium">Rp {formatRupiah(receiptInvoice.total_amount)}</p>
                </div>
                <div>
                  <p className="text-gray-500 text-xs">{t('ar:colOutstanding')}</p>
                  <p className="font-semibold text-red-600">Rp {formatRupiah(receiptInvoice.outstanding ?? (receiptInvoice.total_amount - receiptInvoice.paid_amount))}</p>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="form-label">{t('ar:receiptDateLabel')}</label>
                  <input type="date" value={receiptForm.receipt_date}
                    onChange={e => setReceiptForm({ ...receiptForm, receipt_date: e.target.value })}
                    className="form-input" />
                </div>
                <div>
                  <label className="form-label">{t('ar:bankAccountLabel')}</label>
                  <input value={receiptForm.bank_account}
                    onChange={e => setReceiptForm({ ...receiptForm, bank_account: e.target.value })}
                    className="form-input font-mono text-sm" placeholder="1-1-001" />
                </div>
                {isFcyReceipt ? (
                  <>
                    <div>
                      <label className="form-label">
                        {t('multicurrency:payment_amountFcyLabel')} ({receiptInvoice.currency})
                      </label>
                      <input type="number" value={receiptForm.amount_fcy}
                        onChange={e => setReceiptForm({ ...receiptForm, amount_fcy: e.target.value })}
                        className="form-input" />
                    </div>
                    <div>
                      <label className="form-label">{t('multicurrency:payment_paymentRateLabel')}</label>
                      <input type="number" value={receiptForm.payment_rate}
                        onChange={e => setReceiptForm({ ...receiptForm, payment_rate: e.target.value })}
                        min={0} step="0.000001" className="form-input" />
                      <p className="text-xs text-gray-400 mt-1">
                        {t('multicurrency:invoice_exchangeRateLabel')}: {invoiceRate}
                      </p>
                    </div>
                  </>
                ) : (
                  <div>
                    <label className="form-label">{t('ar:amountReceivedLabel')}</label>
                    <input type="number" value={receiptForm.amount}
                      onChange={e => setReceiptForm({ ...receiptForm, amount: e.target.value })}
                      className="form-input" />
                  </div>
                )}
                <div>
                  <label className="form-label">{t('ar:referenceNoLabel')}</label>
                  <input value={receiptForm.reference_no}
                    onChange={e => setReceiptForm({ ...receiptForm, reference_no: e.target.value })}
                    className="form-input" placeholder={t('ar:referenceNoPlaceholder')} />
                </div>
                <div>
                  <label className="form-label">{t('ar:pphWithheldLabel')}</label>
                  <input type="number" value={receiptForm.pph_withheld}
                    onChange={e => setReceiptForm({ ...receiptForm, pph_withheld: e.target.value })}
                    className="form-input" placeholder="0" />
                </div>
                <div>
                  <label className="form-label">{t('ar:pphTypeLabel')}</label>
                  <select value={receiptForm.pph_type}
                    onChange={e => setReceiptForm({ ...receiptForm, pph_type: e.target.value })}
                    className="form-select">
                    <option value="PPh23">PPh 23</option>
                    <option value="PPh4_2">PPh 4(2)</option>
                    <option value="PPh21">PPh 21</option>
                  </select>
                </div>
              </div>

              {isFcyReceipt && Math.abs(estimatedRealizedGl) >= 1 && (
                <div className={`rounded-lg p-2.5 text-xs ${estimatedRealizedGl > 0 ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>
                  {estimatedRealizedGl > 0 ? t('multicurrency:payment_realizedGainLabel') : t('multicurrency:payment_realizedLossLabel')}:{' '}
                  Rp {formatRupiah(Math.abs(estimatedRealizedGl))}
                </div>
              )}

              {/* Total cleared preview */}
              <div className="bg-blue-50 rounded-lg p-3 text-sm">
                <div className="flex justify-between">
                  <span className="text-gray-600">{t('ar:cashReceivedLabel')}</span>
                  <span>
                    {isFcyReceipt
                      ? formatCurrency(parseFloat(receiptForm.amount_fcy) || 0, receiptInvoice.currency)
                      : `Rp ${formatRupiah(parseFloat(receiptForm.amount) || 0)}`}
                  </span>
                </div>
                {parseFloat(receiptForm.pph_withheld) > 0 && (
                  <div className="flex justify-between">
                    <span className="text-gray-600">{t('ar:pphWithheldPreviewLabel')}</span>
                    <span>Rp {formatRupiah(parseFloat(receiptForm.pph_withheld) || 0)}</span>
                  </div>
                )}
                <div className="flex justify-between font-semibold border-t border-blue-200 pt-1.5 mt-1.5">
                  <span>{t('ar:totalClearsLabel')}</span>
                  <span className="text-primary-700">
                    Rp {formatRupiah(
                      (isFcyReceipt ? (parseFloat(receiptForm.amount_fcy) || 0) * invoiceRate : (parseFloat(receiptForm.amount) || 0))
                      + (parseFloat(receiptForm.pph_withheld) || 0)
                    )}
                  </span>
                </div>
              </div>
            </div>

            <div className="flex gap-3 px-4 pb-4">
              <button onClick={() => receiptMutation.mutate()}
                disabled={receiptMutation.isPending || (isFcyReceipt ? (!receiptForm.amount_fcy || !receiptForm.payment_rate) : !receiptForm.amount)}
                className="btn-primary flex-1">
                <CheckCircle className="h-4 w-4" />
                {receiptMutation.isPending ? t('ar:savingBtn') : t('ar:recordReceiptBtn')}
              </button>
              <button onClick={() => setReceiptInvoice(null)} className="btn-secondary">{t('ar:cancelBtn')}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
