import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Plus, RefreshCw, Send, CheckCircle, XCircle, Rocket, Trash2,
  ListChecks, BarChart3, ArrowLeftRight,
} from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext'
import api from '../../lib/api'
import { Card, CardHeader } from '../../components/ui/Card'
import Spinner from '../../components/ui/Spinner'
import EmptyState from '../../components/ui/EmptyState'
import Badge from '../../components/ui/Badge'
import { formatRupiah, formatPercent, todayISO, currentYear } from '../../lib/utils'
import { showToast } from '../../components/ui/Toast'

const ROLE_LEVEL: Record<string, number> = { viewer: 1, finance: 2, admin: 3, superadmin: 4 }
const MONTH_OPTS = Array.from({ length: 12 }, (_, i) => i + 1)

type Tab = 'periods' | 'lines' | 'reports' | 'transfers'

type NewLine = { _key: number; cost_center: string; account_code: string; month: string; budgeted_amount: string; activity_description: string }
let _k = 0
const newLine = (): NewLine => ({ _key: ++_k, cost_center: '', account_code: '', month: '1', budgeted_amount: '', activity_description: '' })

export default function BudgetPage() {
  const { t } = useTranslation(['budget', 'common'])
  const { entityId, user } = useAuth()
  const qc = useQueryClient()
  const [tab, setTab] = useState<Tab>('periods')

  const myLevel = ROLE_LEVEL[user?.role ?? 'viewer'] ?? 1
  const canFinance = myLevel >= 2
  const canAdmin = myLevel >= 3

  // ── Periods ──────────────────────────────────────────────────────────────────
  const { data: periodsData, isLoading: periodsLoading, refetch: refetchPeriods } = useQuery({
    queryKey: ['budget-periods', entityId],
    queryFn: () => api.get('/budget/period', { params: { entity_id: entityId } }).then((r) => r.data),
    enabled: !!entityId,
  })
  const periods: any[] = Array.isArray(periodsData) ? periodsData : []

  const [activePeriodId, setActivePeriodId] = useState('')

  const [showPeriodForm, setShowPeriodForm] = useState(false)
  const [periodForm, setPeriodForm] = useState({
    fiscal_year: String(currentYear()),
    budget_version: 'ORIGINAL',
    control_mode: 'soft',
    description: '',
  })

  const createPeriodMutation = useMutation({
    mutationFn: () =>
      api.post('/budget/period', {
        entity_id: entityId,
        fiscal_year: parseInt(periodForm.fiscal_year, 10) || currentYear(),
        budget_version: periodForm.budget_version,
        control_mode: periodForm.control_mode,
        description: periodForm.description || undefined,
      }),
    onSuccess: () => {
      showToast(t('budget:periods_createSuccess'))
      setShowPeriodForm(false)
      setPeriodForm({ fiscal_year: String(currentYear()), budget_version: 'ORIGINAL', control_mode: 'soft', description: '' })
      qc.invalidateQueries({ queryKey: ['budget-periods', entityId] })
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('budget:periods_createFailed'), 'error'),
  })

  const periodActionMutation = useMutation({
    mutationFn: ({ id, action }: { id: string; action: 'submit' | 'release' }) =>
      api.post(`/budget/period/${id}/${action}`),
    onSuccess: () => {
      showToast(t('budget:periods_actionSuccess'))
      qc.invalidateQueries({ queryKey: ['budget-periods', entityId] })
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('budget:periods_actionFailed'), 'error'),
  })

  const approveMutation = useMutation({
    mutationFn: ({ id, action }: { id: string; action: 'approved' | 'rejected' }) =>
      api.post(`/budget/period/${id}/approve`, { action }),
    onSuccess: () => {
      showToast(t('budget:periods_actionSuccess'))
      qc.invalidateQueries({ queryKey: ['budget-periods', entityId] })
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('budget:periods_actionFailed'), 'error'),
  })

  const controlModeMutation = useMutation({
    mutationFn: ({ id, mode }: { id: string; mode: string }) =>
      api.post(`/budget/period/${id}/control-mode`, null, { params: { mode } }),
    onSuccess: () => {
      showToast(t('budget:periods_controlModeUpdated'))
      qc.invalidateQueries({ queryKey: ['budget-periods', entityId] })
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('budget:periods_actionFailed'), 'error'),
  })

  // ── COA untuk dropdown akun di tab Lines ─────────────────────────────────────
  const { data: coaData } = useQuery({
    queryKey: ['coa', entityId],
    queryFn: () => api.get('/coa/', { params: { entity_id: entityId, limit: 1000 } }).then((r) => r.data),
    enabled: !!entityId && tab === 'lines',
  })
  const accounts: any[] = Array.isArray(coaData) ? coaData : (coaData?.accounts ?? [])
  const expenseAccounts = accounts.filter((a: any) => !a.is_header && a.account_type === 'expense')

  // ── Lines ────────────────────────────────────────────────────────────────────
  const activePeriod = periods.find((p: any) => p.id === activePeriodId)

  const { data: linesData, isLoading: linesLoading, refetch: refetchLines } = useQuery({
    queryKey: ['budget-lines', activePeriodId],
    queryFn: () => api.get(`/budget/period/${activePeriodId}/lines`).then((r) => r.data),
    enabled: !!activePeriodId,
  })
  const existingLines: any[] = Array.isArray(linesData) ? linesData : []

  const [newLines, setNewLines] = useState<NewLine[]>([newLine()])
  const updateNewLine = (key: number, patch: Partial<NewLine>) =>
    setNewLines((prev) => prev.map((l) => (l._key === key ? { ...l, ...patch } : l)))
  const addNewLine = () => setNewLines((prev) => [...prev, newLine()])
  const removeNewLine = (key: number) =>
    setNewLines((prev) => (prev.length > 1 ? prev.filter((l) => l._key !== key) : prev))

  const linesEditable = activePeriod?.status === 'draft'

  const saveLinesMutation = useMutation({
    mutationFn: () =>
      api.post(`/budget/period/${activePeriodId}/lines`, {
        lines: newLines
          .filter((l) => l.cost_center && l.account_code && parseFloat(l.budgeted_amount) > 0 && l.activity_description.trim())
          .map((l) => ({
            cost_center: l.cost_center,
            account_code: l.account_code,
            month: parseInt(l.month, 10) || 1,
            budgeted_amount: parseFloat(l.budgeted_amount) || 0,
            activity_description: l.activity_description.trim(),
          })),
      }),
    onSuccess: (res) => {
      showToast(t('budget:lines_saveSuccess', { count: res.data.upserted }))
      setNewLines([newLine()])
      refetchLines()
      qc.invalidateQueries({ queryKey: ['budget-periods', entityId] })
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('budget:lines_saveFailed'), 'error'),
  })

  // ── Reports ──────────────────────────────────────────────────────────────────
  const [reportYear, setReportYear] = useState(String(currentYear()))
  const [reportCC, setReportCC] = useState('')

  const { data: ccData } = useQuery({
    queryKey: ['budget-cost-centers', entityId, reportYear],
    queryFn: () => api.get('/budget/cost-centers', { params: { entity_id: entityId, year: reportYear || undefined } }).then((r) => r.data),
    enabled: !!entityId && tab === 'reports',
  })
  const reportCostCenters: any[] = Array.isArray(ccData) ? ccData : []

  const { data: varianceData, isLoading: varianceLoading } = useQuery({
    queryKey: ['budget-variance', entityId, reportYear, reportCC],
    queryFn: () => api.get('/budget/variance', {
      params: { entity_id: entityId, year: reportYear, cost_center: reportCC || undefined },
    }).then((r) => r.data),
    enabled: !!entityId && tab === 'reports' && !!reportYear,
  })
  const varianceLines: any[] = Array.isArray(varianceData?.lines) ? varianceData.lines : []

  const { data: utilData, isLoading: utilLoading } = useQuery({
    queryKey: ['budget-utilization', entityId, reportYear, reportCC],
    queryFn: () => api.get('/budget/utilization', {
      params: { entity_id: entityId, year: reportYear, cost_center: reportCC || undefined },
    }).then((r) => r.data),
    enabled: !!entityId && tab === 'reports' && !!reportYear,
  })
  const utilRows: any[] = Array.isArray(utilData) ? utilData : []

  const { data: commitData, isLoading: commitLoading } = useQuery({
    queryKey: ['budget-commitments', entityId, reportYear, reportCC],
    queryFn: () => api.get('/budget/commitment', {
      params: { entity_id: entityId, year: reportYear, cost_center: reportCC || undefined },
    }).then((r) => r.data),
    enabled: !!entityId && tab === 'reports' && !!reportYear,
  })
  const commitRows: any[] = Array.isArray(commitData) ? commitData : []

  // ── Transfers & Supplements ──────────────────────────────────────────────────
  const [transferForm, setTransferForm] = useState({
    fiscal_year: String(currentYear()), month: '1', transfer_date: todayISO(),
    from_period_id: '', from_cost_center: '', from_account_code: '',
    to_period_id: '', to_cost_center: '', to_account_code: '',
    amount: '', reason: '',
  })
  const transferMutation = useMutation({
    mutationFn: () =>
      api.post('/budget/transfer', {
        entity_id: entityId,
        fiscal_year: parseInt(transferForm.fiscal_year, 10) || currentYear(),
        month: parseInt(transferForm.month, 10) || 1,
        transfer_date: transferForm.transfer_date,
        from_period_id: transferForm.from_period_id,
        from_cost_center: transferForm.from_cost_center,
        from_account_code: transferForm.from_account_code,
        to_period_id: transferForm.to_period_id,
        to_cost_center: transferForm.to_cost_center,
        to_account_code: transferForm.to_account_code,
        amount: parseFloat(transferForm.amount) || 0,
        reason: transferForm.reason || undefined,
      }),
    onSuccess: (res) => {
      showToast(t('budget:transfers_createSuccess', { no: res.data.transfer_no }))
      setTransferForm((f) => ({
        ...f, from_cost_center: '', from_account_code: '', to_cost_center: '', to_account_code: '', amount: '', reason: '',
      }))
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('budget:transfers_createFailed'), 'error'),
  })

  const [supplementForm, setSupplementForm] = useState({
    period_id: '', cost_center: '', account_code: '', month: '1', amount: '', reason: '',
  })
  const supplementMutation = useMutation({
    mutationFn: () =>
      api.post('/budget/supplement', {
        entity_id: entityId,
        period_id: supplementForm.period_id,
        cost_center: supplementForm.cost_center,
        account_code: supplementForm.account_code,
        month: parseInt(supplementForm.month, 10) || 1,
        amount: parseFloat(supplementForm.amount) || 0,
        reason: supplementForm.reason || undefined,
      }),
    onSuccess: (res) => {
      showToast(t('budget:supplements_createSuccess', { no: res.data.supplement_no }))
      setSupplementForm((f) => ({ ...f, cost_center: '', account_code: '', amount: '', reason: '' }))
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('budget:supplements_createFailed'), 'error'),
  })

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">{t('budget:pageTitle')}</h1>
          <p className="text-sm text-gray-500 mt-0.5">{t('budget:pageSubtitle')}</p>
        </div>
      </div>

      {/* Tabs */}
      <div className="border-b border-gray-200">
        <nav className="flex gap-1">
          {[
            { key: 'periods', label: t('budget:tab_periods'), icon: <ListChecks className="h-4 w-4" /> },
            { key: 'lines', label: t('budget:tab_lines'), icon: <Plus className="h-4 w-4" /> },
            { key: 'reports', label: t('budget:tab_reports'), icon: <BarChart3 className="h-4 w-4" /> },
            { key: 'transfers', label: t('budget:tab_transfers'), icon: <ArrowLeftRight className="h-4 w-4" /> },
          ].map((tb) => (
            <button key={tb.key}
              onClick={() => setTab(tb.key as Tab)}
              className={`flex items-center gap-1.5 px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                tab === tb.key ? 'border-primary-600 text-primary-700' : 'border-transparent text-gray-500 hover:text-gray-700'
              }`}>
              {tb.icon} {tb.label}
            </button>
          ))}
        </nav>
      </div>

      {/* Tab: Periods */}
      {tab === 'periods' && (
        <div className="space-y-4">
          {canFinance && (
            <div className="flex justify-end">
              <button onClick={() => setShowPeriodForm((s) => !s)} className="btn-primary">
                <Plus className="h-4 w-4" /> {t('budget:periods_newBtn')}
              </button>
            </div>
          )}

          {showPeriodForm && (
            <Card>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <div>
                  <label className="form-label">{t('budget:periods_fiscalYearLabel')}</label>
                  <input type="number" value={periodForm.fiscal_year}
                    onChange={(e) => setPeriodForm({ ...periodForm, fiscal_year: e.target.value })}
                    className="form-input" />
                </div>
                <div>
                  <label className="form-label">{t('budget:periods_versionLabel')}</label>
                  <input value={periodForm.budget_version}
                    onChange={(e) => setPeriodForm({ ...periodForm, budget_version: e.target.value })}
                    className="form-input" placeholder="ORIGINAL" />
                </div>
                <div>
                  <label className="form-label">{t('budget:periods_controlModeLabel')}</label>
                  <select value={periodForm.control_mode}
                    onChange={(e) => setPeriodForm({ ...periodForm, control_mode: e.target.value })}
                    className="form-select">
                    <option value="soft">{t('budget:controlMode_soft')}</option>
                    <option value="hard">{t('budget:controlMode_hard')}</option>
                    <option value="off">{t('budget:controlMode_off')}</option>
                  </select>
                </div>
                <div>
                  <label className="form-label">{t('common:description')}</label>
                  <input value={periodForm.description}
                    onChange={(e) => setPeriodForm({ ...periodForm, description: e.target.value })}
                    className="form-input" placeholder={t('common:optional')} />
                </div>
              </div>
              <div className="flex gap-3 mt-4">
                <button onClick={() => createPeriodMutation.mutate()}
                  disabled={createPeriodMutation.isPending || !periodForm.fiscal_year}
                  className="btn-primary">
                  {createPeriodMutation.isPending ? t('common:saving') : t('common:save')}
                </button>
                <button onClick={() => setShowPeriodForm(false)} className="btn-secondary">{t('common:cancel')}</button>
              </div>
            </Card>
          )}

          <Card noPad>
            <CardHeader
              title={t('budget:periods_listTitle')}
              subtitle={t('budget:periods_listSubtitle', { count: periods.length })}
              actions={<button onClick={() => refetchPeriods()} className="btn-secondary"><RefreshCw className="h-4 w-4" /></button>}
            />
            {periodsLoading ? (
              <div className="flex justify-center py-16"><Spinner size="lg" /></div>
            ) : periods.length === 0 ? (
              <EmptyState title={t('budget:periods_emptyTitle')} />
            ) : (
              <div className="overflow-x-auto">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>{t('budget:periods_colYear')}</th>
                      <th>{t('budget:periods_colVersion')}</th>
                      <th>{t('budget:periods_colStatus')}</th>
                      <th>{t('budget:periods_colControlMode')}</th>
                      <th className="right">{t('budget:periods_colTotalBudget')}</th>
                      <th className="right">{t('budget:periods_colLineCount')}</th>
                      <th>{t('budget:periods_colAction')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {periods.map((p: any) => (
                      <tr key={p.id} className={p.id === activePeriodId ? 'bg-primary-50/40' : ''}>
                        <td className="text-sm font-medium">{p.fiscal_year}</td>
                        <td className="font-mono text-xs">{p.budget_version}</td>
                        <td><Badge status={p.status} /></td>
                        <td className="text-xs text-gray-500">{t(`budget:controlMode_${p.control_mode}`)}</td>
                        <td className="right">Rp {formatRupiah(p.total_budget)}</td>
                        <td className="right text-gray-400">{p.line_count}</td>
                        <td>
                          <div className="flex items-center gap-1.5 flex-wrap">
                            <button
                              onClick={() => { setActivePeriodId(p.id); setTab('lines') }}
                              className="text-xs px-2 py-1 rounded-md bg-gray-100 text-gray-600 hover:bg-gray-200">
                              {t('budget:periods_useBtn')}
                            </button>
                            {p.status === 'draft' && canFinance && (
                              <button
                                onClick={() => periodActionMutation.mutate({ id: p.id, action: 'submit' })}
                                disabled={periodActionMutation.isPending}
                                className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-md bg-blue-50 text-blue-700 hover:bg-blue-100">
                                <Send className="h-3 w-3" /> {t('budget:periods_submitBtn')}
                              </button>
                            )}
                            {p.status === 'submitted' && canAdmin && (
                              <>
                                <button
                                  onClick={() => approveMutation.mutate({ id: p.id, action: 'approved' })}
                                  disabled={approveMutation.isPending}
                                  className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-md bg-green-50 text-green-700 hover:bg-green-100">
                                  <CheckCircle className="h-3 w-3" /> {t('budget:periods_approveBtn')}
                                </button>
                                <button
                                  onClick={() => approveMutation.mutate({ id: p.id, action: 'rejected' })}
                                  disabled={approveMutation.isPending}
                                  className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-md bg-red-50 text-red-700 hover:bg-red-100">
                                  <XCircle className="h-3 w-3" /> {t('budget:periods_rejectBtn')}
                                </button>
                              </>
                            )}
                            {p.status === 'approved' && canAdmin && (
                              <button
                                onClick={() => periodActionMutation.mutate({ id: p.id, action: 'release' })}
                                disabled={periodActionMutation.isPending}
                                className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-md bg-primary-50 text-primary-700 hover:bg-primary-100">
                                <Rocket className="h-3 w-3" /> {t('budget:periods_releaseBtn')}
                              </button>
                            )}
                            {(p.status === 'released' || p.status === 'closed') && canAdmin && (
                              <select
                                value={p.control_mode}
                                onChange={(e) => controlModeMutation.mutate({ id: p.id, mode: e.target.value })}
                                className="form-select text-xs py-1">
                                <option value="soft">{t('budget:controlMode_soft')}</option>
                                <option value="hard">{t('budget:controlMode_hard')}</option>
                                <option value="off">{t('budget:controlMode_off')}</option>
                              </select>
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
      )}

      {/* Tab: Lines */}
      {tab === 'lines' && (
        <div className="space-y-4">
          <Card>
            <label className="form-label">{t('budget:lines_selectPeriodLabel')}</label>
            <select value={activePeriodId} onChange={(e) => setActivePeriodId(e.target.value)} className="form-select max-w-md">
              <option value="">—</option>
              {periods.map((p: any) => (
                <option key={p.id} value={p.id}>{p.fiscal_year} / {p.budget_version} ({t(p.status)})</option>
              ))}
            </select>
          </Card>

          {!activePeriodId ? (
            <EmptyState title={t('budget:lines_noPeriodTitle')} />
          ) : (
            <>
              {!linesEditable && (
                <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 text-sm text-amber-700">
                  {t('budget:lines_notDraftNotice', { status: t(activePeriod?.status ?? '') })}
                </div>
              )}

              {linesEditable && (
                <Card noPad>
                  <CardHeader title={t('budget:lines_inputTitle')} subtitle={t('budget:lines_inputSubtitle')} />
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="text-xs text-gray-500 border-b border-gray-100">
                          <th className="text-left font-medium px-3 py-2 min-w-32">{t('budget:lines_colCostCenter')}</th>
                          <th className="text-left font-medium px-3 py-2 min-w-40">{t('budget:lines_colAccount')}</th>
                          <th className="text-left font-medium px-3 py-2 min-w-24">{t('budget:lines_colMonth')}</th>
                          <th className="text-right font-medium px-3 py-2 min-w-32">{t('budget:lines_colAmount')}</th>
                          <th className="text-left font-medium px-3 py-2 min-w-48">{t('budget:lines_colActivity')}</th>
                          <th className="px-2 py-2 w-8"></th>
                        </tr>
                      </thead>
                      <tbody>
                        {newLines.map((line) => (
                          <tr key={line._key} className="border-b border-gray-50 last:border-0">
                            <td className="px-3 py-1.5">
                              <input value={line.cost_center}
                                onChange={(e) => updateNewLine(line._key, { cost_center: e.target.value })}
                                className="form-input text-xs py-1" placeholder="GA" />
                            </td>
                            <td className="px-3 py-1.5">
                              <input list={`budget-coa-${line._key}`} value={line.account_code}
                                onChange={(e) => updateNewLine(line._key, { account_code: e.target.value })}
                                className="form-input font-mono text-xs py-1" placeholder="6-1-001" />
                              <datalist id={`budget-coa-${line._key}`}>
                                {expenseAccounts.map((a: any) => (
                                  <option key={a.account_code} value={a.account_code}>{a.account_name}</option>
                                ))}
                              </datalist>
                            </td>
                            <td className="px-3 py-1.5">
                              <select value={line.month} onChange={(e) => updateNewLine(line._key, { month: e.target.value })}
                                className="form-select text-xs py-1">
                                {MONTH_OPTS.map((m) => <option key={m} value={m}>{m}</option>)}
                              </select>
                            </td>
                            <td className="px-3 py-1.5">
                              <input type="number" value={line.budgeted_amount}
                                onChange={(e) => updateNewLine(line._key, { budgeted_amount: e.target.value })}
                                className="form-input text-xs py-1 text-right" placeholder="0" />
                            </td>
                            <td className="px-3 py-1.5">
                              <input value={line.activity_description}
                                onChange={(e) => updateNewLine(line._key, { activity_description: e.target.value })}
                                className="form-input text-xs py-1" placeholder={t('budget:lines_activityPlaceholder')} />
                            </td>
                            <td className="px-2 py-1.5 text-center">
                              <button type="button" onClick={() => removeNewLine(line._key)}
                                disabled={newLines.length <= 1}
                                className="text-gray-400 hover:text-red-500 disabled:opacity-30">
                                <Trash2 className="h-3.5 w-3.5" />
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <div className="px-3 py-2 flex items-center justify-between border-t border-gray-100">
                    <button type="button" onClick={addNewLine}
                      className="inline-flex items-center gap-1 text-xs text-primary-600 hover:text-primary-700 font-medium">
                      <Plus className="h-3.5 w-3.5" /> {t('budget:lines_addLineBtn')}
                    </button>
                    <button onClick={() => saveLinesMutation.mutate()} disabled={saveLinesMutation.isPending} className="btn-primary">
                      {saveLinesMutation.isPending ? t('common:saving') : t('budget:lines_saveBtn')}
                    </button>
                  </div>
                </Card>
              )}

              <Card noPad>
                <CardHeader title={t('budget:lines_existingTitle')} subtitle={t('budget:lines_existingSubtitle', { count: existingLines.length })} />
                {linesLoading ? (
                  <div className="flex justify-center py-16"><Spinner size="lg" /></div>
                ) : existingLines.length === 0 ? (
                  <EmptyState title={t('budget:lines_emptyTitle')} />
                ) : (
                  <div className="overflow-x-auto">
                    <table className="data-table">
                      <thead>
                        <tr>
                          <th>{t('budget:lines_colBudgetNo')}</th>
                          <th>{t('budget:lines_colCostCenter')}</th>
                          <th>{t('budget:lines_colAccount')}</th>
                          <th>{t('budget:lines_colMonth')}</th>
                          <th className="right">{t('budget:lines_colAmount')}</th>
                          <th>{t('budget:lines_colActivity')}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {existingLines.map((l: any) => (
                          <tr key={l.id}>
                            <td className="font-mono text-xs font-medium text-primary-700">{l.budget_no}</td>
                            <td className="text-sm">{l.cost_center}</td>
                            <td className="font-mono text-xs">{l.account_code}</td>
                            <td className="text-sm text-gray-500">{l.month}</td>
                            <td className="right">Rp {formatRupiah(l.budgeted_amount)}</td>
                            <td className="text-sm text-gray-600">{l.activity_description}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Card>
            </>
          )}
        </div>
      )}

      {/* Tab: Reports */}
      {tab === 'reports' && (
        <div className="space-y-4">
          <Card>
            <div className="flex items-end gap-3 flex-wrap">
              <div>
                <label className="form-label">{t('budget:reports_yearLabel')}</label>
                <input type="number" value={reportYear} onChange={(e) => setReportYear(e.target.value)} className="form-input w-28" />
              </div>
              <div>
                <label className="form-label">{t('budget:reports_costCenterLabel')}</label>
                <select value={reportCC} onChange={(e) => setReportCC(e.target.value)} className="form-select w-48">
                  <option value="">{t('budget:reports_allCostCenters')}</option>
                  {reportCostCenters.map((cc: any) => (
                    <option key={cc.cost_center} value={cc.cost_center}>{cc.cost_center}</option>
                  ))}
                </select>
              </div>
            </div>
          </Card>

          {varianceData?.summary && (
            <div className="grid grid-cols-4 gap-4">
              <div className="card p-4">
                <p className="text-xs font-medium text-gray-500 uppercase">{t('budget:reports_totalBudget')}</p>
                <p className="text-lg font-bold text-gray-900 mt-1">Rp {formatRupiah(varianceData.summary.total_budget)}</p>
              </div>
              <div className="card p-4">
                <p className="text-xs font-medium text-gray-500 uppercase">{t('budget:reports_totalActual')}</p>
                <p className="text-lg font-bold text-gray-900 mt-1">Rp {formatRupiah(varianceData.summary.total_actual)}</p>
              </div>
              <div className="card p-4">
                <p className="text-xs font-medium text-gray-500 uppercase">{t('budget:reports_totalVariance')}</p>
                <p className={`text-lg font-bold mt-1 ${varianceData.summary.total_variance < 0 ? 'text-red-600' : 'text-green-700'}`}>
                  Rp {formatRupiah(varianceData.summary.total_variance)}
                </p>
              </div>
              <div className="card p-4">
                <p className="text-xs font-medium text-gray-500 uppercase">{t('budget:reports_utilization')}</p>
                <p className="text-lg font-bold text-gray-900 mt-1">{formatPercent(varianceData.summary.utilization_pct)}</p>
              </div>
            </div>
          )}

          <Card noPad>
            <CardHeader title={t('budget:reports_varianceTitle')} subtitle={t('budget:reports_varianceSubtitle')} />
            {varianceLoading ? (
              <div className="flex justify-center py-16"><Spinner size="lg" /></div>
            ) : varianceLines.length === 0 ? (
              <EmptyState title={t('budget:reports_emptyVariance')} />
            ) : (
              <div className="overflow-x-auto">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>{t('budget:lines_colCostCenter')}</th>
                      <th>{t('budget:lines_colAccount')}</th>
                      <th className="right">{t('budget:reports_colBudgetAnnual')}</th>
                      <th className="right">{t('budget:reports_colActualAnnual')}</th>
                      <th className="right">{t('budget:reports_colVariance')}</th>
                      <th className="right">{t('budget:reports_colUtilPct')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {varianceLines.map((r: any, i: number) => (
                      <tr key={i}>
                        <td className="text-sm">{r.cost_center}</td>
                        <td className="font-mono text-xs">{r.account_code}</td>
                        <td className="right">Rp {formatRupiah(r.budget_annual)}</td>
                        <td className="right">Rp {formatRupiah(r.actual_annual)}</td>
                        <td className={`right font-medium ${r.variance < 0 ? 'text-red-600' : 'text-green-700'}`}>
                          Rp {formatRupiah(r.variance)}
                        </td>
                        <td className="right text-gray-500">{r.utilization_pct != null ? formatPercent(r.utilization_pct) : '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          <Card noPad>
            <CardHeader title={t('budget:reports_utilizationTitle')} subtitle={t('budget:reports_utilizationSubtitle')} />
            {utilLoading ? (
              <div className="flex justify-center py-16"><Spinner size="lg" /></div>
            ) : utilRows.length === 0 ? (
              <EmptyState title={t('budget:reports_emptyUtilization')} />
            ) : (
              <div className="overflow-x-auto">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>{t('budget:lines_colCostCenter')}</th>
                      <th>{t('budget:lines_colAccount')}</th>
                      <th>{t('budget:lines_colMonth')}</th>
                      <th className="right">{t('budget:reports_colBudget')}</th>
                      <th className="right">{t('budget:reports_colActual')}</th>
                      <th className="right">{t('budget:reports_colCommitted')}</th>
                      <th className="right">{t('budget:reports_colAvailable')}</th>
                      <th className="right">{t('budget:reports_colUtilPct')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {utilRows.map((r: any, i: number) => (
                      <tr key={i}>
                        <td className="text-sm">{r.cost_center}</td>
                        <td className="font-mono text-xs">{r.account_code}</td>
                        <td className="text-sm text-gray-500">{r.month}</td>
                        <td className="right">Rp {formatRupiah(r.budgeted_amount)}</td>
                        <td className="right">Rp {formatRupiah(r.actual_amount)}</td>
                        <td className="right text-gray-500">Rp {formatRupiah(r.commitment_amount)}</td>
                        <td className={`right font-medium ${r.available_amount < 0 ? 'text-red-600' : 'text-gray-800'}`}>
                          Rp {formatRupiah(r.available_amount)}
                        </td>
                        <td className="right text-gray-500">{r.utilization_pct != null ? formatPercent(r.utilization_pct) : '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          <Card noPad>
            <CardHeader title={t('budget:reports_commitmentTitle')} subtitle={t('budget:reports_commitmentSubtitle')} />
            {commitLoading ? (
              <div className="flex justify-center py-16"><Spinner size="lg" /></div>
            ) : commitRows.length === 0 ? (
              <EmptyState title={t('budget:reports_emptyCommitment')} />
            ) : (
              <div className="overflow-x-auto">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>{t('budget:reports_colSourceRef')}</th>
                      <th>{t('budget:lines_colCostCenter')}</th>
                      <th>{t('budget:lines_colAccount')}</th>
                      <th className="right">{t('budget:reports_colCommitted')}</th>
                      <th className="right">{t('budget:reports_colReleased')}</th>
                      <th className="right">{t('budget:reports_colNetCommitted')}</th>
                      <th>{t('budget:periods_colStatus')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {commitRows.map((r: any) => (
                      <tr key={r.id}>
                        <td className="font-mono text-xs">{r.source_ref ?? '—'}</td>
                        <td className="text-sm">{r.cost_center}</td>
                        <td className="font-mono text-xs">{r.account_code}</td>
                        <td className="right">Rp {formatRupiah(r.committed_amount)}</td>
                        <td className="right text-gray-500">Rp {formatRupiah(r.released_amount)}</td>
                        <td className="right font-medium">Rp {formatRupiah(r.net_committed)}</td>
                        <td><Badge status={r.status} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </div>
      )}

      {/* Tab: Transfers & Supplements */}
      {tab === 'transfers' && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <Card>
            <p className="text-sm font-semibold text-gray-800 mb-4">{t('budget:transfers_formTitle')}</p>
            <div className="space-y-3">
              <div>
                <label className="form-label">{t('budget:transfers_periodLabel')}</label>
                <select value={transferForm.from_period_id}
                  onChange={(e) => setTransferForm({ ...transferForm, from_period_id: e.target.value, to_period_id: e.target.value })}
                  className="form-select">
                  <option value="">—</option>
                  {periods.map((p: any) => <option key={p.id} value={p.id}>{p.fiscal_year} / {p.budget_version}</option>)}
                </select>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="form-label">{t('budget:transfers_monthLabel')}</label>
                  <select value={transferForm.month} onChange={(e) => setTransferForm({ ...transferForm, month: e.target.value })} className="form-select">
                    {MONTH_OPTS.map((m) => <option key={m} value={m}>{m}</option>)}
                  </select>
                </div>
                <div>
                  <label className="form-label">{t('budget:transfers_dateLabel')}</label>
                  <input type="date" value={transferForm.transfer_date}
                    onChange={(e) => setTransferForm({ ...transferForm, transfer_date: e.target.value })} className="form-input" />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3 p-3 bg-gray-50 rounded-lg">
                <div>
                  <label className="form-label">{t('budget:transfers_fromCcLabel')}</label>
                  <input value={transferForm.from_cost_center}
                    onChange={(e) => setTransferForm({ ...transferForm, from_cost_center: e.target.value })} className="form-input" placeholder="GA" />
                </div>
                <div>
                  <label className="form-label">{t('budget:transfers_fromAccLabel')}</label>
                  <input value={transferForm.from_account_code}
                    onChange={(e) => setTransferForm({ ...transferForm, from_account_code: e.target.value })} className="form-input font-mono" placeholder="6-1-001" />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3 p-3 bg-gray-50 rounded-lg">
                <div>
                  <label className="form-label">{t('budget:transfers_toCcLabel')}</label>
                  <input value={transferForm.to_cost_center}
                    onChange={(e) => setTransferForm({ ...transferForm, to_cost_center: e.target.value })} className="form-input" placeholder="IT" />
                </div>
                <div>
                  <label className="form-label">{t('budget:transfers_toAccLabel')}</label>
                  <input value={transferForm.to_account_code}
                    onChange={(e) => setTransferForm({ ...transferForm, to_account_code: e.target.value })} className="form-input font-mono" placeholder="6-1-002" />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="form-label">{t('budget:transfers_amountLabel')}</label>
                  <input type="number" value={transferForm.amount}
                    onChange={(e) => setTransferForm({ ...transferForm, amount: e.target.value })} className="form-input" placeholder="0" />
                </div>
                <div>
                  <label className="form-label">{t('budget:transfers_reasonLabel')}</label>
                  <input value={transferForm.reason}
                    onChange={(e) => setTransferForm({ ...transferForm, reason: e.target.value })} className="form-input" placeholder={t('common:optional')} />
                </div>
              </div>
              <button onClick={() => transferMutation.mutate()}
                disabled={transferMutation.isPending || !canFinance || !transferForm.from_period_id || !transferForm.amount}
                className="btn-primary w-full">
                <ArrowLeftRight className="h-4 w-4" />
                {transferMutation.isPending ? t('common:saving') : t('budget:transfers_submitBtn')}
              </button>
              {!canFinance && <p className="text-xs text-gray-400">{t('budget:needFinanceRoleNotice')}</p>}
            </div>
          </Card>

          <Card>
            <p className="text-sm font-semibold text-gray-800 mb-4">{t('budget:supplements_formTitle')}</p>
            <div className="space-y-3">
              <div>
                <label className="form-label">{t('budget:transfers_periodLabel')}</label>
                <select value={supplementForm.period_id}
                  onChange={(e) => setSupplementForm({ ...supplementForm, period_id: e.target.value })} className="form-select">
                  <option value="">—</option>
                  {periods.map((p: any) => <option key={p.id} value={p.id}>{p.fiscal_year} / {p.budget_version}</option>)}
                </select>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="form-label">{t('budget:transfers_fromCcLabel')}</label>
                  <input value={supplementForm.cost_center}
                    onChange={(e) => setSupplementForm({ ...supplementForm, cost_center: e.target.value })} className="form-input" placeholder="GA" />
                </div>
                <div>
                  <label className="form-label">{t('budget:transfers_fromAccLabel')}</label>
                  <input value={supplementForm.account_code}
                    onChange={(e) => setSupplementForm({ ...supplementForm, account_code: e.target.value })} className="form-input font-mono" placeholder="6-1-001" />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="form-label">{t('budget:transfers_monthLabel')}</label>
                  <select value={supplementForm.month}
                    onChange={(e) => setSupplementForm({ ...supplementForm, month: e.target.value })} className="form-select">
                    {MONTH_OPTS.map((m) => <option key={m} value={m}>{m}</option>)}
                  </select>
                </div>
                <div>
                  <label className="form-label">{t('budget:transfers_amountLabel')}</label>
                  <input type="number" value={supplementForm.amount}
                    onChange={(e) => setSupplementForm({ ...supplementForm, amount: e.target.value })} className="form-input" placeholder="0" />
                </div>
              </div>
              <div>
                <label className="form-label">{t('budget:transfers_reasonLabel')}</label>
                <input value={supplementForm.reason}
                  onChange={(e) => setSupplementForm({ ...supplementForm, reason: e.target.value })} className="form-input" placeholder={t('common:optional')} />
              </div>
              <button onClick={() => supplementMutation.mutate()}
                disabled={supplementMutation.isPending || !canFinance || !supplementForm.period_id || !supplementForm.amount}
                className="btn-primary w-full">
                <Plus className="h-4 w-4" />
                {supplementMutation.isPending ? t('common:saving') : t('budget:supplements_submitBtn')}
              </button>
              {!canFinance && <p className="text-xs text-gray-400">{t('budget:needFinanceRoleNotice')}</p>}
              <p className="text-xs text-gray-400">{t('budget:supplements_cfoNotice')}</p>
            </div>
          </Card>
        </div>
      )}
    </div>
  )
}
