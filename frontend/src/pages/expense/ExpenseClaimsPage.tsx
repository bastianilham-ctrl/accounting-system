import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Plus, RefreshCw, ChevronDown, ChevronUp, Trash2, X,
  CheckCircle, XCircle, DollarSign, ClipboardCheck, Filter,
} from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext'
import api from '../../lib/api'
import { Card, CardHeader } from '../../components/ui/Card'
import Spinner from '../../components/ui/Spinner'
import EmptyState from '../../components/ui/EmptyState'
import Badge from '../../components/ui/Badge'
import { formatRupiah, formatDate, todayISO, firstDayOfMonth, lastDayOfMonth } from '../../lib/utils'
import { showToast } from '../../components/ui/Toast'

// ── Status config ─────────────────────────────────────────────────────────────
const STATUS_STEPS = ['draft', 'submitted', 'approved', 'verified', 'paid']

// ── Empty line factory ────────────────────────────────────────────────────────
let _k = 0
const newLine = () => ({
  _key: ++_k, category_id: '', expense_date: todayISO(),
  description: '', quantity: '1', unit_amount: '', is_billable: false,
})

export default function ExpenseClaimsPage() {
  const { t } = useTranslation(['expense', 'common'])
  const { entityId } = useAuth()
  const qc = useQueryClient()
  const STATUS_LABEL: Record<string, string> = {
    draft: t('common:draft'), submitted: t('expense:claims_statusSubmitted'), approved: t('common:approved'),
    rejected: t('expense:claims_statusRejected'), verified: t('expense:claims_statusVerified'), paid: t('expense:claims_statusPaid'),
  }
  const [tab, setTab] = useState<'claims' | 'advances'>('claims')

  // ── Filters ──────────────────────────────────────────────────────────────────
  const [statusFilter, setStatusFilter] = useState('')
  const [dateFrom, setDateFrom] = useState(firstDayOfMonth())
  const [dateTo, setDateTo]     = useState(lastDayOfMonth())

  // ── Fetch categories ─────────────────────────────────────────────────────────
  const { data: catData } = useQuery({
    queryKey: ['expense-categories', entityId],
    queryFn: () => api.get('/expense-claims/categories', { params: { entity_id: entityId } }).then(r => r.data),
    enabled: !!entityId,
  })
  const categories: any[] = Array.isArray(catData) ? catData : []

  // ── Fetch claims ──────────────────────────────────────────────────────────────
  const { data: claimsData, isLoading, refetch } = useQuery({
    queryKey: ['expense-claims', entityId, statusFilter, dateFrom, dateTo],
    queryFn: () =>
      api.get('/expense-claims', {
        params: {
          entity_id: entityId,
          status:    statusFilter || undefined,
          date_from: dateFrom    || undefined,
          date_to:   dateTo      || undefined,
          size: 50,
        },
      }).then(r => r.data),
    enabled: !!entityId,
  })
  const claims: any[] = Array.isArray(claimsData)
    ? claimsData
    : (claimsData?.items ?? claimsData?.claims ?? [])

  // ── Fetch advances ────────────────────────────────────────────────────────────
  const { data: advData, isLoading: advLoading, refetch: refetchAdv } = useQuery({
    queryKey: ['expense-advances', entityId],
    queryFn: () =>
      api.get('/expense-claims/advances', { params: { entity_id: entityId } }).then(r => r.data),
    enabled: !!entityId && tab === 'advances',
  })
  const advances: any[] = Array.isArray(advData) ? advData : (advData?.items ?? [])

  // ── New claim form ────────────────────────────────────────────────────────────
  const [showForm, setShowForm] = useState(false)
  const [claimForm, setClaimForm] = useState({
    employee_id: '', claim_date: todayISO(),
    period_from: firstDayOfMonth(), period_to: lastDayOfMonth(),
    purpose: '',
  })
  const [lines, setLines] = useState([newLine()])

  const addLine   = () => setLines(ls => [...ls, newLine()])
  const removeLine = (key: number) => setLines(ls => ls.filter(l => l._key !== key))
  const updateLine = (key: number, field: string, value: any) =>
    setLines(ls => ls.map(l => l._key === key ? { ...l, [field]: value } : l))

  const totalAmount = lines.reduce((s, l) => {
    const amt = parseFloat(l.unit_amount) || 0
    const qty = parseFloat(l.quantity)    || 1
    return s + amt * qty
  }, 0)

  const createClaimMutation = useMutation({
    mutationFn: () =>
      api.post('/expense-claims', {
        entity_id:   entityId,
        employee_id: claimForm.employee_id,
        claim_date:  claimForm.claim_date,
        period_from: claimForm.period_from,
        period_to:   claimForm.period_to,
        purpose:     claimForm.purpose,
        lines: lines.map(({ _key, ...l }) => ({
          ...l,
          quantity:    parseFloat(l.quantity)    || 1,
          unit_amount: parseFloat(l.unit_amount) || 0,
        })),
      }),
    onSuccess: () => {
      showToast(t('expense:claims_createSuccess'))
      setShowForm(false)
      setLines([newLine()])
      setClaimForm({ employee_id: '', claim_date: todayISO(), period_from: firstDayOfMonth(), period_to: lastDayOfMonth(), purpose: '' })
      qc.invalidateQueries({ queryKey: ['expense-claims'] })
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('expense:claims_createFailed'), 'error'),
  })

  // ── Workflow mutations ────────────────────────────────────────────────────────
  const submitMutation = useMutation({
    mutationFn: (id: string) => api.post(`/expense-claims/${id}/submit`, { submitted_by: 'user' }),
    onSuccess: () => { showToast(t('expense:claims_submitSuccess')); qc.invalidateQueries({ queryKey: ['expense-claims'] }) },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('expense:claims_submitFailed'), 'error'),
  })
  const approveMutation = useMutation({
    mutationFn: (id: string) => api.post(`/expense-claims/${id}/approve`, { approved_by: 'user' }),
    onSuccess: () => { showToast(t('expense:claims_approveSuccess')); qc.invalidateQueries({ queryKey: ['expense-claims'] }) },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('expense:claims_approveFailed'), 'error'),
  })
  const rejectMutation = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) =>
      api.post(`/expense-claims/${id}/reject`, { rejected_by: 'user', reason }),
    onSuccess: () => { showToast(t('expense:claims_rejectSuccess'), 'warning'); qc.invalidateQueries({ queryKey: ['expense-claims'] }) },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('expense:claims_rejectFailed'), 'error'),
  })
  const verifyMutation = useMutation({
    mutationFn: (id: string) => api.post(`/expense-claims/${id}/verify`, { verified_by: 'user' }),
    onSuccess: () => { showToast(t('expense:claims_verifySuccess')); qc.invalidateQueries({ queryKey: ['expense-claims'] }) },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('expense:claims_verifyFailed'), 'error'),
  })
  const payMutation = useMutation({
    mutationFn: (id: string) =>
      api.post(`/expense-claims/${id}/pay`, {
        payment_method: 'bank_transfer',
        paid_by:        'user',
        payment_date:   todayISO(),
      }),
    onSuccess: () => { showToast(t('expense:claims_paySuccess')); qc.invalidateQueries({ queryKey: ['expense-claims'] }) },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('expense:claims_payFailed'), 'error'),
  })

  // ── Advance form ──────────────────────────────────────────────────────────────
  const [showAdvForm, setShowAdvForm] = useState(false)
  const [advForm, setAdvForm] = useState({
    employee_id: '', advance_date: todayISO(),
    purpose: '', amount_requested: '',
  })
  const createAdvMutation = useMutation({
    mutationFn: () =>
      api.post('/expense-claims/advances', {
        entity_id:        entityId,
        employee_id:      advForm.employee_id,
        advance_date:     advForm.advance_date,
        purpose:          advForm.purpose,
        amount_requested: parseFloat(advForm.amount_requested) || 0,
      }),
    onSuccess: () => {
      showToast(t('expense:advances_createSuccess'))
      setShowAdvForm(false)
      setAdvForm({ employee_id: '', advance_date: todayISO(), purpose: '', amount_requested: '' })
      qc.invalidateQueries({ queryKey: ['expense-advances'] })
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('expense:advances_createFailed'), 'error'),
  })

  // ── Stats ─────────────────────────────────────────────────────────────────────
  const pending  = claims.filter(c => ['submitted', 'approved'].includes(c.status)).length
  const totalClaimed = claims.reduce((s, c) => s + (c.total_amount ?? 0), 0)
  const totalApproved = claims.filter(c => !['draft','rejected'].includes(c.status))
    .reduce((s, c) => s + (c.approved_amount ?? c.total_amount ?? 0), 0)

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">{t('expense:claims_pageTitle')}</h1>
          <p className="text-sm text-gray-500 mt-0.5">{t('expense:claims_pageSubtitle')}</p>
        </div>
        <button onClick={() => setShowForm(true)} className="btn-primary">
          <Plus className="h-4 w-4" /> {t('expense:claims_newClaim')}
        </button>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-3 gap-4">
        <div className="card p-4">
          <p className="text-xs text-gray-500 uppercase">{t('expense:claims_statTotalPeriod')}</p>
          <p className="text-2xl font-bold text-gray-900 mt-1">{claims.length}</p>
          <p className="text-xs text-gray-400 mt-0.5">{t('expense:claims_statPendingAction', { count: pending })}</p>
        </div>
        <div className="card p-4">
          <p className="text-xs text-gray-500 uppercase">{t('expense:claims_statTotalClaimed')}</p>
          <p className="text-xl font-bold text-gray-900 mt-1">Rp {formatRupiah(totalClaimed)}</p>
        </div>
        <div className="card p-4 border-l-4 border-green-400">
          <p className="text-xs text-gray-500 uppercase">{t('expense:claims_statTotalApproved')}</p>
          <p className="text-xl font-bold text-green-700 mt-1">Rp {formatRupiah(totalApproved)}</p>
        </div>
      </div>

      {/* New Claim Form */}
      {showForm && (
        <Card>
          <div className="flex items-center justify-between mb-4">
            <p className="text-sm font-semibold text-gray-700">{t('expense:claims_formTitle')}</p>
            <button onClick={() => setShowForm(false)} className="text-gray-400 hover:text-gray-600">
              <X className="h-5 w-5" />
            </button>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
            <div>
              <label className="form-label">{t('expense:claims_employeeId')}</label>
              <input value={claimForm.employee_id}
                onChange={e => setClaimForm({ ...claimForm, employee_id: e.target.value })}
                className="form-input font-mono text-sm" placeholder="UUID karyawan" />
            </div>
            <div>
              <label className="form-label">{t('expense:claims_claimDate')}</label>
              <input type="date" value={claimForm.claim_date}
                onChange={e => setClaimForm({ ...claimForm, claim_date: e.target.value })}
                className="form-input" />
            </div>
            <div>
              <label className="form-label">{t('expense:claims_periodFrom')}</label>
              <input type="date" value={claimForm.period_from}
                onChange={e => setClaimForm({ ...claimForm, period_from: e.target.value })}
                className="form-input" />
            </div>
            <div>
              <label className="form-label">{t('expense:claims_periodTo')}</label>
              <input type="date" value={claimForm.period_to}
                onChange={e => setClaimForm({ ...claimForm, period_to: e.target.value })}
                className="form-input" />
            </div>
            <div className="col-span-2 md:col-span-4">
              <label className="form-label">{t('expense:claims_purpose')}</label>
              <input value={claimForm.purpose}
                onChange={e => setClaimForm({ ...claimForm, purpose: e.target.value })}
                className="form-input" placeholder={t('expense:claims_purposePlaceholder')} />
            </div>
          </div>

          {/* Lines */}
          <div className="mb-3">
            <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">{t('expense:claims_linesTitle')}</p>
            <div className="border rounded-lg overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="text-left px-3 py-2 font-medium text-gray-600 w-40">{t('expense:claims_colCategory')}</th>
                    <th className="text-left px-3 py-2 font-medium text-gray-600 w-32">{t('common:date')}</th>
                    <th className="text-left px-3 py-2 font-medium text-gray-600">{t('common:description')}</th>
                    <th className="text-right px-3 py-2 font-medium text-gray-600 w-16">{t('expense:claims_colQty')}</th>
                    <th className="text-right px-3 py-2 font-medium text-gray-600 w-32">{t('expense:claims_colUnitAmount')}</th>
                    <th className="text-right px-3 py-2 font-medium text-gray-600 w-32">{t('common:amount')}</th>
                    <th className="w-8" />
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {lines.map(l => {
                    const total = (parseFloat(l.unit_amount) || 0) * (parseFloat(l.quantity) || 1)
                    return (
                      <tr key={l._key}>
                        <td className="px-2 py-1.5">
                          <select value={l.category_id}
                            onChange={e => updateLine(l._key, 'category_id', e.target.value)}
                            className="form-select text-xs">
                            <option value="">{t('expense:claims_selectPlaceholder')}</option>
                            {categories.map((c: any) => (
                              <option key={c.id} value={c.id}>{c.category_name}</option>
                            ))}
                          </select>
                        </td>
                        <td className="px-2 py-1.5">
                          <input type="date" value={l.expense_date}
                            onChange={e => updateLine(l._key, 'expense_date', e.target.value)}
                            className="form-input text-xs" />
                        </td>
                        <td className="px-2 py-1.5">
                          <input value={l.description}
                            onChange={e => updateLine(l._key, 'description', e.target.value)}
                            className="form-input text-xs w-full" placeholder={t('expense:claims_lineDescriptionPlaceholder')} />
                        </td>
                        <td className="px-2 py-1.5">
                          <input type="number" value={l.quantity}
                            onChange={e => updateLine(l._key, 'quantity', e.target.value)}
                            className="form-input text-xs text-right w-full" min="1" />
                        </td>
                        <td className="px-2 py-1.5">
                          <input type="number" value={l.unit_amount}
                            onChange={e => updateLine(l._key, 'unit_amount', e.target.value)}
                            className="form-input text-xs text-right w-full" placeholder="0" />
                        </td>
                        <td className="px-3 py-1.5 text-right font-medium text-sm">
                          {formatRupiah(total)}
                        </td>
                        <td className="px-1">
                          {lines.length > 1 && (
                            <button onClick={() => removeLine(l._key)}
                              className="text-gray-300 hover:text-red-400">
                              <Trash2 className="h-4 w-4" />
                            </button>
                          )}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
                <tfoot className="bg-gray-50">
                  <tr>
                    <td colSpan={5} className="px-3 py-2 text-right text-sm font-semibold text-gray-700">{t('common:total')}:</td>
                    <td className="px-3 py-2 text-right font-bold text-primary-700">
                      Rp {formatRupiah(totalAmount)}
                    </td>
                    <td />
                  </tr>
                </tfoot>
              </table>
            </div>
            <button onClick={addLine} className="mt-2 text-xs text-primary-600 hover:underline flex items-center gap-1">
              <Plus className="h-3 w-3" /> {t('expense:claims_addLine')}
            </button>
          </div>

          <div className="flex gap-3">
            <button onClick={() => createClaimMutation.mutate()}
              disabled={createClaimMutation.isPending || !claimForm.employee_id || !claimForm.purpose}
              className="btn-primary">
              {createClaimMutation.isPending ? t('common:saving') : t('expense:claims_saveDraft')}
            </button>
            <button onClick={() => setShowForm(false)} className="btn-secondary">{t('common:cancel')}</button>
          </div>
        </Card>
      )}

      {/* Tabs */}
      <div className="border-b border-gray-200">
        <nav className="flex gap-1">
          {[
            { key: 'claims',   label: t('expense:claims_tabClaims') },
            { key: 'advances', label: t('expense:claims_tabAdvances') },
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

      {/* Tab: Claims */}
      {tab === 'claims' && (
        <Card noPad>
          <CardHeader
            title={t('expense:claims_listTitle')}
            subtitle={t('expense:claims_listSubtitle', { count: claims.length })}
            actions={
              <div className="flex items-center gap-2 flex-wrap">
                <Filter className="h-4 w-4 text-gray-400" />
                <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)} className="form-select w-36">
                  <option value="">{t('common:allStatus')}</option>
                  {Object.entries(STATUS_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                </select>
                <input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)} className="form-input w-36" />
                <span className="text-gray-400 text-sm">{t('expense:claims_dateRangeTo')}</span>
                <input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)} className="form-input w-36" />
                <button onClick={() => refetch()} className="btn-secondary"><RefreshCw className="h-4 w-4" /></button>
              </div>
            }
          />

          {isLoading ? (
            <div className="flex justify-center py-16"><Spinner size="lg" /></div>
          ) : claims.length === 0 ? (
            <EmptyState title={t('expense:claims_emptyTitle')} description={t('expense:claims_emptyDescription')} />
          ) : (
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>{t('expense:claims_colClaimNo')}</th>
                    <th>{t('expense:claims_colEmployee')}</th>
                    <th>{t('expense:claims_colPurpose')}</th>
                    <th>{t('expense:claims_colPeriod')}</th>
                    <th className="right">{t('expense:claims_colTotalClaimed')}</th>
                    <th className="right">{t('expense:claims_colApproved')}</th>
                    <th>{t('common:status')}</th>
                    <th>{t('common:action')}</th>
                  </tr>
                </thead>
                <tbody>
                  {claims.map((c: any) => (
                    <tr key={c.id}>
                      <td className="font-mono text-xs text-gray-500">{c.claim_no ?? c.id?.slice(0, 8)}</td>
                      <td className="text-sm font-medium">{c.employee_name ?? c.employee_id?.slice(0, 8)}</td>
                      <td className="text-sm max-w-xs truncate" title={c.purpose}>{c.purpose}</td>
                      <td className="text-xs text-gray-500 whitespace-nowrap">
                        {formatDate(c.period_from)} – {formatDate(c.period_to)}
                      </td>
                      <td className="right">Rp {formatRupiah(c.total_amount)}</td>
                      <td className="right font-medium text-green-700">
                        {c.approved_amount != null
                          ? `Rp ${formatRupiah(c.approved_amount)}`
                          : <span className="text-gray-300">—</span>}
                      </td>
                      <td><Badge status={c.status} /></td>
                      <td>
                        <div className="flex items-center gap-1 flex-wrap">
                          {c.status === 'draft' && (
                            <ActionBtn icon={<CheckCircle className="h-3 w-3" />}
                              label={t('expense:claims_actionSubmit')} color="blue"
                              onClick={() => submitMutation.mutate(c.id)} />
                          )}
                          {c.status === 'submitted' && (
                            <>
                              <ActionBtn icon={<CheckCircle className="h-3 w-3" />}
                                label={t('common:approve')} color="green"
                                onClick={() => approveMutation.mutate(c.id)} />
                              <ActionBtn icon={<XCircle className="h-3 w-3" />}
                                label={t('common:reject')} color="red"
                                onClick={() => {
                                  const reason = prompt(t('expense:claims_rejectReasonPrompt'))
                                  if (reason) rejectMutation.mutate({ id: c.id, reason })
                                }} />
                            </>
                          )}
                          {c.status === 'approved' && (
                            <ActionBtn icon={<ClipboardCheck className="h-3 w-3" />}
                              label={t('expense:claims_actionVerify')} color="purple"
                              onClick={() => verifyMutation.mutate(c.id)} />
                          )}
                          {c.status === 'verified' && (
                            <ActionBtn icon={<DollarSign className="h-3 w-3" />}
                              label={t('expense:claims_actionPay')} color="green"
                              onClick={() => payMutation.mutate(c.id)} />
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

      {/* Tab: Advances */}
      {tab === 'advances' && (
        <div className="space-y-4">
          {/* Create advance */}
          {showAdvForm ? (
            <Card>
              <div className="flex items-center justify-between mb-3">
                <p className="text-sm font-semibold text-gray-700">{t('expense:advances_formTitle')}</p>
                <button onClick={() => setShowAdvForm(false)} className="text-gray-400 hover:text-gray-600">
                  <X className="h-4 w-4" />
                </button>
              </div>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <div>
                  <label className="form-label">{t('expense:claims_employeeId')}</label>
                  <input value={advForm.employee_id}
                    onChange={e => setAdvForm({ ...advForm, employee_id: e.target.value })}
                    className="form-input font-mono text-sm" placeholder="UUID karyawan" />
                </div>
                <div>
                  <label className="form-label">{t('common:date')}</label>
                  <input type="date" value={advForm.advance_date}
                    onChange={e => setAdvForm({ ...advForm, advance_date: e.target.value })}
                    className="form-input" />
                </div>
                <div>
                  <label className="form-label">{t('expense:advances_amountRequested')}</label>
                  <input type="number" value={advForm.amount_requested}
                    onChange={e => setAdvForm({ ...advForm, amount_requested: e.target.value })}
                    className="form-input" placeholder="0" />
                </div>
                <div className="col-span-1 md:col-span-4">
                  <label className="form-label">{t('expense:claims_colPurpose')}</label>
                  <input value={advForm.purpose}
                    onChange={e => setAdvForm({ ...advForm, purpose: e.target.value })}
                    className="form-input" placeholder={t('expense:advances_purposePlaceholder')} />
                </div>
              </div>
              <div className="flex gap-3 mt-4">
                <button onClick={() => createAdvMutation.mutate()}
                  disabled={createAdvMutation.isPending || !advForm.employee_id || !advForm.amount_requested}
                  className="btn-primary">
                  {createAdvMutation.isPending ? t('common:saving') : t('expense:advances_submitBtn')}
                </button>
                <button onClick={() => setShowAdvForm(false)} className="btn-secondary">{t('common:cancel')}</button>
              </div>
            </Card>
          ) : (
            <button onClick={() => setShowAdvForm(true)} className="btn-secondary">
              <Plus className="h-4 w-4" /> {t('expense:advances_newRequest')}
            </button>
          )}

          <Card noPad>
            <CardHeader
              title={t('expense:advances_listTitle')}
              subtitle={t('expense:advances_listSubtitle', { count: advances.length })}
              actions={<button onClick={() => refetchAdv()} className="btn-secondary"><RefreshCw className="h-4 w-4" /></button>}
            />
            {advLoading ? (
              <div className="flex justify-center py-16"><Spinner size="lg" /></div>
            ) : advances.length === 0 ? (
              <EmptyState title={t('expense:advances_emptyTitle')} />
            ) : (
              <div className="overflow-x-auto">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>{t('expense:advances_colAdvanceNo')}</th>
                      <th>{t('expense:claims_colEmployee')}</th>
                      <th>{t('expense:claims_colPurpose')}</th>
                      <th>{t('common:date')}</th>
                      <th className="right">{t('expense:advances_colRequested')}</th>
                      <th className="right">{t('expense:advances_colDisbursed')}</th>
                      <th className="right">{t('expense:advances_colSettled')}</th>
                      <th className="right">{t('expense:advances_colBalance')}</th>
                      <th>{t('common:status')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {advances.map((a: any) => (
                      <tr key={a.id}>
                        <td className="font-mono text-xs text-gray-500">{a.advance_no ?? a.id?.slice(0, 8)}</td>
                        <td className="text-sm font-medium">{a.employee_name}</td>
                        <td className="text-sm max-w-xs truncate" title={a.purpose}>{a.purpose}</td>
                        <td className="text-sm text-gray-500 whitespace-nowrap">{formatDate(a.advance_date)}</td>
                        <td className="right">Rp {formatRupiah(a.amount_requested)}</td>
                        <td className="right">
                          {a.amount_disbursed > 0
                            ? `Rp ${formatRupiah(a.amount_disbursed)}`
                            : <span className="text-gray-300">—</span>}
                        </td>
                        <td className="right text-green-700">
                          {a.amount_settled > 0
                            ? `Rp ${formatRupiah(a.amount_settled)}`
                            : <span className="text-gray-300">—</span>}
                        </td>
                        <td className={`right font-semibold ${(a.balance_due ?? 0) > 0 ? 'text-red-600' : 'text-gray-400'}`}>
                          {a.balance_due != null ? `Rp ${formatRupiah(a.balance_due)}` : '—'}
                        </td>
                        <td><Badge status={a.status} /></td>
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

function ActionBtn({ icon, label, color, onClick }: {
  icon: React.ReactNode; label: string; color: string; onClick: () => void
}) {
  const colors: Record<string, string> = {
    blue:   'bg-blue-50   text-blue-600   hover:bg-blue-100',
    green:  'bg-green-50  text-green-600  hover:bg-green-100',
    red:    'bg-red-50    text-red-600    hover:bg-red-100',
    purple: 'bg-purple-50 text-purple-600 hover:bg-purple-100',
  }
  return (
    <button onClick={onClick}
      className={`inline-flex items-center gap-1 text-xs px-2 py-1 rounded-md ${colors[color] ?? colors.blue}`}>
      {icon} {label}
    </button>
  )
}
