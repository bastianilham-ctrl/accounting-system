import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Plus, RefreshCw, ChevronRight, CheckCircle, Clock, AlertCircle } from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext'
import api from '../../lib/api'
import { Card, CardHeader } from '../../components/ui/Card'
import Spinner from '../../components/ui/Spinner'
import EmptyState from '../../components/ui/EmptyState'
import { formatRupiah, formatDate, MONTHS, currentYear, currentMonth, todayISO, lastDayOfMonth } from '../../lib/utils'
import { showToast } from '../../components/ui/Toast'

const STATUS_ICON: Record<string, React.ReactNode> = {
  draft:     <Clock className="h-4 w-4 text-yellow-500" />,
  in_progress: <AlertCircle className="h-4 w-4 text-blue-500" />,
  finalized: <CheckCircle className="h-4 w-4 text-green-500" />,
}

export default function BankReconPage() {
  const { entityId } = useAuth()
  const { t } = useTranslation(['bank', 'common'])
  const qc = useQueryClient()
  const navigate = useNavigate()

  const STATUS_LABEL: Record<string, string> = {
    draft: t('bank:reconPage_status_draft'),
    in_progress: t('bank:reconPage_status_in_progress'),
    finalized: t('bank:reconPage_status_finalized'),
  }
  const [yearFilter, setYearFilter] = useState(currentYear())
  const [showForm, setShowForm] = useState(false)

  // Fetch bank accounts for selector
  const { data: bankData } = useQuery({
    queryKey: ['bank-accounts', entityId],
    queryFn: () => api.get('/bank/accounts', { params: { entity_id: entityId } }).then(r => r.data),
    enabled: !!entityId,
  })
  const bankAccounts: any[] = Array.isArray(bankData) ? bankData : (bankData?.accounts ?? bankData?.items ?? [])

  // Fetch statements
  const { data, isLoading, refetch } = useQuery({
    queryKey: ['bank-recon-statements', entityId, yearFilter],
    queryFn: () =>
      api.get('/bank-recon/statements', {
        params: { entity_id: entityId, year: yearFilter },
      }).then(r => r.data),
    enabled: !!entityId,
  })
  const statements: any[] = Array.isArray(data) ? data : []

  // Create form state
  const [form, setForm] = useState({
    bank_account_id: '',
    statement_period_year: currentYear(),
    statement_period_month: currentMonth(),
    statement_date: lastDayOfMonth(),
    opening_balance: '',
    closing_balance: '',
  })

  const createMutation = useMutation({
    mutationFn: () => api.post('/bank-recon/statements', {
      entity_id: entityId,
      ...form,
      opening_balance: parseFloat(form.opening_balance) || 0,
      closing_balance: parseFloat(form.closing_balance) || 0,
      source: 'manual',
    }),
    onSuccess: (res) => {
      showToast(t('bank:reconPage_createSuccess'))
      setShowForm(false)
      qc.invalidateQueries({ queryKey: ['bank-recon-statements'] })
      navigate(`/bank-recon/${res.data.id ?? res.data.statement_id}`)
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('bank:reconPage_createFailed'), 'error'),
  })

  const years = [currentYear(), currentYear() - 1, currentYear() - 2]

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">{t('bank:reconPage_title')}</h1>
          <p className="text-sm text-gray-500 mt-0.5">{t('bank:reconPage_subtitle')}</p>
        </div>
        <button onClick={() => setShowForm(true)} className="btn-primary">
          <Plus className="h-4 w-4" /> {t('bank:reconPage_newRecon')}
        </button>
      </div>

      {/* Create form */}
      {showForm && (
        <Card>
          <p className="text-sm font-semibold text-gray-700 mb-4">{t('bank:reconPage_newReconTitle')}</p>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
            <div className="col-span-2 md:col-span-1">
              <label className="form-label">{t('bank:reconPage_bankAccount')}</label>
              <select value={form.bank_account_id}
                onChange={e => setForm({ ...form, bank_account_id: e.target.value })}
                className="form-select">
                <option value="">{t('bank:reconPage_selectAccount')}</option>
                {bankAccounts.map((b: any) => (
                  <option key={b.id} value={b.id}>
                    {b.bank_name} — {b.account_no} ({b.account_name})
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="form-label">{t('bank:reconPage_month')}</label>
              <select value={form.statement_period_month}
                onChange={e => setForm({ ...form, statement_period_month: +e.target.value })}
                className="form-select">
                {MONTHS.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
              </select>
            </div>
            <div>
              <label className="form-label">{t('bank:reconPage_year')}</label>
              <input type="number" value={form.statement_period_year}
                onChange={e => setForm({ ...form, statement_period_year: +e.target.value })}
                className="form-input w-24" min={2020} />
            </div>
            <div>
              <label className="form-label">{t('bank:reconPage_statementDate')}</label>
              <input type="date" value={form.statement_date}
                onChange={e => setForm({ ...form, statement_date: e.target.value })}
                className="form-input" />
            </div>
            <div>
              <label className="form-label">{t('bank:reconPage_openingBalance')}</label>
              <input type="number" value={form.opening_balance}
                onChange={e => setForm({ ...form, opening_balance: e.target.value })}
                className="form-input" placeholder="0" />
            </div>
            <div>
              <label className="form-label">{t('bank:reconPage_closingBalance')}</label>
              <input type="number" value={form.closing_balance}
                onChange={e => setForm({ ...form, closing_balance: e.target.value })}
                className="form-input" placeholder="0" />
            </div>
          </div>
          <div className="flex gap-3 mt-4">
            <button onClick={() => createMutation.mutate()}
              disabled={createMutation.isPending || !form.bank_account_id}
              className="btn-primary">
              {createMutation.isPending ? t('bank:reconPage_saving') : t('bank:reconPage_createAndContinue')}
            </button>
            <button onClick={() => setShowForm(false)} className="btn-secondary">{t('bank:reconPage_cancel')}</button>
          </div>
        </Card>
      )}

      {/* List */}
      <Card noPad>
        <CardHeader
          title={t('bank:reconPage_historyTitle')}
          subtitle={t('bank:reconPage_historySubtitle', { count: statements.length })}
          actions={
            <div className="flex items-center gap-2">
              <select value={yearFilter} onChange={e => setYearFilter(+e.target.value)} className="form-select w-24">
                {years.map(y => <option key={y} value={y}>{y}</option>)}
              </select>
              <button onClick={() => refetch()} className="btn-secondary"><RefreshCw className="h-4 w-4" /></button>
            </div>
          }
        />

        {isLoading ? (
          <div className="flex justify-center py-16"><Spinner size="lg" /></div>
        ) : statements.length === 0 ? (
          <EmptyState title={t('bank:reconPage_emptyTitle')} description={t('bank:reconPage_emptyDescription')} />
        ) : (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>{t('bank:reconPage_colAccount')}</th>
                  <th>{t('bank:reconPage_colPeriod')}</th>
                  <th>{t('bank:reconPage_colStatementDate')}</th>
                  <th className="right">{t('bank:reconPage_colOpeningBalance')}</th>
                  <th className="right">{t('bank:reconPage_colClosingBalance')}</th>
                  <th>{t('bank:reconPage_colStatus')}</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {statements.map((s: any) => (
                  <tr key={s.id} className="cursor-pointer"
                    onClick={() => navigate(`/bank-recon/${s.id}`)}>
                    <td>
                      <p className="text-sm font-medium">{s.account_name}</p>
                      <p className="text-xs text-gray-400">{s.account_number}</p>
                    </td>
                    <td className="text-sm">
                      {MONTHS.find(m => m.value === s.statement_period_month)?.label} {s.statement_period_year}
                    </td>
                    <td className="text-sm text-gray-500">{formatDate(s.statement_date)}</td>
                    <td className="right">{formatRupiah(s.opening_balance)}</td>
                    <td className="right font-medium">{formatRupiah(s.closing_balance)}</td>
                    <td>
                      <div className="flex items-center gap-1.5">
                        {STATUS_ICON[s.status ?? 'draft']}
                        <span className="text-sm">{STATUS_LABEL[s.status ?? 'draft'] ?? s.status}</span>
                      </div>
                    </td>
                    <td>
                      <ChevronRight className="h-4 w-4 text-gray-400" />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}
