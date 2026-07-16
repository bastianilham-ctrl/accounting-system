import { Fragment, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { useQuery, useMutation } from '@tanstack/react-query'
import { Plus, RefreshCw, Send, CheckCircle, XCircle, Trash2, ChevronDown, ChevronUp } from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext'
import api from '../../lib/api'
import { Card, CardHeader } from '../../components/ui/Card'
import Spinner from '../../components/ui/Spinner'
import EmptyState from '../../components/ui/EmptyState'
import Badge from '../../components/ui/Badge'
import { formatRupiah, currentYear } from '../../lib/utils'
import { showToast } from '../../components/ui/Toast'

const CATEGORY_OPTS = ['goods', 'services', 'asset']

type PRItemForm = {
  _key: number; description: string; category: string; unit: string
  qty: string; unit_price: string; budget_line_id: string; item_id: string; notes: string
}
let _prk = 0
const newPRItem = (): PRItemForm => ({
  _key: ++_prk, description: '', category: 'services', unit: '',
  qty: '1', unit_price: '', budget_line_id: '', item_id: '', notes: '',
})

type EvalResult = { status: 'SUCCESS' | 'OVERLIMIT' | 'BLOCKED'; message: string; budget_code?: string; remaining_budget?: number }

function EvaluateLineBadge({ entityId, itemId, costCenter, qty, unitPrice, onResult }: {
  entityId: string; itemId: string; costCenter: string; qty: number; unitPrice: number
  onResult: (r: EvalResult | null) => void
}) {
  const { t } = useTranslation(['procurement', 'common'])
  const enabled = !!entityId && !!itemId && !!costCenter && qty > 0
  const { data, isFetching } = useQuery({
    queryKey: ['pr-evaluate-line', entityId, itemId, costCenter, qty, unitPrice],
    queryFn: () => api.post('/procurement/pr/evaluate-line', {
      entity_id: entityId, item_id: itemId, cost_center: costCenter, qty, unit_price: unitPrice,
    }).then((r) => r.data as EvalResult),
    enabled,
    staleTime: 0,
  })

  useEffect(() => {
    onResult(enabled ? (data ?? null) : null)
  }, [enabled, data, onResult])

  if (!enabled) return <span className="text-xs text-gray-400">{t('procurement:pr_evalNeedItemCc')}</span>
  if (isFetching && !data) return <span className="text-xs text-gray-400">{t('procurement:pr_evalChecking')}</span>
  if (!data) return null

  const isOk = data.status === 'SUCCESS'
  return (
    <div className="space-y-0.5">
      <span className={`inline-flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full ${
        isOk ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'
      }`}>
        {isOk ? '🟢' : '🔴'} {t(`procurement:pr_evalStatus_${data.status}`)}
      </span>
      {data.budget_code && (
        <p className="text-[11px] text-gray-400">
          {data.budget_code} · {t('procurement:pr_available')}: Rp {formatRupiah(data.remaining_budget ?? 0)}
        </p>
      )}
      {!isOk && <p className="text-[11px] text-red-500">{data.message}</p>}
    </div>
  )
}

function PRStepsRow({ prId, colSpan }: { prId: string; colSpan: number }) {
  const { t } = useTranslation(['procurement', 'common'])
  const { data, isLoading } = useQuery({
    queryKey: ['procurement-pr-detail', prId],
    queryFn: () => api.get(`/procurement/pr/${prId}`).then((r) => r.data),
  })
  const steps: any[] = data?.steps ?? []
  return (
    <tr>
      <td colSpan={colSpan} className="bg-gray-50 px-4 py-3">
        {isLoading ? (
          <Spinner size="sm" />
        ) : steps.length === 0 ? (
          <p className="text-xs text-gray-400">{t('procurement:pr_noStepsYet')}</p>
        ) : (
          <ol className="flex items-center gap-2 flex-wrap text-xs">
            {steps.map((s: any, i: number) => (
              <li key={s.id} className="flex items-center gap-2">
                {i > 0 && <span className="text-gray-300">→</span>}
                <span className={`inline-flex items-center gap-1 px-2 py-1 rounded-md border ${
                  s.status === 'approved' ? 'border-green-200 bg-green-50 text-green-700'
                  : s.status === 'rejected' ? 'border-red-200 bg-red-50 text-red-700'
                  : s.status === 'skipped' ? 'border-gray-200 bg-gray-100 text-gray-400'
                  : 'border-amber-200 bg-amber-50 text-amber-700'
                }`}>
                  {s.status === 'approved' && <CheckCircle className="h-3 w-3" />}
                  {s.status === 'rejected' && <XCircle className="h-3 w-3" />}
                  {t('procurement:pr_stepLevel', { level: s.level })}: {s.approver_label}
                </span>
              </li>
            ))}
          </ol>
        )}
      </td>
    </tr>
  )
}

export default function PRPage() {
  const { t } = useTranslation(['procurement', 'common'])
  const { entityId } = useAuth()

  const [prStatusFilter, setPrStatusFilter] = useState('')
  const { data: prData, isLoading: prLoading, refetch: refetchPR } = useQuery({
    queryKey: ['procurement-pr', entityId, prStatusFilter],
    queryFn: () => api.get('/procurement/pr', {
      params: { entity_id: entityId, status: prStatusFilter || undefined },
    }).then((r) => r.data),
    enabled: !!entityId,
  })
  const prs: any[] = Array.isArray(prData) ? prData : []

  const [expandedPrId, setExpandedPrId] = useState('')

  const [showPRForm, setShowPRForm] = useState(false)
  const [prForm, setPrForm] = useState({ department: '', cost_center: '', requested_by: '', required_date: '', purpose: '' })
  const [prItems, setPrItems] = useState<PRItemForm[]>([newPRItem()])
  const [refYear, setRefYear] = useState(String(currentYear()))

  const updatePRItem = (key: number, patch: Partial<PRItemForm>) =>
    setPrItems((prev) => prev.map((it) => (it._key === key ? { ...it, ...patch } : it)))
  const addPRItem = () => setPrItems((prev) => [...prev, newPRItem()])
  const removePRItem = (key: number) =>
    setPrItems((prev) => (prev.length > 1 ? prev.filter((it) => it._key !== key) : prev))

  const { data: budgetRefData } = useQuery({
    queryKey: ['budget-lines-ref', entityId, refYear, prForm.cost_center],
    queryFn: () => api.get('/budget/lines', {
      params: { entity_id: entityId, year: refYear, cost_center: prForm.cost_center || undefined },
    }).then((r) => r.data),
    enabled: !!entityId && showPRForm,
  })
  const budgetRefs: any[] = Array.isArray(budgetRefData) ? budgetRefData : []

  const { data: itemMasterData } = useQuery({
    queryKey: ['procurement-items-ref', entityId],
    queryFn: () => api.get('/procurement/items', { params: { entity_id: entityId } }).then((r) => r.data),
    enabled: !!entityId && showPRForm,
  })
  const itemMasterOptions: any[] = Array.isArray(itemMasterData) ? itemMasterData : []

  const [evalResults, setEvalResults] = useState<Record<number, EvalResult | null>>({})
  const setEvalResult = (key: number, r: EvalResult | null) =>
    setEvalResults((prev) => (prev[key] === r ? prev : { ...prev, [key]: r }))

  const prItemsValid = prItems.length > 0 && prItems.every((it) => {
    if (!it.description || !(parseFloat(it.qty) > 0) || !(parseFloat(it.unit_price) >= 0)) return false
    if (it.item_id) return evalResults[it._key]?.status === 'SUCCESS'
    return !!it.budget_line_id
  })

  const prSubtotal = prItems.reduce((sum, it) => sum + (parseFloat(it.qty) || 0) * (parseFloat(it.unit_price) || 0), 0)

  const createPRMutation = useMutation({
    mutationFn: () => api.post('/procurement/pr', {
      entity_id: entityId,
      department: prForm.department || undefined,
      cost_center: prForm.cost_center || undefined,
      requested_by: prForm.requested_by,
      required_date: prForm.required_date || undefined,
      purpose: prForm.purpose || undefined,
      items: prItems.map((it, idx) => ({
        item_no: idx + 1,
        description: it.description,
        category: it.category,
        unit: it.unit || undefined,
        qty: parseFloat(it.qty) || 1,
        unit_price: parseFloat(it.unit_price) || 0,
        item_id: it.item_id || undefined,
        budget_line_id: it.item_id ? undefined : (it.budget_line_id || undefined),
        notes: it.notes || undefined,
      })),
    }),
    onSuccess: (res) => {
      showToast(t('procurement:pr_createSuccess', { no: res.data.req_no }))
      setShowPRForm(false)
      setPrForm({ department: '', cost_center: '', requested_by: '', required_date: '', purpose: '' })
      setPrItems([newPRItem()])
      setEvalResults({})
      refetchPR()
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('procurement:pr_createFailed'), 'error'),
  })

  const prSubmitMutation = useMutation({
    mutationFn: (id: string) => api.post(`/procurement/pr/${id}/submit`),
    onSuccess: () => { showToast(t('procurement:actionSuccess')); refetchPR() },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('procurement:actionFailed'), 'error'),
  })
  const prApproveMutation = useMutation({
    mutationFn: ({ id, action }: { id: string; action: 'approved' | 'rejected' }) =>
      api.post(`/procurement/pr/${id}/approve`, { action }),
    onSuccess: () => { showToast(t('procurement:actionSuccess')); refetchPR() },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('procurement:actionFailed'), 'error'),
  })

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">{t('procurement:pr_pageTitle')}</h1>
        <p className="text-sm text-gray-500 mt-0.5">{t('procurement:pr_pageSubtitle')}</p>
      </div>

      <div className="flex items-center justify-between">
        <select value={prStatusFilter} onChange={(e) => setPrStatusFilter(e.target.value)} className="form-select w-48">
          <option value="">{t('common:allStatus')}</option>
          {['draft', 'submitted', 'approved', 'rejected', 'converted', 'cancelled'].map((s) => (
            <option key={s} value={s}>{t(s, { ns: 'common' })}</option>
          ))}
        </select>
        <button onClick={() => setShowPRForm((s) => !s)} className="btn-primary">
          <Plus className="h-4 w-4" /> {t('procurement:pr_newBtn')}
        </button>
      </div>

      {showPRForm && (
        <Card>
          <p className="text-sm font-semibold text-gray-800 mb-4">{t('procurement:pr_formTitle')}</p>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
            <div>
              <label className="form-label">{t('procurement:pr_departmentLabel')}</label>
              <input value={prForm.department} onChange={(e) => setPrForm({ ...prForm, department: e.target.value })} className="form-input" />
            </div>
            <div>
              <label className="form-label">{t('procurement:pr_costCenterLabel')}</label>
              <input value={prForm.cost_center} onChange={(e) => setPrForm({ ...prForm, cost_center: e.target.value })} className="form-input" placeholder="IT" />
            </div>
            <div>
              <label className="form-label">{t('procurement:pr_requestedByLabel')}</label>
              <input value={prForm.requested_by} onChange={(e) => setPrForm({ ...prForm, requested_by: e.target.value })} className="form-input" />
            </div>
            <div>
              <label className="form-label">{t('procurement:pr_requiredDateLabel')}</label>
              <input type="date" value={prForm.required_date} onChange={(e) => setPrForm({ ...prForm, required_date: e.target.value })} className="form-input" />
            </div>
          </div>
          <div className="mb-4">
            <label className="form-label">{t('procurement:pr_purposeLabel')}</label>
            <input value={prForm.purpose} onChange={(e) => setPrForm({ ...prForm, purpose: e.target.value })} className="form-input" placeholder={t('common:optional')} />
          </div>

          <div className="flex items-center justify-between mb-2">
            <p className="text-sm font-medium text-gray-700">{t('procurement:pr_itemsTitle')}</p>
            <div className="flex items-center gap-2">
              <label className="text-xs text-gray-500">{t('procurement:pr_refYearLabel')}</label>
              <input type="number" value={refYear} onChange={(e) => setRefYear(e.target.value)} className="form-input w-24 text-xs py-1" />
            </div>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-gray-500 border-b border-gray-100">
                  <th className="text-left font-medium px-2 py-2 min-w-44">{t('procurement:pr_colItem')}</th>
                  <th className="text-left font-medium px-2 py-2 min-w-48">{t('procurement:pr_colDescription')}</th>
                  <th className="text-left font-medium px-2 py-2 min-w-28">{t('procurement:pr_colCategory')}</th>
                  <th className="text-right font-medium px-2 py-2 min-w-20">{t('procurement:pr_colQty')}</th>
                  <th className="text-right font-medium px-2 py-2 min-w-32">{t('procurement:pr_colUnitPrice')}</th>
                  <th className="text-left font-medium px-2 py-2 min-w-56">{t('procurement:pr_colBudgetRef')}</th>
                  <th className="px-2 py-2 w-8"></th>
                </tr>
              </thead>
              <tbody>
                {prItems.map((it) => {
                  const refLine = budgetRefs.find((b: any) => b.budget_line_id === it.budget_line_id)
                  return (
                    <tr key={it._key} className="border-b border-gray-50 last:border-0">
                      <td className="px-2 py-1.5">
                        <select value={it.item_id} onChange={(e) => {
                          const selected = itemMasterOptions.find((m: any) => m.id === e.target.value)
                          updatePRItem(it._key, {
                            item_id: e.target.value,
                            budget_line_id: e.target.value ? '' : it.budget_line_id,
                            description: e.target.value && !it.description ? (selected?.item_name ?? it.description) : it.description,
                          })
                        }} className="form-select text-xs py-1">
                          <option value="">{t('procurement:pr_selectItem')}</option>
                          {itemMasterOptions.map((m: any) => (
                            <option key={m.id} value={m.id}>{m.sku_code} — {m.item_name}</option>
                          ))}
                        </select>
                      </td>
                      <td className="px-2 py-1.5">
                        <input value={it.description} onChange={(e) => updatePRItem(it._key, { description: e.target.value })}
                          className="form-input text-xs py-1" />
                      </td>
                      <td className="px-2 py-1.5">
                        <select value={it.category} onChange={(e) => updatePRItem(it._key, { category: e.target.value })} className="form-select text-xs py-1">
                          {CATEGORY_OPTS.map((c) => <option key={c} value={c}>{t(`procurement:category_${c}`)}</option>)}
                        </select>
                      </td>
                      <td className="px-2 py-1.5">
                        <input type="number" value={it.qty} onChange={(e) => updatePRItem(it._key, { qty: e.target.value })}
                          className="form-input text-xs py-1 text-right" />
                      </td>
                      <td className="px-2 py-1.5">
                        <input type="number" value={it.unit_price} onChange={(e) => updatePRItem(it._key, { unit_price: e.target.value })}
                          className="form-input text-xs py-1 text-right" />
                      </td>
                      <td className="px-2 py-1.5">
                        {it.item_id ? (
                          <EvaluateLineBadge
                            entityId={entityId}
                            itemId={it.item_id}
                            costCenter={prForm.cost_center}
                            qty={parseFloat(it.qty) || 0}
                            unitPrice={parseFloat(it.unit_price) || 0}
                            onResult={(r) => setEvalResult(it._key, r)}
                          />
                        ) : (
                          <>
                            <select value={it.budget_line_id} onChange={(e) => updatePRItem(it._key, { budget_line_id: e.target.value })}
                              className="form-select text-xs py-1">
                              <option value="">{t('procurement:pr_selectBudgetRef')}</option>
                              {budgetRefs.map((b: any) => (
                                <option key={b.budget_line_id} value={b.budget_line_id}>
                                  {b.budget_no} — {b.activity_description} ({t('procurement:pr_available')}: Rp {formatRupiah(b.available_amount)})
                                </option>
                              ))}
                            </select>
                            {refLine && (
                              <p className="text-[11px] text-gray-400 mt-0.5">{t('common:code')}: {refLine.account_code} · {refLine.cost_center}</p>
                            )}
                          </>
                        )}
                      </td>
                      <td className="px-2 py-1.5 text-center">
                        <button type="button" onClick={() => removePRItem(it._key)} disabled={prItems.length <= 1}
                          className="text-gray-400 hover:text-red-500 disabled:opacity-30">
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          {prItems.some((it) => it.item_id) && !prForm.cost_center && (
            <p className="text-xs text-amber-600 mt-2">{t('procurement:pr_needCostCenterForItem')}</p>
          )}

          <div className="flex justify-end mt-3">
            <div className="w-56 space-y-1 text-sm">
              <div className="flex justify-between text-gray-500">
                <span>{t('procurement:colSubtotal')}</span>
                <span>Rp {formatRupiah(prSubtotal)}</span>
              </div>
              <div className="flex justify-between font-semibold text-gray-900 border-t border-gray-100 pt-1">
                <span>{t('procurement:colTotal')}</span>
                <span>Rp {formatRupiah(prSubtotal)}</span>
              </div>
            </div>
          </div>

          <div className="flex items-center justify-between mt-3">
            <button type="button" onClick={addPRItem} className="inline-flex items-center gap-1 text-xs text-primary-600 hover:text-primary-700 font-medium">
              <Plus className="h-3.5 w-3.5" /> {t('procurement:pr_addItemBtn')}
            </button>
            <div className="flex gap-3">
              <button onClick={() => setShowPRForm(false)} className="btn-secondary">{t('common:cancel')}</button>
              <button onClick={() => createPRMutation.mutate()}
                disabled={createPRMutation.isPending || !prForm.requested_by || !prItemsValid}
                className="btn-primary">
                {createPRMutation.isPending ? t('common:saving') : t('common:save')}
              </button>
            </div>
          </div>
          {!prItemsValid && (
            <p className="text-xs text-amber-600 mt-2">{t('procurement:pr_needBudgetRefNotice')}</p>
          )}
        </Card>
      )}

      <Card noPad>
        <CardHeader title={t('procurement:pr_listTitle')} subtitle={t('procurement:pr_listSubtitle', { count: prs.length })}
          actions={<button onClick={() => refetchPR()} className="btn-secondary"><RefreshCw className="h-4 w-4" /></button>} />
        {prLoading ? (
          <div className="flex justify-center py-16"><Spinner size="lg" /></div>
        ) : prs.length === 0 ? (
          <EmptyState title={t('procurement:pr_emptyTitle')} />
        ) : (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th></th>
                  <th>{t('procurement:pr_colReqNo')}</th>
                  <th>{t('procurement:pr_colDepartment')}</th>
                  <th>{t('procurement:pr_colCostCenter')}</th>
                  <th>{t('common:status')}</th>
                  <th className="right">{t('procurement:pr_colTotalAmount')}</th>
                  <th>{t('procurement:pr_colBudgetCheck')}</th>
                  <th>{t('procurement:pr_colCurrentApprover')}</th>
                  <th>{t('procurement:pr_colRelatedPo')}</th>
                  <th>{t('common:action')}</th>
                </tr>
              </thead>
              <tbody>
                {prs.map((pr: any) => (
                  <Fragment key={pr.id}>
                    <tr>
                      <td>
                        <button onClick={() => setExpandedPrId((cur) => (cur === pr.id ? '' : pr.id))}
                          className="text-gray-400 hover:text-gray-600">
                          {expandedPrId === pr.id ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
                        </button>
                      </td>
                      <td className="text-sm font-medium">{pr.req_no}</td>
                      <td className="text-sm text-gray-500">{pr.department ?? '—'}</td>
                      <td className="text-sm text-gray-500">{pr.cost_center ?? '—'}</td>
                      <td><Badge status={pr.status} /></td>
                      <td className="right">Rp {formatRupiah(pr.total_amount)}</td>
                      <td><Badge status={pr.budget_check_status} /></td>
                      <td className="text-sm text-gray-500">{pr.current_approver ?? '—'}</td>
                      <td>
                        {pr.po_id ? (
                          <Link to="/po" className="inline-flex items-center gap-1.5 text-sm hover:underline">
                            <span className="font-medium text-primary-700">{pr.po_no}</span>
                            <Badge status={pr.po_status} />
                            {pr.po_status === 'open' && (
                              <span className="text-[11px] text-gray-400">
                                {t('procurement:pr_quoteCount', { count: pr.po_quote_count ?? 0 })}
                              </span>
                            )}
                          </Link>
                        ) : (
                          <span className="text-sm text-gray-400">—</span>
                        )}
                      </td>
                      <td>
                        <div className="flex items-center gap-1.5 flex-wrap">
                          {pr.status === 'draft' && (
                            <button onClick={() => prSubmitMutation.mutate(pr.id)} disabled={prSubmitMutation.isPending}
                              className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-md bg-blue-50 text-blue-700 hover:bg-blue-100">
                              <Send className="h-3 w-3" /> {t('common:submit')}
                            </button>
                          )}
                          {pr.status === 'submitted' && (
                            <>
                              <button onClick={() => prApproveMutation.mutate({ id: pr.id, action: 'approved' })} disabled={prApproveMutation.isPending}
                                className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-md bg-green-50 text-green-700 hover:bg-green-100">
                                <CheckCircle className="h-3 w-3" /> {t('common:approve')}
                              </button>
                              <button onClick={() => prApproveMutation.mutate({ id: pr.id, action: 'rejected' })} disabled={prApproveMutation.isPending}
                                className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-md bg-red-50 text-red-700 hover:bg-red-100">
                                <XCircle className="h-3 w-3" /> {t('common:reject')}
                              </button>
                            </>
                          )}
                        </div>
                      </td>
                    </tr>
                    {expandedPrId === pr.id && <PRStepsRow prId={pr.id} colSpan={10} />}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}
