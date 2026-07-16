import { Fragment, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery, useMutation } from '@tanstack/react-query'
import {
  Plus, RefreshCw, Send, CheckCircle, XCircle, Trash2,
  ShoppingCart, PackageCheck, Shield, ChevronDown, ChevronUp, Trophy,
} from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext'
import api from '../../lib/api'
import { Card, CardHeader } from '../../components/ui/Card'
import Spinner from '../../components/ui/Spinner'
import EmptyState from '../../components/ui/EmptyState'
import Badge from '../../components/ui/Badge'
import { formatRupiah, formatDate, todayISO } from '../../lib/utils'
import { showToast } from '../../components/ui/Toast'

const ROLE_LEVEL: Record<string, number> = { viewer: 1, finance: 2, admin: 3, superadmin: 4 }
const CATEGORY_OPTS = ['goods', 'services', 'asset']

type Tab = 'po' | 'receipt' | 'matrix'

type POItemForm = {
  _key: number; description: string; category: string; unit: string
  qty: string; unit_price: string; account_code: string; cost_center: string
}
let _pok = 0
const newPOItem = (): POItemForm => ({
  _key: ++_pok, description: '', category: 'services', unit: '',
  qty: '1', unit_price: '', account_code: '', cost_center: '',
})

function POQuotesPanel({
  po, colSpan, vendors, vendorSearch, setVendorSearch, canFinance, onSelected,
}: {
  po: any; colSpan: number; vendors: any[]; vendorSearch: string
  setVendorSearch: (v: string) => void; canFinance: boolean; onSelected: () => void
}) {
  const { t } = useTranslation(['procurement', 'common'])
  const { data, refetch } = useQuery({
    queryKey: ['po-quotes', po.id],
    queryFn: () => api.get(`/procurement/po/${po.id}/quotes`).then((r) => r.data),
  })
  const quotes: any[] = Array.isArray(data) ? data : []
  const isOpen = po.status === 'open'

  const [form, setForm] = useState({
    vendor_id: '', vendor_search: '', quoted_amount: '', quote_date: todayISO(), payment_terms: 'Net 30', notes: '',
  })

  const addMutation = useMutation({
    mutationFn: () => api.post(`/procurement/po/${po.id}/quotes`, {
      vendor_id: form.vendor_id,
      quoted_amount: parseFloat(form.quoted_amount) || 0,
      quote_date: form.quote_date || undefined,
      payment_terms: form.payment_terms || undefined,
      notes: form.notes || undefined,
    }),
    onSuccess: () => {
      showToast(t('procurement:quote_saveSuccess'))
      setForm({ vendor_id: '', vendor_search: '', quoted_amount: '', quote_date: todayISO(), payment_terms: 'Net 30', notes: '' })
      refetch()
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('procurement:quote_saveFailed'), 'error'),
  })

  const deleteMutation = useMutation({
    mutationFn: (quoteId: string) => api.delete(`/procurement/po/${po.id}/quotes/${quoteId}`),
    onSuccess: () => refetch(),
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('procurement:quote_saveFailed'), 'error'),
  })

  const selectMutation = useMutation({
    mutationFn: (quoteId: string) => api.post(`/procurement/po/${po.id}/select-vendor`, { quote_id: quoteId, tax_amount: 0 }),
    onSuccess: () => { showToast(t('procurement:quote_selectSuccess')); onSelected() },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('procurement:quote_selectFailed'), 'error'),
  })

  return (
    <tr>
      <td colSpan={colSpan} className="bg-gray-50 px-4 py-3">
        {quotes.length === 0 ? (
          <p className="text-xs text-gray-400 mb-3">{t('procurement:quote_emptyTitle')}</p>
        ) : (
          <table className="w-full text-xs mb-3">
            <thead>
              <tr className="text-gray-500 border-b border-gray-200">
                <th className="text-left font-medium px-2 py-1.5">{t('procurement:po_vendorLabel')}</th>
                <th className="right font-medium px-2 py-1.5">{t('procurement:quote_amountLabel')}</th>
                <th className="text-left font-medium px-2 py-1.5">{t('procurement:po_dateLabel')}</th>
                <th className="text-left font-medium px-2 py-1.5">{t('procurement:po_paymentTermsLabel')}</th>
                <th className="text-left font-medium px-2 py-1.5">{t('procurement:quote_notesLabel')}</th>
                <th className="px-2 py-1.5 w-32"></th>
              </tr>
            </thead>
            <tbody>
              {quotes.map((q: any) => (
                <tr key={q.id} className={`border-b border-gray-100 last:border-0 ${q.is_selected ? 'bg-green-50' : ''}`}>
                  <td className="px-2 py-1.5 font-medium">{q.vendor_name}</td>
                  <td className="right px-2 py-1.5">Rp {formatRupiah(q.quoted_amount)}</td>
                  <td className="px-2 py-1.5 text-gray-500">{q.quote_date ? formatDate(q.quote_date) : '—'}</td>
                  <td className="px-2 py-1.5 text-gray-500">{q.payment_terms ?? '—'}</td>
                  <td className="px-2 py-1.5 text-gray-500">{q.notes ?? '—'}</td>
                  <td className="px-2 py-1.5 text-right">
                    {q.is_selected ? (
                      <span className="inline-flex items-center gap-1 text-green-700 font-medium"><Trophy className="h-3.5 w-3.5" /> {t('procurement:quote_winnerBadge')}</span>
                    ) : isOpen && canFinance ? (
                      <div className="flex items-center justify-end gap-1.5">
                        <button onClick={() => selectMutation.mutate(q.id)} disabled={selectMutation.isPending}
                          className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-md bg-green-50 text-green-700 hover:bg-green-100">
                          <Trophy className="h-3 w-3" /> {t('procurement:quote_selectBtn')}
                        </button>
                        <button onClick={() => deleteMutation.mutate(q.id)} disabled={deleteMutation.isPending}
                          className="text-gray-400 hover:text-red-500">
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {isOpen && canFinance && (
          <div className="flex flex-wrap items-end gap-2">
            <div>
              <label className="text-[11px] text-gray-500">{t('procurement:po_vendorLabel')}</label>
              <input value={form.vendor_search}
                onChange={(e) => { setForm({ ...form, vendor_search: e.target.value, vendor_id: '' }); setVendorSearch(e.target.value) }}
                list={`po-quote-vendor-list-${po.id}`} className="form-input text-xs py-1 w-44" placeholder={t('procurement:po_vendorSearchPlaceholder')} />
              <datalist id={`po-quote-vendor-list-${po.id}`}>
                {vendors.map((v: any) => <option key={v.id} value={v.vendor_name} data-id={v.id} />)}
              </datalist>
              <select value={form.vendor_id} onChange={(e) => setForm({ ...form, vendor_id: e.target.value })} className="form-select text-xs py-1 w-44 mt-1">
                <option value="">{t('procurement:po_selectVendor')}</option>
                {vendors.map((v: any) => <option key={v.id} value={v.id}>{v.vendor_name}</option>)}
              </select>
            </div>
            <div>
              <label className="text-[11px] text-gray-500">{t('procurement:quote_amountLabel')}</label>
              <input type="number" value={form.quoted_amount} onChange={(e) => setForm({ ...form, quoted_amount: e.target.value })}
                className="form-input text-xs py-1 w-32" />
            </div>
            <div>
              <label className="text-[11px] text-gray-500">{t('procurement:po_dateLabel')}</label>
              <input type="date" value={form.quote_date} onChange={(e) => setForm({ ...form, quote_date: e.target.value })} className="form-input text-xs py-1" />
            </div>
            <div>
              <label className="text-[11px] text-gray-500">{t('procurement:po_paymentTermsLabel')}</label>
              <input value={form.payment_terms} onChange={(e) => setForm({ ...form, payment_terms: e.target.value })} className="form-input text-xs py-1 w-28" />
            </div>
            <div className="flex-1 min-w-32">
              <label className="text-[11px] text-gray-500">{t('procurement:quote_notesLabel')}</label>
              <input value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} className="form-input text-xs py-1" placeholder={t('common:optional')} />
            </div>
            <button onClick={() => addMutation.mutate()} disabled={addMutation.isPending || !form.vendor_id || !form.quoted_amount}
              className="btn-primary text-xs py-1.5">
              <Plus className="h-3.5 w-3.5" /> {addMutation.isPending ? t('common:saving') : t('procurement:quote_addBtn')}
            </button>
          </div>
        )}
      </td>
    </tr>
  )
}

export default function POPage() {
  const { t } = useTranslation(['procurement', 'common'])
  const { entityId, user } = useAuth()
  const [tab, setTab] = useState<Tab>('po')

  const myLevel = ROLE_LEVEL[user?.role ?? 'viewer'] ?? 1
  const canFinance = myLevel >= 2
  const canAdmin = myLevel >= 3

  // ── Vendor search (dipakai create PO & quote tender) ─────────────────────────
  const [vendorSearch, setVendorSearch] = useState('')
  const { data: vendorResults } = useQuery({
    queryKey: ['vendor-search', entityId, vendorSearch],
    queryFn: () => api.get('/vendors', { params: { entity_id: entityId, search: vendorSearch, limit: 20 } }).then((r) => r.data),
    enabled: !!entityId && tab === 'po',
  })
  const vendors: any[] = Array.isArray(vendorResults) ? vendorResults : (vendorResults?.items ?? [])

  // ── PO ───────────────────────────────────────────────────────────────────────
  const [poStatusFilter, setPoStatusFilter] = useState('')
  const { data: poData, isLoading: poLoading, refetch: refetchPO } = useQuery({
    queryKey: ['procurement-po', entityId, poStatusFilter],
    queryFn: () => api.get('/procurement/po', {
      params: { entity_id: entityId, status: poStatusFilter || undefined },
    }).then((r) => r.data),
    enabled: !!entityId,
  })
  const pos: any[] = Array.isArray(poData) ? poData : []
  const [expandedPoId, setExpandedPoId] = useState('')

  const [showPOForm, setShowPOForm] = useState(false)
  const [poForm, setPoForm] = useState({
    vendor_id: '', vendor_search: '', po_date: todayISO(), payment_terms: 'Net 30', currency: 'IDR', tax_amount: '0', notes: '',
  })
  const [poItems, setPoItems] = useState<POItemForm[]>([newPOItem()])
  const updatePOItem = (key: number, patch: Partial<POItemForm>) =>
    setPoItems((prev) => prev.map((it) => (it._key === key ? { ...it, ...patch } : it)))
  const addPOItem = () => setPoItems((prev) => [...prev, newPOItem()])
  const removePOItem = (key: number) =>
    setPoItems((prev) => (prev.length > 1 ? prev.filter((it) => it._key !== key) : prev))

  const { data: coaData } = useQuery({
    queryKey: ['coa', entityId],
    queryFn: () => api.get('/coa/', { params: { entity_id: entityId, limit: 1000 } }).then((r) => r.data),
    enabled: !!entityId && showPOForm,
  })
  const accounts: any[] = Array.isArray(coaData) ? coaData : []
  const expenseAccounts = accounts.filter((a: any) => !a.is_header && a.account_type === 'expense')

  const poItemsValid = poItems.length > 0 && poItems.every((it) =>
    it.description && parseFloat(it.qty) > 0 && parseFloat(it.unit_price) >= 0)

  const poSubtotal = poItems.reduce((sum, it) => sum + (parseFloat(it.qty) || 0) * (parseFloat(it.unit_price) || 0), 0)
  const poTax = parseFloat(poForm.tax_amount) || 0
  const poTotal = poSubtotal + poTax

  const createPOMutation = useMutation({
    mutationFn: () => api.post('/procurement/po', {
      entity_id: entityId,
      vendor_id: poForm.vendor_id,
      po_date: poForm.po_date,
      payment_terms: poForm.payment_terms,
      currency: poForm.currency,
      tax_amount: parseFloat(poForm.tax_amount) || 0,
      notes: poForm.notes || undefined,
      items: poItems.map((it, idx) => ({
        item_no: idx + 1,
        description: it.description,
        category: it.category,
        unit: it.unit || undefined,
        qty: parseFloat(it.qty) || 1,
        unit_price: parseFloat(it.unit_price) || 0,
        account_code: it.account_code || undefined,
        cost_center: it.cost_center || undefined,
      })),
    }),
    onSuccess: (res) => {
      showToast(t('procurement:po_createSuccess', { no: res.data.po_no }))
      setShowPOForm(false)
      setPoForm({ vendor_id: '', vendor_search: '', po_date: todayISO(), payment_terms: 'Net 30', currency: 'IDR', tax_amount: '0', notes: '' })
      setPoItems([newPOItem()])
      refetchPO()
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('procurement:po_createFailed'), 'error'),
  })

  const poSubmitMutation = useMutation({
    mutationFn: (id: string) => api.post(`/procurement/po/${id}/submit`),
    onSuccess: () => { showToast(t('procurement:actionSuccess')); refetchPO() },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('procurement:actionFailed'), 'error'),
  })
  const poApproveMutation = useMutation({
    mutationFn: ({ id, action }: { id: string; action: 'approved' | 'rejected' }) =>
      api.post(`/procurement/po/${id}/approve`, { action }),
    onSuccess: () => { showToast(t('procurement:actionSuccess')); refetchPO() },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('procurement:actionFailed'), 'error'),
  })
  const poSendMutation = useMutation({
    mutationFn: ({ id, email }: { id: string; email: string }) =>
      api.post(`/procurement/po/${id}/send`, null, { params: { sent_email: email } }),
    onSuccess: () => { showToast(t('procurement:actionSuccess')); refetchPO() },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('procurement:actionFailed'), 'error'),
  })
  const poCancelMutation = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) =>
      api.post(`/procurement/po/${id}/cancel`, null, { params: { reason } }),
    onSuccess: () => { showToast(t('procurement:actionSuccess')); refetchPO() },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('procurement:actionFailed'), 'error'),
  })

  // ── Goods Receipt ────────────────────────────────────────────────────────────
  const grEligiblePOs = pos.filter((p) => ['sent', 'partial_received', 'approved'].includes(p.status))
  const [grPoId, setGrPoId] = useState('')
  const { data: grPoDetail, refetch: refetchGrPo } = useQuery({
    queryKey: ['procurement-po-detail', grPoId],
    queryFn: () => api.get(`/procurement/po/${grPoId}`).then((r) => r.data),
    enabled: !!grPoId,
  })
  const [receivedQty, setReceivedQty] = useState<Record<string, string>>({})

  const grMutation = useMutation({
    mutationFn: () => api.post('/procurement/receipt', {
      po_id: grPoId,
      entity_id: entityId,
      receipt_date: todayISO(),
      items: Object.entries(receivedQty)
        .filter(([, v]) => parseFloat(v) > 0)
        .map(([po_item_id, v]) => ({ po_item_id, received_qty: parseFloat(v) })),
    }),
    onSuccess: (res) => {
      showToast(t('procurement:receipt_createSuccess', { no: res.data.receipt_no }))
      setReceivedQty({})
      refetchGrPo()
      refetchPO()
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('procurement:receipt_createFailed'), 'error'),
  })

  // ── Approval Matrix & Traceability ───────────────────────────────────────────
  const { data: matrixData, refetch: refetchMatrix } = useQuery({
    queryKey: ['procurement-matrix', entityId],
    queryFn: () => api.get('/procurement/approval-matrix', { params: { entity_id: entityId } }).then((r) => r.data),
    enabled: !!entityId,
  })
  const matrixRows: any[] = Array.isArray(matrixData) ? matrixData : []

  const [matrixForm, setMatrixForm] = useState({ level: '1', threshold_name: '', min_amount: '0', max_amount: '', approver_role: 'finance' })
  const saveMatrixMutation = useMutation({
    mutationFn: () => api.post('/procurement/approval-matrix', [{
      entity_id: entityId,
      level: parseInt(matrixForm.level, 10) || 1,
      threshold_name: matrixForm.threshold_name,
      min_amount: parseFloat(matrixForm.min_amount) || 0,
      max_amount: matrixForm.max_amount ? parseFloat(matrixForm.max_amount) : undefined,
      approver_role: matrixForm.approver_role,
    }]),
    onSuccess: () => {
      showToast(t('procurement:matrix_saveSuccess'))
      setMatrixForm({ level: '1', threshold_name: '', min_amount: '0', max_amount: '', approver_role: 'finance' })
      refetchMatrix()
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('procurement:matrix_saveFailed'), 'error'),
  })

  const { data: traceData, isLoading: traceLoading } = useQuery({
    queryKey: ['procurement-traceability', entityId],
    queryFn: () => api.get('/procurement/traceability', { params: { entity_id: entityId } }).then((r) => r.data),
    enabled: !!entityId && tab === 'matrix',
  })
  const traceRows: any[] = Array.isArray(traceData) ? traceData : []

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">{t('procurement:po_pageTitle')}</h1>
        <p className="text-sm text-gray-500 mt-0.5">{t('procurement:po_pageSubtitle')}</p>
      </div>

      <div className="border-b border-gray-200">
        <nav className="flex gap-1">
          {[
            { key: 'po', label: t('procurement:tab_po'), icon: <ShoppingCart className="h-4 w-4" /> },
            { key: 'receipt', label: t('procurement:tab_receipt'), icon: <PackageCheck className="h-4 w-4" /> },
            { key: 'matrix', label: t('procurement:tab_matrix'), icon: <Shield className="h-4 w-4" /> },
          ].map((tb) => (
            <button key={tb.key} onClick={() => setTab(tb.key as Tab)}
              className={`flex items-center gap-1.5 px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                tab === tb.key ? 'border-primary-600 text-primary-700' : 'border-transparent text-gray-500 hover:text-gray-700'
              }`}>
              {tb.icon} {tb.label}
            </button>
          ))}
        </nav>
      </div>

      {/* Tab: PO */}
      {tab === 'po' && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <select value={poStatusFilter} onChange={(e) => setPoStatusFilter(e.target.value)} className="form-select w-48">
              <option value="">{t('common:allStatus')}</option>
              {['open', 'draft', 'submitted', 'approved', 'sent', 'partial_received', 'received', 'closed', 'cancelled'].map((s) => (
                <option key={s} value={s}>{t(s, { ns: 'common' })}</option>
              ))}
            </select>
            {canFinance && (
              <button onClick={() => setShowPOForm((s) => !s)} className="btn-primary">
                <Plus className="h-4 w-4" /> {t('procurement:po_newBtn')}
              </button>
            )}
          </div>

          <p className="text-xs text-gray-400">{t('procurement:po_openHint')}</p>

          {showPOForm && (
            <Card>
              <p className="text-sm font-semibold text-gray-800 mb-4">{t('procurement:po_formTitle')}</p>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
                <div className="md:col-span-2">
                  <label className="form-label">{t('procurement:po_vendorLabel')}</label>
                  <input value={poForm.vendor_search}
                    onChange={(e) => { setPoForm({ ...poForm, vendor_search: e.target.value, vendor_id: '' }); setVendorSearch(e.target.value) }}
                    className="form-input" placeholder={t('procurement:po_vendorSearchPlaceholder')} />
                  <select value={poForm.vendor_id} onChange={(e) => setPoForm({ ...poForm, vendor_id: e.target.value })} className="form-select mt-1">
                    <option value="">{t('procurement:po_selectVendor')}</option>
                    {vendors.map((v: any) => <option key={v.id} value={v.id}>{v.vendor_name}</option>)}
                  </select>
                </div>
                <div>
                  <label className="form-label">{t('procurement:po_dateLabel')}</label>
                  <input type="date" value={poForm.po_date} onChange={(e) => setPoForm({ ...poForm, po_date: e.target.value })} className="form-input" />
                </div>
                <div>
                  <label className="form-label">{t('procurement:po_paymentTermsLabel')}</label>
                  <input value={poForm.payment_terms} onChange={(e) => setPoForm({ ...poForm, payment_terms: e.target.value })} className="form-input" />
                </div>
                <div>
                  <label className="form-label">{t('procurement:po_taxAmountLabel')}</label>
                  <input type="number" value={poForm.tax_amount} onChange={(e) => setPoForm({ ...poForm, tax_amount: e.target.value })} className="form-input" />
                </div>
                <div className="md:col-span-3">
                  <label className="form-label">{t('common:description')}</label>
                  <input value={poForm.notes} onChange={(e) => setPoForm({ ...poForm, notes: e.target.value })} className="form-input" placeholder={t('common:optional')} />
                </div>
              </div>

              <p className="text-sm font-medium text-gray-700 mb-2">{t('procurement:po_itemsTitle')}</p>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-xs text-gray-500 border-b border-gray-100">
                      <th className="text-left font-medium px-2 py-2 min-w-48">{t('procurement:pr_colDescription')}</th>
                      <th className="text-left font-medium px-2 py-2 min-w-28">{t('procurement:pr_colCategory')}</th>
                      <th className="text-right font-medium px-2 py-2 min-w-20">{t('procurement:pr_colQty')}</th>
                      <th className="text-right font-medium px-2 py-2 min-w-32">{t('procurement:pr_colUnitPrice')}</th>
                      <th className="text-left font-medium px-2 py-2 min-w-36">{t('procurement:po_colAccount')}</th>
                      <th className="text-left font-medium px-2 py-2 min-w-28">{t('procurement:po_colCostCenter')}</th>
                      <th className="px-2 py-2 w-8"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {poItems.map((it) => (
                      <tr key={it._key} className="border-b border-gray-50 last:border-0">
                        <td className="px-2 py-1.5">
                          <input value={it.description} onChange={(e) => updatePOItem(it._key, { description: e.target.value })} className="form-input text-xs py-1" />
                        </td>
                        <td className="px-2 py-1.5">
                          <select value={it.category} onChange={(e) => updatePOItem(it._key, { category: e.target.value })} className="form-select text-xs py-1">
                            {CATEGORY_OPTS.map((c) => <option key={c} value={c}>{t(`procurement:category_${c}`)}</option>)}
                          </select>
                        </td>
                        <td className="px-2 py-1.5">
                          <input type="number" value={it.qty} onChange={(e) => updatePOItem(it._key, { qty: e.target.value })} className="form-input text-xs py-1 text-right" />
                        </td>
                        <td className="px-2 py-1.5">
                          <input type="number" value={it.unit_price} onChange={(e) => updatePOItem(it._key, { unit_price: e.target.value })} className="form-input text-xs py-1 text-right" />
                        </td>
                        <td className="px-2 py-1.5">
                          <input list={`po-coa-${it._key}`} value={it.account_code} onChange={(e) => updatePOItem(it._key, { account_code: e.target.value })}
                            className="form-input font-mono text-xs py-1" placeholder="6-1-001" />
                          <datalist id={`po-coa-${it._key}`}>
                            {expenseAccounts.map((a: any) => <option key={a.account_code} value={a.account_code}>{a.account_name}</option>)}
                          </datalist>
                        </td>
                        <td className="px-2 py-1.5">
                          <input value={it.cost_center} onChange={(e) => updatePOItem(it._key, { cost_center: e.target.value })} className="form-input text-xs py-1" placeholder="IT" />
                        </td>
                        <td className="px-2 py-1.5 text-center">
                          <button type="button" onClick={() => removePOItem(it._key)} disabled={poItems.length <= 1}
                            className="text-gray-400 hover:text-red-500 disabled:opacity-30">
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div className="flex justify-end mt-3">
                <div className="w-56 space-y-1 text-sm">
                  <div className="flex justify-between text-gray-500">
                    <span>{t('procurement:colSubtotal')}</span>
                    <span>Rp {formatRupiah(poSubtotal)}</span>
                  </div>
                  <div className="flex justify-between text-gray-500">
                    <span>{t('procurement:po_taxAmountLabel')}</span>
                    <span>Rp {formatRupiah(poTax)}</span>
                  </div>
                  <div className="flex justify-between font-semibold text-gray-900 border-t border-gray-100 pt-1">
                    <span>{t('procurement:colTotal')}</span>
                    <span>Rp {formatRupiah(poTotal)}</span>
                  </div>
                </div>
              </div>

              <div className="flex items-center justify-between mt-3">
                <button type="button" onClick={addPOItem} className="inline-flex items-center gap-1 text-xs text-primary-600 hover:text-primary-700 font-medium">
                  <Plus className="h-3.5 w-3.5" /> {t('procurement:pr_addItemBtn')}
                </button>
                <div className="flex gap-3">
                  <button onClick={() => setShowPOForm(false)} className="btn-secondary">{t('common:cancel')}</button>
                  <button onClick={() => createPOMutation.mutate()} disabled={createPOMutation.isPending || !poForm.vendor_id || !poItemsValid} className="btn-primary">
                    {createPOMutation.isPending ? t('common:saving') : t('common:save')}
                  </button>
                </div>
              </div>
            </Card>
          )}

          <Card noPad>
            <CardHeader title={t('procurement:po_listTitle')} subtitle={t('procurement:po_listSubtitle', { count: pos.length })}
              actions={<button onClick={() => refetchPO()} className="btn-secondary"><RefreshCw className="h-4 w-4" /></button>} />
            {poLoading ? (
              <div className="flex justify-center py-16"><Spinner size="lg" /></div>
            ) : pos.length === 0 ? (
              <EmptyState title={t('procurement:po_emptyTitle')} />
            ) : (
              <div className="overflow-x-auto">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th></th>
                      <th>{t('procurement:po_colPoNo')}</th>
                      <th>{t('procurement:po_colVendor')}</th>
                      <th>{t('procurement:po_colDate')}</th>
                      <th className="right">{t('procurement:pr_colTotalAmount')}</th>
                      <th>{t('common:status')}</th>
                      <th>{t('common:action')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {pos.map((po: any) => {
                      const canExpand = po.status === 'open' || po.quote_count > 0
                      return (
                        <Fragment key={po.id}>
                          <tr>
                            <td>
                              {canExpand && (
                                <button onClick={() => setExpandedPoId((cur) => (cur === po.id ? '' : po.id))}
                                  className="text-gray-400 hover:text-gray-600">
                                  {expandedPoId === po.id ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
                                </button>
                              )}
                            </td>
                            <td className="text-sm font-medium">{po.po_no}</td>
                            <td className="text-sm text-gray-500">{po.vendor_name ?? t('procurement:quote_pendingVendor')}</td>
                            <td className="text-sm text-gray-500">{formatDate(po.po_date)}</td>
                            <td className="right">Rp {formatRupiah(po.total_amount)}</td>
                            <td><Badge status={po.status} /></td>
                            <td>
                              <div className="flex items-center gap-1.5 flex-wrap">
                                {po.status === 'draft' && canFinance && (
                                  <button onClick={() => poSubmitMutation.mutate(po.id)} disabled={poSubmitMutation.isPending}
                                    className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-md bg-blue-50 text-blue-700 hover:bg-blue-100">
                                    <Send className="h-3 w-3" /> {t('common:submit')}
                                  </button>
                                )}
                                {po.status === 'submitted' && canFinance && (
                                  <>
                                    <button onClick={() => poApproveMutation.mutate({ id: po.id, action: 'approved' })} disabled={poApproveMutation.isPending}
                                      className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-md bg-green-50 text-green-700 hover:bg-green-100">
                                      <CheckCircle className="h-3 w-3" /> {t('common:approve')}
                                    </button>
                                    <button onClick={() => poApproveMutation.mutate({ id: po.id, action: 'rejected' })} disabled={poApproveMutation.isPending}
                                      className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-md bg-red-50 text-red-700 hover:bg-red-100">
                                      <XCircle className="h-3 w-3" /> {t('common:reject')}
                                    </button>
                                  </>
                                )}
                                {po.status === 'approved' && canFinance && (
                                  <button onClick={() => {
                                    const email = window.prompt(t('procurement:po_sendEmailPrompt') as string, po.vendor_email ?? '')
                                    if (email) poSendMutation.mutate({ id: po.id, email })
                                  }} className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-md bg-primary-50 text-primary-700 hover:bg-primary-100">
                                    <Send className="h-3 w-3" /> {t('procurement:po_sendBtn')}
                                  </button>
                                )}
                                {!['open', 'closed', 'cancelled'].includes(po.status) && canFinance && (
                                  <button onClick={() => {
                                    const reason = window.prompt(t('procurement:po_cancelReasonPrompt') as string)
                                    if (reason) poCancelMutation.mutate({ id: po.id, reason })
                                  }} className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-md bg-red-50 text-red-700 hover:bg-red-100">
                                    <XCircle className="h-3 w-3" /> {t('common:cancel')}
                                  </button>
                                )}
                              </div>
                            </td>
                          </tr>
                          {expandedPoId === po.id && (
                            <POQuotesPanel po={po} colSpan={7} vendors={vendors} vendorSearch={vendorSearch}
                              setVendorSearch={setVendorSearch} canFinance={canFinance}
                              onSelected={() => { setExpandedPoId(''); refetchPO() }} />
                          )}
                        </Fragment>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </div>
      )}

      {/* Tab: Goods Receipt */}
      {tab === 'receipt' && (
        <div className="space-y-4">
          <Card>
            <label className="form-label">{t('procurement:receipt_selectPoLabel')}</label>
            <select value={grPoId} onChange={(e) => { setGrPoId(e.target.value); setReceivedQty({}) }} className="form-select max-w-md">
              <option value="">—</option>
              {grEligiblePOs.map((p: any) => (
                <option key={p.id} value={p.id}>{p.po_no} — {p.vendor_name} ({t(p.status, { ns: 'common' })})</option>
              ))}
            </select>
          </Card>

          {!grPoId ? (
            <EmptyState title={t('procurement:receipt_noPoTitle')} />
          ) : !grPoDetail ? (
            <div className="flex justify-center py-16"><Spinner size="lg" /></div>
          ) : (
            <Card noPad>
              <CardHeader title={t('procurement:receipt_itemsTitle')} subtitle={grPoDetail.po_no} />
              <div className="overflow-x-auto">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>{t('procurement:pr_colDescription')}</th>
                      <th className="right">{t('procurement:pr_colQty')}</th>
                      <th className="right">{t('procurement:receipt_colReceivedQty')}</th>
                      <th className="right">{t('procurement:receipt_colInputQty')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(grPoDetail.items ?? []).map((item: any) => {
                      const remaining = item.qty - item.received_qty
                      return (
                        <tr key={item.id}>
                          <td className="text-sm">{item.description}</td>
                          <td className="right">{item.qty}</td>
                          <td className="right text-gray-500">{item.received_qty}</td>
                          <td className="right">
                            <input type="number" min={0} max={remaining}
                              value={receivedQty[item.id] ?? ''}
                              onChange={(e) => setReceivedQty((prev) => ({ ...prev, [item.id]: e.target.value }))}
                              disabled={remaining <= 0}
                              className="form-input text-xs py-1 text-right w-28" placeholder="0" />
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
              <div className="px-3 py-2 flex justify-end border-t border-gray-100">
                <button onClick={() => grMutation.mutate()} disabled={grMutation.isPending} className="btn-primary">
                  <PackageCheck className="h-4 w-4" /> {grMutation.isPending ? t('common:saving') : t('procurement:receipt_submitBtn')}
                </button>
              </div>
            </Card>
          )}
        </div>
      )}

      {/* Tab: Approval Matrix & Traceability */}
      {tab === 'matrix' && (
        <div className="space-y-6">
          {canAdmin && (
            <Card>
              <p className="text-sm font-semibold text-gray-800 mb-4">{t('procurement:matrix_formTitle')}</p>
              <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
                <div>
                  <label className="form-label">{t('procurement:matrix_levelLabel')}</label>
                  <input type="number" value={matrixForm.level} onChange={(e) => setMatrixForm({ ...matrixForm, level: e.target.value })} className="form-input" />
                </div>
                <div>
                  <label className="form-label">{t('procurement:matrix_thresholdNameLabel')}</label>
                  <input value={matrixForm.threshold_name} onChange={(e) => setMatrixForm({ ...matrixForm, threshold_name: e.target.value })} className="form-input" placeholder="CFO" />
                </div>
                <div>
                  <label className="form-label">{t('procurement:matrix_minAmountLabel')}</label>
                  <input type="number" value={matrixForm.min_amount} onChange={(e) => setMatrixForm({ ...matrixForm, min_amount: e.target.value })} className="form-input" />
                </div>
                <div>
                  <label className="form-label">{t('procurement:matrix_maxAmountLabel')}</label>
                  <input type="number" value={matrixForm.max_amount} onChange={(e) => setMatrixForm({ ...matrixForm, max_amount: e.target.value })} className="form-input" placeholder={t('procurement:matrix_noLimit')} />
                </div>
                <div>
                  <label className="form-label">{t('procurement:matrix_approverRoleLabel')}</label>
                  <select value={matrixForm.approver_role} onChange={(e) => setMatrixForm({ ...matrixForm, approver_role: e.target.value })} className="form-select">
                    <option value="finance">finance</option>
                    <option value="admin">admin</option>
                  </select>
                </div>
              </div>
              <button onClick={() => saveMatrixMutation.mutate()} disabled={saveMatrixMutation.isPending || !matrixForm.threshold_name} className="btn-primary mt-4">
                {saveMatrixMutation.isPending ? t('common:saving') : t('common:save')}
              </button>
            </Card>
          )}

          <Card noPad>
            <CardHeader title={t('procurement:matrix_listTitle')} />
            {matrixRows.length === 0 ? (
              <EmptyState title={t('procurement:matrix_emptyTitle')} />
            ) : (
              <div className="overflow-x-auto">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>{t('procurement:matrix_levelLabel')}</th>
                      <th>{t('procurement:matrix_thresholdNameLabel')}</th>
                      <th className="right">{t('procurement:matrix_minAmountLabel')}</th>
                      <th className="right">{t('procurement:matrix_maxAmountLabel')}</th>
                      <th>{t('procurement:matrix_approverRoleLabel')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {matrixRows.map((m: any) => (
                      <tr key={m.id}>
                        <td className="text-sm">{m.level}</td>
                        <td className="text-sm">{m.threshold_name}</td>
                        <td className="right">Rp {formatRupiah(m.min_amount)}</td>
                        <td className="right">{m.max_amount != null ? `Rp ${formatRupiah(m.max_amount)}` : t('procurement:matrix_noLimit')}</td>
                        <td className="text-sm font-mono">{m.approver_role}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          <Card noPad>
            <CardHeader title={t('procurement:trace_title')} subtitle={t('procurement:trace_subtitle')} />
            {traceLoading ? (
              <div className="flex justify-center py-16"><Spinner size="lg" /></div>
            ) : traceRows.length === 0 ? (
              <EmptyState title={t('procurement:trace_emptyTitle')} />
            ) : (
              <div className="overflow-x-auto">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>{t('procurement:pr_colReqNo')}</th>
                      <th>{t('procurement:pr_colDepartment')}</th>
                      <th>{t('common:status')}</th>
                      <th>{t('procurement:po_colPoNo')}</th>
                      <th>{t('procurement:po_colVendor')}</th>
                      <th className="right">{t('procurement:pr_colTotalAmount')}</th>
                      <th>{t('common:status')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {traceRows.map((r: any, i: number) => (
                      <tr key={i}>
                        <td className="text-sm font-medium">{r.req_no}</td>
                        <td className="text-sm text-gray-500">{r.department ?? '—'}</td>
                        <td><Badge status={r.pr_status} /></td>
                        <td className="text-sm">{r.po_no ?? '—'}</td>
                        <td className="text-sm text-gray-500">{r.vendor_name ?? '—'}</td>
                        <td className="right">{r.po_value != null ? `Rp ${formatRupiah(r.po_value)}` : '—'}</td>
                        <td>{r.po_status ? <Badge status={r.po_status} /> : '—'}</td>
                      </tr>
                    ))}
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
