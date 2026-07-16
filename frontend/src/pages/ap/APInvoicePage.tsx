import { useRef, useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Search, RefreshCw, Filter, Plus, X, Send, Paperclip, FileUp, Trash2 } from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext'
import api from '../../lib/api'
import { Card, CardHeader } from '../../components/ui/Card'
import Spinner from '../../components/ui/Spinner'
import EmptyState from '../../components/ui/EmptyState'
import Badge from '../../components/ui/Badge'
import AttachmentPanel from '../../components/shared/AttachmentPanel'
import CurrencySelect from '../../components/shared/CurrencySelect'
import { useLatestRate } from '../../lib/currency'
import { formatRupiah, formatCurrency, formatDate, todayISO } from '../../lib/utils'
import { showToast } from '../../components/ui/Toast'

const PAYABLE_CATEGORY_DEFAULT_COA: Record<string, string> = {
  trade: '2-1-001',
  related_party: '2-1-016',
  bank_loan: '2-1-015',
  other: '2-1-001',
}

const PAYMENT_TERM_OPTS = [0, 7, 14, 30, 45, 60, 90]

function addDaysISO(dateStr: string, days: number): string {
  if (!dateStr) return ''
  const d = new Date(dateStr)
  d.setDate(d.getDate() + days)
  return d.toISOString().split('T')[0]
}

type APInvoiceLineForm = {
  account_code: string
  description: string
  amount: string
  cost_center: string
  project_id: string
}

const emptyLine = (): APInvoiceLineForm => ({
  account_code: '', description: '', amount: '', cost_center: '', project_id: '',
})

const emptyForm = {
  vendor_id: '', vendor_search: '', invoice_no: '', invoice_date: todayISO(),
  due_date: addDaysISO(todayISO(), 30), payment_term_days: '30',
  ppn_amount: '', pph_type: '', pph_rate: '',
  faktur_pajak_no: '',
  lines: [emptyLine()] as APInvoiceLineForm[],
  payable_category: 'trade', payable_coa: '2-1-001',
  currency: 'IDR', exchange_rate: '1',
}

export default function APInvoicePage() {
  const { t } = useTranslation(['ap', 'common', 'multicurrency'])
  const { entityId } = useAuth()
  const qc = useQueryClient()
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('')
  const [page, setPage] = useState(1)
  const pageSize = 50
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState(emptyForm)
  const [attachFile, setAttachFile] = useState<File | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [attachmentInvoiceId, setAttachmentInvoiceId] = useState<string | null>(null)

  const isFcy = form.currency !== 'IDR'
  const exchangeRateNum = parseFloat(form.exchange_rate) || 1

  // Auto-fill kurs terbaru saat ganti mata uang (tetap bisa dioverride manual)
  const { rate: latestRate } = useLatestRate(form.currency)
  useEffect(() => {
    if (form.currency === 'IDR') { setForm((f) => ({ ...f, exchange_rate: '1' })); return }
    if (latestRate != null) setForm((f) => ({ ...f, exchange_rate: String(latestRate) }))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [form.currency, latestRate])

  const STATUS_OPTS = [
    { value: '', label: t('ap:statusAll') },
    { value: 'draft', label: t('ap:statusDraft') },
    { value: 'posted', label: t('ap:statusPosted') },
    { value: 'partial', label: t('ap:statusPartial') },
    { value: 'paid', label: t('ap:statusPaid') },
    { value: 'overdue', label: t('ap:statusOverdue') },
    { value: 'cancelled', label: t('ap:statusCancelled') },
  ]

  const PAYABLE_CATEGORY_OPTS = [
    { value: 'trade', label: t('ap:payableCategoryTrade') },
    { value: 'related_party', label: t('ap:payableCategoryRelatedParty') },
    { value: 'bank_loan', label: t('ap:payableCategoryBankLoan') },
    { value: 'other', label: t('ap:payableCategoryOther') },
  ]

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['ap-invoices', entityId, status, page],
    queryFn: () =>
      api.get(`/ap/invoices`, {
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

  const filtered = invoices.filter((inv) =>
    !search ||
    inv.invoice_no?.toLowerCase().includes(search.toLowerCase()) ||
    inv.vendor_name?.toLowerCase().includes(search.toLowerCase()),
  )

  // Vendor search (for create form)
  const { data: vendorResults } = useQuery({
    queryKey: ['vendor-search', entityId, form.vendor_search],
    queryFn: () => api.get('/vendors', {
      params: { entity_id: entityId, search: form.vendor_search, limit: 20 },
    }).then((r) => r.data),
    enabled: !!entityId && showForm,
  })
  const vendors: any[] = Array.isArray(vendorResults) ? vendorResults : (vendorResults?.items ?? [])

  // COA (for expense account datalist + payable account datalist)
  const { data: coaData } = useQuery({
    queryKey: ['coa', entityId],
    queryFn: () => api.get('/coa/', { params: { entity_id: entityId, limit: 1000 } }).then((r) => r.data),
    enabled: !!entityId && showForm,
  })
  const accounts: any[] = Array.isArray(coaData) ? coaData : (coaData?.accounts ?? [])
  const liabilityAccounts = accounts.filter((a: any) => !a.is_header && a.account_type === 'liability')
  // Sisi debit AP invoice: bisa langsung dibebankan (expense), atau dikapitalisasi
  // sebagai aset tetap / dibayar dimuka (prepaid) tergantung sifat transaksinya.
  // Tidak termasuk akun "asset" generik (kas/bank/piutang) — itu bukan tujuan invoice vendor.
  const debitAccounts = accounts.filter((a: any) =>
    !a.is_header && ['expense', 'cogs', 'other_expense', 'tax_expense', 'fixed_asset', 'prepaid'].includes(a.account_type))

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

  const selectVendor = (vendorId: string) => {
    const v = vendors.find((x: any) => x.id === vendorId)
    setForm((f) => {
      const term = v?.default_payment_term_days != null ? String(v.default_payment_term_days) : f.payment_term_days
      return {
        ...f,
        vendor_id: vendorId,
        vendor_search: v?.vendor_name ?? f.vendor_search,
        pph_type: v?.default_pph_type ?? f.pph_type,
        pph_rate: v?.default_pph_rate != null ? String(v.default_pph_rate) : f.pph_rate,
        payment_term_days: term,
        due_date: addDaysISO(f.invoice_date, parseInt(term) || 0),
      }
    })
  }

  const lineTotalFcy = form.lines.reduce((s, l) => s + (parseFloat(l.amount) || 0), 0)
  const lineTotal = isFcy ? lineTotalFcy * exchangeRateNum : lineTotalFcy
  const linesValid = form.lines.length > 0 &&
    form.lines.every((l) => l.account_code && parseFloat(l.amount) > 0)

  const updateLine = (idx: number, patch: Partial<APInvoiceLineForm>) => {
    setForm((f) => ({
      ...f,
      lines: f.lines.map((l, i) => (i === idx ? { ...l, ...patch } : l)),
    }))
  }
  const addLine = () => setForm((f) => ({ ...f, lines: [...f.lines, emptyLine()] }))
  const removeLine = (idx: number) => setForm((f) => ({ ...f, lines: f.lines.filter((_, i) => i !== idx) }))

  // OCR auto-extract dari file lampiran
  const ocrMutation = useMutation({
    mutationFn: (file: File) => {
      const fd = new FormData()
      fd.append('file', file)
      return api.post('/ocr/extract', fd, {
        params: { entity_id: entityId },
        headers: { 'Content-Type': 'multipart/form-data' },
        timeout: 90000,
      }).then((r) => r.data)
    },
    onSuccess: (res) => {
      if (!res.success) {
        showToast(t('ap:ocrFailed'), 'error')
        return
      }
      setForm((f) => {
        const invoiceDate = res.invoice_date || f.invoice_date
        return {
          ...f,
          invoice_no: res.invoice_no || f.invoice_no,
          invoice_date: invoiceDate,
          due_date: addDaysISO(invoiceDate, parseInt(f.payment_term_days) || 0),
          lines: res.subtotal
            ? [{ ...f.lines[0], amount: String(res.subtotal) }, ...f.lines.slice(1)]
            : f.lines,
          ppn_amount: res.ppn_amount ? String(res.ppn_amount) : f.ppn_amount,
          vendor_search: res.vendor_name || f.vendor_search,
          vendor_id: res.vendor_id || f.vendor_id,
        }
      })
      showToast(t('ap:ocrAutoFillSuccess'))
    },
    onError: () => showToast(t('ap:ocrFailed'), 'error'),
  })

  const handleAttachFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    setAttachFile(file)
    ocrMutation.mutate(file)
  }

  const createMutation = useMutation({
    mutationFn: () =>
      api.post('/ap/invoices', {
        entity_id: entityId,
        vendor_id: form.vendor_id,
        invoice_no: form.invoice_no,
        invoice_date: form.invoice_date,
        due_date: form.due_date || undefined,
        ppn_amount: parseFloat(form.ppn_amount) || 0,
        pph_type: form.pph_type || undefined,
        pph_rate: form.pph_rate ? parseFloat(form.pph_rate) : undefined,
        faktur_pajak_no: form.faktur_pajak_no || undefined,
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
        payable_category: form.payable_category,
        payable_coa: form.payable_coa || undefined,
        payment_term_days: parseInt(form.payment_term_days) || 30,
        currency: form.currency,
        exchange_rate: exchangeRateNum,
      }),
    onSuccess: async (res) => {
      const invoiceId = res.data?.invoice_id
      if (attachFile && invoiceId) {
        const fd = new FormData()
        fd.append('entity_id', entityId)
        fd.append('file', attachFile)
        fd.append('ref_type', 'ap_invoice')
        fd.append('ref_id', invoiceId)
        try {
          await api.post('/attachments/upload', fd, { headers: { 'Content-Type': 'multipart/form-data' } })
        } catch {
          showToast(t('ap:attachmentUploadFailed'), 'error')
        }
      }
      showToast(t('ap:createSuccess'))
      setShowForm(false)
      setForm(emptyForm)
      setAttachFile(null)
      qc.invalidateQueries({ queryKey: ['ap-invoices'] })
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('ap:createFailed'), 'error'),
  })

  const postJournalMutation = useMutation({
    mutationFn: (invoiceId: string) => api.post(`/ap/invoices/${invoiceId}/post-journal`),
    onSuccess: () => {
      showToast(t('ap:postJournalSuccess'))
      qc.invalidateQueries({ queryKey: ['ap-invoices'] })
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('ap:postJournalFailed'), 'error'),
  })

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">{t('ap:invoicePageTitle')}</h1>
          <p className="text-sm text-gray-500 mt-0.5">{t('ap:invoicePageSubtitle')}</p>
        </div>
        <button onClick={() => setShowForm(true)} className="btn-primary">
          <Plus className="h-4 w-4" /> {t('ap:createInvoiceBtn')}
        </button>
      </div>

      {/* Create form */}
      {showForm && (
        <Card>
          <div className="flex items-center justify-between mb-4">
            <p className="text-sm font-semibold text-gray-700">{t('ap:newInvoiceFormTitle')}</p>
            <button onClick={() => setShowForm(false)}><X className="h-4 w-4 text-gray-400" /></button>
          </div>

          {/* Lampiran + OCR auto-fill */}
          <div className="mb-4 p-3 border border-dashed border-gray-300 rounded-lg bg-gray-50">
            <p className="text-sm font-medium text-gray-700 mb-1">{t('ap:attachmentLabel')}</p>
            <p className="text-xs text-gray-500 mb-2">{t('ap:attachFileHint')}</p>
            <input ref={fileInputRef} type="file" className="hidden"
              accept=".pdf,.jpg,.jpeg,.png" onChange={handleAttachFile} />
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={ocrMutation.isPending}
              className="btn-secondary"
            >
              {ocrMutation.isPending ? <Spinner size="sm" /> : <FileUp className="h-4 w-4" />}
              {ocrMutation.isPending ? t('ap:ocrReadingLabel') : (attachFile?.name ?? t('ap:attachmentLabel'))}
            </button>
          </div>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div className="col-span-2">
              <label className="form-label">{t('ap:vendorLabel')}</label>
              <input
                list="vendor-list"
                value={form.vendor_search}
                onChange={(e) => {
                  const val = e.target.value
                  const match = vendors.find((v: any) => v.vendor_name === val)
                  if (match) selectVendor(match.id)
                  else setForm({ ...form, vendor_search: val, vendor_id: '' })
                }}
                className="form-input"
                placeholder={t('ap:vendorSearchPlaceholder')}
              />
              <datalist id="vendor-list">
                {vendors.map((v: any) => (
                  <option key={v.id} value={v.vendor_name} />
                ))}
              </datalist>
              {form.vendor_search && !form.vendor_id && (
                <p className="text-xs text-red-400 mt-1">{t('ap:vendorSelectHint')}</p>
              )}
            </div>
            <div>
              <label className="form-label">{t('ap:invoiceNoLabel')}</label>
              <input value={form.invoice_no}
                onChange={(e) => setForm({ ...form, invoice_no: e.target.value })}
                className="form-input" placeholder={t('ap:invoiceNoPlaceholder')} />
            </div>
            <div>
              <label className="form-label">{t('ap:fakturPajakNoLabel')}</label>
              <input value={form.faktur_pajak_no}
                onChange={(e) => setForm({ ...form, faktur_pajak_no: e.target.value })}
                className="form-input" placeholder={t('common:optional')} />
            </div>
            <div>
              <label className="form-label">{t('ap:invoiceDateLabel')}</label>
              <input type="date" value={form.invoice_date}
                onChange={(e) => setForm({
                  ...form, invoice_date: e.target.value,
                  due_date: addDaysISO(e.target.value, parseInt(form.payment_term_days) || 0),
                })}
                className="form-input" />
            </div>
            <div>
              <label className="form-label">{t('ap:paymentTermLabel')}</label>
              <select value={form.payment_term_days}
                onChange={(e) => setForm({
                  ...form, payment_term_days: e.target.value,
                  due_date: addDaysISO(form.invoice_date, parseInt(e.target.value) || 0),
                })}
                className="form-select">
                {PAYMENT_TERM_OPTS.map((d) => (
                  <option key={d} value={d}>{d === 0 ? t('ap:termCod') : `NET ${d}`}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="form-label">{t('ap:dueDateLabel')}</label>
              <input type="date" value={form.due_date}
                onChange={(e) => setForm({ ...form, due_date: e.target.value })}
                className="form-input" />
              <p className="text-xs text-gray-400 mt-1">{t('ap:dueDateAutoHint')}</p>
            </div>
            <div>
              <label className="form-label">{t('ap:ppnAmountLabel')}</label>
              <input type="number" value={form.ppn_amount}
                onChange={(e) => setForm({ ...form, ppn_amount: e.target.value })}
                className="form-input" placeholder="0" />
            </div>
            <div>
              <label className="form-label">{t('ap:pphTypeLabel')}</label>
              <input value={form.pph_type}
                onChange={(e) => setForm({ ...form, pph_type: e.target.value })}
                className="form-input" placeholder={t('ap:pphTypePlaceholder')} />
            </div>
            <div>
              <label className="form-label">{t('ap:pphRateLabel')}</label>
              <input type="number" value={form.pph_rate}
                onChange={(e) => setForm({ ...form, pph_rate: e.target.value })}
                className="form-input" placeholder={t('ap:pphRatePlaceholder')} />
            </div>
            <div>
              <label className="form-label">{t('ap:payableCategoryLabel')}</label>
              <select value={form.payable_category}
                onChange={(e) => {
                  const cat = e.target.value
                  setForm((f) => ({ ...f, payable_category: cat, payable_coa: PAYABLE_CATEGORY_DEFAULT_COA[cat] ?? f.payable_coa }))
                }}
                className="form-select">
                {PAYABLE_CATEGORY_OPTS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </div>
            <div>
              <label className="form-label">{t('ap:payableCoaLabel')}</label>
              <input
                list="payable-coa-list"
                value={form.payable_coa}
                onChange={(e) => setForm({ ...form, payable_coa: e.target.value })}
                className="form-input font-mono text-sm" placeholder="2-1-001"
              />
              <datalist id="payable-coa-list">
                {liabilityAccounts.map((a: any) => (
                  <option key={a.account_code} value={a.account_code}>{a.account_name}</option>
                ))}
              </datalist>
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

          {/* Rincian Beban — multi-baris (account + cost center + project per baris) */}
          <div className="mt-4 border border-gray-200 rounded-lg overflow-hidden">
            <div className="px-3 py-2 bg-gray-50 border-b border-gray-200">
              <p className="text-sm font-semibold text-gray-700">{t('ap:expenseLinesTitle')}</p>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-xs text-gray-500 border-b border-gray-100">
                    <th className="text-left font-medium px-3 py-2 min-w-40">{t('ap:coaExpenseLabel')}</th>
                    <th className="text-left font-medium px-3 py-2 min-w-32">{t('common:description')}</th>
                    <th className="text-left font-medium px-3 py-2 min-w-32">{t('ap:costCenterLabel')}</th>
                    <th className="text-left font-medium px-3 py-2 min-w-32">{t('ap:projectLabel')}</th>
                    <th className="text-right font-medium px-3 py-2 min-w-32">
                      {isFcy ? t('multicurrency:invoice_fcyAmountLabel') : t('ap:lineAmountLabel')}
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
                          list={`coa-expense-list-${idx}`}
                          value={line.account_code}
                          onChange={(e) => updateLine(idx, { account_code: e.target.value })}
                          className="form-input font-mono text-xs py-1" placeholder="6-1-001"
                        />
                        <datalist id={`coa-expense-list-${idx}`}>
                          {debitAccounts.map((a: any) => (
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
                        <Plus className="h-3.5 w-3.5" /> {t('ap:addLineBtn')}
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
              disabled={createMutation.isPending || !form.vendor_id || !form.invoice_no || !linesValid}
              className="btn-primary">
              {createMutation.isPending ? t('ap:savingBtn') : t('ap:saveInvoiceBtn')}
            </button>
            <button onClick={() => setShowForm(false)} className="btn-secondary">{t('ap:cancelBtn')}</button>
          </div>
        </Card>
      )}

      {/* Summary */}
      <div className="grid grid-cols-3 gap-4">
        {[
          { label: t('ap:summaryTotalInvoice'), value: filtered.length, isCurrency: false },
          { label: t('ap:summaryTotalPayable'), value: filtered.reduce((s, i) => s + (i.total_amount ?? 0), 0), isCurrency: true },
          { label: t('ap:summaryUnpaid'), value: filtered.reduce((s, i) => s + ((i.total_amount ?? 0) - (i.paid_amount ?? 0)), 0), isCurrency: true },
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
          title={t('ap:invoiceListTitle')}
          subtitle={t('ap:invoiceListSubtitle', { count: filtered.length })}
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
              placeholder={t('ap:searchPlaceholder')}
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
          <EmptyState title={t('ap:noInvoices')} />
        ) : (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>{t('ap:colInvoiceNo')}</th>
                  <th>{t('ap:colVendor')}</th>
                  <th>{t('ap:colInvoiceDate')}</th>
                  <th>{t('ap:colDueDate')}</th>
                  <th>{t('ap:colCategory')}</th>
                  <th className="right">{t('ap:colTotal')}</th>
                  <th className="right">{t('ap:colPph')}</th>
                  <th className="right">{t('ap:colPaid')}</th>
                  <th className="right">{t('ap:colRemaining')}</th>
                  <th>{t('ap:colStatus')}</th>
                  <th>{t('ap:colAction')}</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((inv) => {
                  const sisa = (inv.total_amount ?? 0) - (inv.paid_amount ?? 0)
                  const isOverdue = sisa > 0 && inv.due_date && new Date(inv.due_date) < new Date()
                  return (
                    <tr key={inv.id}>
                      <td className="font-mono text-sm font-medium text-primary-600">
                        {inv.invoice_no}
                      </td>
                      <td className="text-sm">{inv.vendor_name ?? inv.vendor_id}</td>
                      <td className="text-sm text-gray-500">{formatDate(inv.invoice_date)}</td>
                      <td className={`text-sm ${isOverdue ? 'text-red-600 font-medium' : 'text-gray-500'}`}>
                        {formatDate(inv.due_date)}
                      </td>
                      <td className="text-xs text-gray-500">
                        {t(`ap:payableCategory${{
                          trade: 'Trade', related_party: 'RelatedParty', bank_loan: 'BankLoan', other: 'Other',
                        }[inv.payable_category as string] ?? 'Trade'}`)}
                      </td>
                      <td className="right">
                        Rp {formatRupiah(inv.total_amount)}
                        {inv.currency && inv.currency !== 'IDR' && (
                          <div className="text-xs text-gray-400">{formatCurrency(inv.amount_fcy, inv.currency)}</div>
                        )}
                      </td>
                      <td className="right text-gray-400 text-xs">Rp {formatRupiah(inv.pph_amount)}</td>
                      <td className="right text-green-600">Rp {formatRupiah(inv.paid_amount)}</td>
                      <td className={`right font-semibold ${sisa > 0 ? 'text-red-600' : 'text-gray-400'}`}>
                        Rp {formatRupiah(sisa)}
                      </td>
                      <td>
                        <Badge status={isOverdue && sisa > 0 ? 'overdue' : (inv.status ?? 'draft')} />
                      </td>
                      <td>
                        <div className="flex items-center gap-1.5">
                          <button
                            onClick={() => setAttachmentInvoiceId(inv.id)}
                            title={t('ap:viewAttachmentsBtn')}
                            className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-md bg-gray-100 text-gray-600 hover:bg-gray-200">
                            <Paperclip className="h-3 w-3" />
                          </button>
                          {inv.status === 'draft' && (
                            <button
                              onClick={() => postJournalMutation.mutate(inv.id)}
                              disabled={postJournalMutation.isPending}
                              className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-md bg-primary-50 text-primary-700 hover:bg-primary-100">
                              <Send className="h-3 w-3" /> {t('ap:postJournalBtn')}
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}

        {total > pageSize && (
          <div className="px-6 py-3 border-t border-gray-100 flex items-center justify-between text-sm text-gray-500">
            <span>{t('ap:paginationPage', { page, total: Math.ceil(total / pageSize) })}</span>
            <div className="flex gap-2">
              <button disabled={page === 1} onClick={() => setPage(p => p - 1)} className="btn-secondary py-1">{t('ap:paginationPrev')}</button>
              <button disabled={page * pageSize >= total} onClick={() => setPage(p => p + 1)} className="btn-secondary py-1">{t('ap:paginationNext')}</button>
            </div>
          </div>
        )}
      </Card>

      {attachmentInvoiceId && (
        <AttachmentPanel
          refType="ap_invoice"
          refId={attachmentInvoiceId}
          entityId={entityId}
          onClose={() => setAttachmentInvoiceId(null)}
        />
      )}
    </div>
  )
}
