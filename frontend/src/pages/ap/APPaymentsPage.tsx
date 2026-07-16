import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { RefreshCw, DollarSign, X, AlertTriangle, CheckCircle, Send } from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext'
import api from '../../lib/api'
import { Card, CardHeader } from '../../components/ui/Card'
import Spinner from '../../components/ui/Spinner'
import EmptyState from '../../components/ui/EmptyState'
import Badge from '../../components/ui/Badge'
import { useLatestRate } from '../../lib/currency'
import { formatRupiah, formatCurrency, formatDate, todayISO, currentYear, currentMonth, MONTHS } from '../../lib/utils'
import { showToast } from '../../components/ui/Toast'

export default function APPaymentsPage() {
  const { t } = useTranslation(['ap', 'common', 'multicurrency'])
  const { entityId } = useAuth()
  const qc = useQueryClient()
  const [tab, setTab] = useState<'outstanding' | 'payments' | 'pph'>('outstanding')

  // ── Outstanding AP invoices ──────────────────────────────────────────────────
  const { data: outData, isLoading: outLoading, refetch: refetchOut } = useQuery({
    queryKey: ['ap-outstanding', entityId],
    queryFn: () =>
      api.get(`/ap/invoices/${entityId}/outstanding`).then(r => r.data),
    enabled: !!entityId,
  })
  const outstanding: any[] = Array.isArray(outData) ? outData : []

  // ── Payment history ──────────────────────────────────────────────────────────
  const { data: paymentsData, isLoading: pLoading, refetch: refetchPay } = useQuery({
    queryKey: ['ap-payments', entityId],
    queryFn: () => api.get(`/ap/payments/${entityId}`).then(r => r.data),
    enabled: !!entityId && tab === 'payments',
  })
  const payments: any[] = Array.isArray(paymentsData) ? paymentsData : []

  // ── Payment form ─────────────────────────────────────────────────────────────
  const [payInvoice, setPayInvoice] = useState<any>(null)
  const [payForm, setPayForm] = useState({
    payment_date: todayISO(),
    amount: '',
    bank_account: '1-1-001',
    reference_no: '',
    notes: '',
    amount_fcy: '',
    payment_rate: '',
  })

  const isFcyPayment = payInvoice?.currency && payInvoice.currency !== 'IDR'
  const invoiceRate = payInvoice?.exchange_rate ? Number(payInvoice.exchange_rate) : 1
  const { rate: latestPayRate } = useLatestRate(payInvoice?.currency ?? 'IDR')
  useEffect(() => {
    if (isFcyPayment && latestPayRate != null && !payForm.payment_rate) {
      setPayForm((f) => ({ ...f, payment_rate: String(latestPayRate) }))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isFcyPayment, latestPayRate])

  const estimatedRealizedGl = isFcyPayment
    ? (parseFloat(payForm.amount_fcy) || 0) * (invoiceRate - (parseFloat(payForm.payment_rate) || invoiceRate))
    : 0

  const openPayForm = (inv: any) => {
    setPayInvoice(inv)
    const fcyOutstanding = inv.currency && inv.currency !== 'IDR' && inv.exchange_rate
      ? ((inv.ap_outstanding ?? 0) / Number(inv.exchange_rate)).toFixed(2)
      : ''
    setPayForm(f => ({
      ...f,
      amount: String(inv.ap_outstanding ?? 0),
      amount_fcy: fcyOutstanding,
      payment_rate: '',
      reference_no: '',
    }))
  }

  const payMutation = useMutation({
    mutationFn: () =>
      api.post(`/ap/invoices/${payInvoice.id}/pay`, {
        entity_id:    entityId,
        payment_date: payForm.payment_date,
        amount:       parseFloat(payForm.amount) || 0,
        bank_account: payForm.bank_account,
        reference_no: payForm.reference_no || undefined,
        notes:        payForm.notes || undefined,
        paid_by:      'user',
        amount_fcy:   isFcyPayment ? (parseFloat(payForm.amount_fcy) || 0) : undefined,
        payment_rate: isFcyPayment ? (parseFloat(payForm.payment_rate) || 0) : undefined,
      }),
    onSuccess: (res) => {
      const d = res.data
      showToast(
        d.invoice_status === 'paid'
          ? t('ap:paidStatusToast', { invoiceNo: d.invoice_no })
          : t('ap:partialPaymentToast', { amount: formatRupiah(d.remaining_ap) })
      )
      setPayInvoice(null)
      qc.invalidateQueries({ queryKey: ['ap-outstanding', entityId] })
      qc.invalidateQueries({ queryKey: ['ap-payments', entityId] })
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('ap:paymentFailed'), 'error'),
  })

  // ── PPh remittance form ──────────────────────────────────────────────────────
  const [pphForm, setPphForm] = useState({
    pph_type: 'PPh23',
    payment_date: todayISO(),
    amount: '',
    bank_account: '1-1-001',
    reference_no: '',
    period_month: currentMonth(),
    period_year:  currentYear(),
  })

  const pphMutation = useMutation({
    mutationFn: () =>
      api.post('/ap/pph-remittance', {
        entity_id:    entityId,
        pph_type:     pphForm.pph_type,
        payment_date: pphForm.payment_date,
        amount:       parseFloat(pphForm.amount) || 0,
        bank_account: pphForm.bank_account,
        reference_no: pphForm.reference_no || undefined,
        period_month: pphForm.period_month,
        period_year:  pphForm.period_year,
        paid_by:      'user',
      }),
    onSuccess: (res) => {
      showToast(t('ap:remittanceSuccess', { journalNo: res.data.journal_no ?? '' }))
      setPphForm(f => ({ ...f, amount: '', reference_no: '' }))
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('ap:remittanceFailed'), 'error'),
  })

  // ── Stats ────────────────────────────────────────────────────────────────────
  const totalOutstanding = outstanding.reduce((s, i) => s + (i.ap_outstanding ?? 0), 0)
  const totalPph         = outstanding.reduce((s, i) => s + (i.pph_amount ?? 0), 0)
  const overdueCount     = outstanding.filter(i => (i.days_overdue ?? 0) > 0).length
  const totalPayments    = payments.reduce((s, p) => s + (p.amount ?? 0), 0)

  const years = [currentYear(), currentYear() - 1]

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">{t('ap:paymentsPageTitle')}</h1>
          <p className="text-sm text-gray-500 mt-0.5">{t('ap:paymentsPageSubtitle')}</p>
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-3 gap-4">
        <div className={`card p-4 ${overdueCount > 0 ? 'border-l-4 border-red-400' : ''}`}>
          <p className="text-xs text-gray-500 uppercase">{t('ap:statTotalOutstanding')}</p>
          <p className="text-xl font-bold text-gray-900 mt-1">Rp {formatRupiah(totalOutstanding)}</p>
          {overdueCount > 0 && (
            <p className="text-xs text-red-500 mt-0.5 flex items-center gap-1">
              <AlertTriangle className="h-3 w-3" /> {t('ap:statOverdueCount', { count: overdueCount })}
            </p>
          )}
        </div>
        <div className="card p-4">
          <p className="text-xs text-gray-500 uppercase">{t('ap:statPphUnpaid')}</p>
          <p className="text-xl font-bold text-amber-600 mt-1">Rp {formatRupiah(totalPph)}</p>
          <p className="text-xs text-gray-400 mt-0.5">{t('ap:statPphUnpaidDescription')}</p>
        </div>
        <div className="card p-4">
          <p className="text-xs text-gray-500 uppercase">{t('ap:statTotalPaid')}</p>
          <p className="text-xl font-bold text-gray-900 mt-1">Rp {formatRupiah(totalPayments)}</p>
        </div>
      </div>

      {/* Tabs */}
      <div className="border-b border-gray-200">
        <nav className="flex gap-1">
          {[
            { key: 'outstanding', label: t('ap:tabOutstanding') },
            { key: 'payments',    label: t('ap:tabPaymentHistory') },
            { key: 'pph',         label: t('ap:tabPph') },
          ].map(tb => (
            <button key={tb.key}
              onClick={() => setTab(tb.key as any)}
              className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                tab === tb.key
                  ? 'border-primary-600 text-primary-700'
                  : 'border-transparent text-gray-500 hover:text-gray-700'
              }`}>
              {tb.label}
              {tb.key === 'pph' && totalPph > 0 && (
                <span className="ml-1.5 text-xs bg-amber-100 text-amber-700 px-1.5 py-0.5 rounded-full">
                  Rp {formatRupiah(totalPph)}
                </span>
              )}
            </button>
          ))}
        </nav>
      </div>

      {/* Tab: Outstanding */}
      {tab === 'outstanding' && (
        <Card noPad>
          <CardHeader
            title={t('ap:outstandingTableTitle')}
            subtitle={t('ap:outstandingTableSubtitle', { count: outstanding.length })}
            actions={<button onClick={() => refetchOut()} className="btn-secondary"><RefreshCw className="h-4 w-4" /></button>}
          />
          {outLoading ? (
            <div className="flex justify-center py-16"><Spinner size="lg" /></div>
          ) : outstanding.length === 0 ? (
            <EmptyState title={t('ap:emptyOutstandingTitle')} />
          ) : (
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>{t('ap:colInvoiceNo')}</th>
                    <th>{t('ap:colVendor')}</th>
                    <th>{t('ap:colInvoiceDate')}</th>
                    <th>{t('ap:colDueDate')}</th>
                    <th className="right">{t('ap:colTotal')}</th>
                    <th className="right">{t('ap:colApOutstanding')}</th>
                    <th className="right">{t('ap:colPph')}</th>
                    <th>{t('common:status')}</th>
                    <th>{t('common:action')}</th>
                  </tr>
                </thead>
                <tbody>
                  {outstanding.map((inv: any) => {
                    const overdue = (inv.days_overdue ?? 0) > 0
                    return (
                      <tr key={inv.id} className={overdue ? 'bg-red-50/40' : ''}>
                        <td className="font-mono text-xs">{inv.invoice_no}</td>
                        <td className="text-sm font-medium">{inv.vendor_name}</td>
                        <td className="text-sm text-gray-500 whitespace-nowrap">{formatDate(inv.invoice_date)}</td>
                        <td className="whitespace-nowrap">
                          <span className={`text-sm ${overdue ? 'text-red-600 font-medium' : 'text-gray-500'}`}>
                            {inv.due_date ? formatDate(inv.due_date) : '—'}
                          </span>
                          {overdue && (
                            <span className="ml-1 text-xs text-red-500">+{inv.days_overdue}{t('ap:overdueDaysSuffix')}</span>
                          )}
                        </td>
                        <td className="right">Rp {formatRupiah(inv.total_amount)}</td>
                        <td className="right font-semibold text-red-600">
                          Rp {formatRupiah(inv.ap_outstanding)}
                        </td>
                        <td className="right text-amber-600 text-sm">
                          {inv.pph_amount > 0
                            ? `Rp ${formatRupiah(inv.pph_amount)}`
                            : <span className="text-gray-300">—</span>}
                        </td>
                        <td><Badge status={inv.status} /></td>
                        <td>
                          <button
                            onClick={() => openPayForm(inv)}
                            disabled={inv.ap_outstanding <= 0}
                            className="inline-flex items-center gap-1 text-xs px-2 py-1 bg-blue-50 text-blue-700 hover:bg-blue-100 rounded-md disabled:opacity-40">
                            <DollarSign className="h-3 w-3" /> {t('ap:payBtn')}
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

      {/* Tab: Payment history */}
      {tab === 'payments' && (
        <Card noPad>
          <CardHeader
            title={t('ap:paymentHistoryTitle')}
            subtitle={t('ap:paymentHistorySubtitle', { count: payments.length })}
            actions={<button onClick={() => refetchPay()} className="btn-secondary"><RefreshCw className="h-4 w-4" /></button>}
          />
          {pLoading ? (
            <div className="flex justify-center py-16"><Spinner size="lg" /></div>
          ) : payments.length === 0 ? (
            <EmptyState title={t('ap:emptyPaymentsTitle')} />
          ) : (
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>{t('ap:colDate')}</th>
                    <th>{t('ap:colInvoice')}</th>
                    <th>{t('ap:colVendor')}</th>
                    <th>{t('ap:colReference')}</th>
                    <th>{t('ap:colBankAccount')}</th>
                    <th className="right">{t('ap:colAmountPaid')}</th>
                    <th>{t('ap:colJournal')}</th>
                  </tr>
                </thead>
                <tbody>
                  {payments.map((p: any) => (
                    <tr key={p.id}>
                      <td className="text-sm text-gray-500 whitespace-nowrap">{formatDate(p.payment_date)}</td>
                      <td className="font-mono text-xs">{p.invoice_no}</td>
                      <td className="text-sm">{p.vendor_name}</td>
                      <td className="text-xs text-gray-400">{p.reference_no ?? '—'}</td>
                      <td className="font-mono text-xs text-gray-400">{p.bank_account}</td>
                      <td className="right font-semibold">
                        Rp {formatRupiah(p.amount)}
                        {p.currency && p.currency !== 'IDR' && (
                          <div className="text-xs text-gray-400 font-normal">{formatCurrency(p.amount_fcy, p.currency)}</div>
                        )}
                        {p.realized_gl != null && (
                          <div className={`text-xs font-normal ${p.realized_gl > 0 ? 'text-green-600' : 'text-red-600'}`}>
                            {p.realized_gl > 0 ? '+' : ''}Rp {formatRupiah(p.realized_gl)}
                          </div>
                        )}
                      </td>
                      <td className="font-mono text-xs text-gray-400">{p.journal_id?.slice(0, 8)}</td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr className="bg-gray-50 font-semibold">
                    <td colSpan={5} className="pl-4 py-2 text-sm text-gray-600">{t('ap:totalPaymentFooter')}</td>
                    <td className="right pr-4">Rp {formatRupiah(totalPayments)}</td>
                    <td />
                  </tr>
                </tfoot>
              </table>
            </div>
          )}
        </Card>
      )}

      {/* Tab: PPh remittance */}
      {tab === 'pph' && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <Card>
            <p className="text-sm font-semibold text-gray-800 mb-4">{t('ap:pphFormTitle')}</p>
            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="form-label">{t('ap:pphTypeSelectLabel')}</label>
                  <select value={pphForm.pph_type}
                    onChange={e => setPphForm({ ...pphForm, pph_type: e.target.value })}
                    className="form-select">
                    <option value="PPh23">PPh Pasal 23</option>
                    <option value="PPh4_2">PPh Pasal 4(2)</option>
                    <option value="PPh21">PPh Pasal 21</option>
                  </select>
                </div>
                <div>
                  <label className="form-label">{t('ap:remitDateLabel')}</label>
                  <input type="date" value={pphForm.payment_date}
                    onChange={e => setPphForm({ ...pphForm, payment_date: e.target.value })}
                    className="form-input" />
                </div>
                <div>
                  <label className="form-label">{t('ap:periodMonthLabel')}</label>
                  <select value={pphForm.period_month}
                    onChange={e => setPphForm({ ...pphForm, period_month: +e.target.value })}
                    className="form-select">
                    {MONTHS.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
                  </select>
                </div>
                <div>
                  <label className="form-label">{t('ap:periodYearLabel')}</label>
                  <select value={pphForm.period_year}
                    onChange={e => setPphForm({ ...pphForm, period_year: +e.target.value })}
                    className="form-select">
                    {years.map(y => <option key={y} value={y}>{y}</option>)}
                  </select>
                </div>
                <div>
                  <label className="form-label">{t('ap:amountRemittedLabel')}</label>
                  <input type="number" value={pphForm.amount}
                    onChange={e => setPphForm({ ...pphForm, amount: e.target.value })}
                    className="form-input" placeholder="0" />
                </div>
                <div>
                  <label className="form-label">{t('ap:bankAccountLabel')}</label>
                  <input value={pphForm.bank_account}
                    onChange={e => setPphForm({ ...pphForm, bank_account: e.target.value })}
                    className="form-input font-mono text-sm" />
                </div>
                <div className="col-span-2">
                  <label className="form-label">{t('ap:ntpnRefLabel')}</label>
                  <input value={pphForm.reference_no}
                    onChange={e => setPphForm({ ...pphForm, reference_no: e.target.value })}
                    className="form-input" placeholder={t('ap:ntpnRefPlaceholder')} />
                </div>
              </div>
              <button
                onClick={() => pphMutation.mutate()}
                disabled={pphMutation.isPending || !pphForm.amount}
                className="btn-primary w-full">
                <Send className="h-4 w-4" />
                {pphMutation.isPending ? t('ap:recordingBtn') : t('ap:recordRemittanceBtn')}
              </button>
            </div>
          </Card>

          <Card>
            <p className="text-sm font-semibold text-gray-800 mb-3">{t('ap:notesTitle')}</p>
            <div className="space-y-3 text-sm text-gray-600">
              <div className="bg-amber-50 border border-amber-200 rounded-lg p-3">
                <p className="font-semibold text-amber-800 mb-1">{t('ap:notesPphOutstandingTitle')}</p>
                <p className="text-amber-700">
                  {t('ap:notesPphOutstandingBody')} <strong>Rp {formatRupiah(totalPph)}</strong>
                </p>
              </div>
              <div className="space-y-2 text-xs">
                <p className="font-semibold text-gray-700">{t('ap:notesFlowTitle')}</p>
                <p>1. {t('ap:notesFlowStep1')}</p>
                <p>2. {t('ap:notesFlowStep2')}</p>
                <p>3. {t('ap:notesFlowStep3')}</p>
                <p>4. {t('ap:notesFlowStep4')}</p>
              </div>
              <div className="space-y-1 text-xs text-gray-500">
                <p className="font-semibold text-gray-700">{t('ap:notesDeadlineTitle')}</p>
                <p>• {t('ap:notesDeadline23')}</p>
                <p>• {t('ap:notesDeadline21')}</p>
                <p>• {t('ap:notesDeadlineBilling')}</p>
              </div>
            </div>
          </Card>
        </div>
      )}

      {/* Pay form modal */}
      {payInvoice && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-xl w-full max-w-lg">
            <div className="flex items-center justify-between p-4 border-b">
              <div>
                <p className="font-semibold text-gray-900">{t('ap:payModalTitle')}</p>
                <p className="text-sm text-gray-500 mt-0.5">
                  {payInvoice.invoice_no} — {payInvoice.vendor_name}
                </p>
              </div>
              <button onClick={() => setPayInvoice(null)} className="p-1.5 hover:bg-gray-100 rounded-lg">
                <X className="h-5 w-5 text-gray-400" />
              </button>
            </div>

            <div className="p-4 space-y-3">
              {/* Invoice summary */}
              <div className="bg-gray-50 rounded-lg p-3 text-sm grid grid-cols-2 gap-2">
                <div>
                  <p className="text-gray-500 text-xs">{t('ap:apOutstandingLabel')}</p>
                  <p className="font-semibold text-red-600">Rp {formatRupiah(payInvoice.ap_outstanding)}</p>
                </div>
                {payInvoice.pph_amount > 0 && (
                  <div>
                    <p className="text-gray-500 text-xs">{t('ap:pphSeparateLabel')}</p>
                    <p className="font-medium text-amber-600">Rp {formatRupiah(payInvoice.pph_amount)}</p>
                  </div>
                )}
              </div>

              {payInvoice.pph_amount > 0 && (
                <div className="bg-amber-50 border border-amber-200 rounded-lg p-2.5 text-xs text-amber-700">
                  {t('ap:pphNotIncludedNotice', { amount: formatRupiah(payInvoice.pph_amount) })}
                </div>
              )}

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="form-label">{t('ap:payDateLabel')}</label>
                  <input type="date" value={payForm.payment_date}
                    onChange={e => setPayForm({ ...payForm, payment_date: e.target.value })}
                    className="form-input" />
                </div>
                <div>
                  <label className="form-label">{t('ap:bankAccountLabel')}</label>
                  <input value={payForm.bank_account}
                    onChange={e => setPayForm({ ...payForm, bank_account: e.target.value })}
                    className="form-input font-mono text-sm" />
                </div>
                {isFcyPayment ? (
                  <>
                    <div>
                      <label className="form-label">
                        {t('multicurrency:payment_amountFcyLabel')} ({payInvoice.currency})
                      </label>
                      <input type="number" value={payForm.amount_fcy}
                        onChange={e => setPayForm({ ...payForm, amount_fcy: e.target.value })}
                        className="form-input" />
                    </div>
                    <div>
                      <label className="form-label">{t('multicurrency:payment_paymentRateLabel')}</label>
                      <input type="number" value={payForm.payment_rate}
                        onChange={e => setPayForm({ ...payForm, payment_rate: e.target.value })}
                        min={0} step="0.000001" className="form-input" />
                      <p className="text-xs text-gray-400 mt-1">
                        {t('multicurrency:invoice_exchangeRateLabel')}: {invoiceRate}
                      </p>
                    </div>
                  </>
                ) : (
                  <div>
                    <label className="form-label">{t('ap:amountPaidLabel')}</label>
                    <input type="number" value={payForm.amount}
                      onChange={e => setPayForm({ ...payForm, amount: e.target.value })}
                      className="form-input" />
                  </div>
                )}
                <div>
                  <label className="form-label">{t('ap:referenceNoLabel')}</label>
                  <input value={payForm.reference_no}
                    onChange={e => setPayForm({ ...payForm, reference_no: e.target.value })}
                    className="form-input" placeholder={t('ap:referenceNoPlaceholder')} />
                </div>
              </div>

              {isFcyPayment && Math.abs(estimatedRealizedGl) >= 1 && (
                <div className={`rounded-lg p-2.5 text-xs ${estimatedRealizedGl > 0 ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>
                  {estimatedRealizedGl > 0 ? t('multicurrency:payment_realizedGainLabel') : t('multicurrency:payment_realizedLossLabel')}:{' '}
                  Rp {formatRupiah(Math.abs(estimatedRealizedGl))}
                </div>
              )}
            </div>

            <div className="flex gap-3 px-4 pb-4">
              <button onClick={() => payMutation.mutate()}
                disabled={payMutation.isPending || (isFcyPayment ? (!payForm.amount_fcy || !payForm.payment_rate) : !payForm.amount)}
                className="btn-primary flex-1">
                <CheckCircle className="h-4 w-4" />
                {payMutation.isPending ? t('ap:savingBtn') : t('ap:recordPaymentBtn')}
              </button>
              <button onClick={() => setPayInvoice(null)} className="btn-secondary">{t('ap:cancelBtn')}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
