import { useState } from 'react'
import { useQuery, useQueries, useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import {
  Plus, RefreshCw, Trash2, X, Wallet, Banknote, ArrowLeftRight, Receipt,
} from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext'
import api from '../../lib/api'
import { Card, CardHeader } from '../../components/ui/Card'
import Spinner from '../../components/ui/Spinner'
import EmptyState from '../../components/ui/EmptyState'
import Badge from '../../components/ui/Badge'
import { formatRupiah, formatDate, todayISO } from '../../lib/utils'
import { showToast } from '../../components/ui/Toast'

type Tab = 'accounts' | 'transactions' | 'petty-cash' | 'transfers'

let _k = 0
const newCtxLine = () => ({ _key: ++_k, account_code: '', description: '', amount: '' })

export default function CashBankPage() {
  const { entityId } = useAuth()
  const { t } = useTranslation(['cashbank', 'common'])
  const qc = useQueryClient()
  const [tab, setTab] = useState<Tab>('accounts')

  // ── Master data shared across tabs ──────────────────────────────────────────
  const { data: cashAccData, refetch: refetchCashAcc } = useQuery({
    queryKey: ['cash-accounts', entityId],
    queryFn: () => api.get('/cash-bank/cash-accounts', { params: { entity_id: entityId } }).then((r) => r.data),
    enabled: !!entityId,
  })
  const cashAccounts: any[] = Array.isArray(cashAccData) ? cashAccData : []

  const { data: bankAccData } = useQuery({
    queryKey: ['bank-accounts', entityId],
    queryFn: () => api.get(`/bank/accounts/${entityId}`).then((r) => r.data),
    enabled: !!entityId,
  })
  const bankAccounts: any[] = Array.isArray(bankAccData) ? bankAccData : []

  const accountOptions = [
    ...bankAccounts.map((b) => ({ type: 'bank', id: b.id, label: `${b.bank_name} — ${b.account_no}` })),
    ...cashAccounts.map((c) => ({
      type: 'cash', id: c.id,
      label: `${c.account_name} (${c.account_type === 'petty_cash' ? t('cashbank:accounts_badgePettyCash') : t('cashbank:accounts_badgeCash')})`,
    })),
  ]

  const { data: currenciesData } = useQuery({
    queryKey: ['currencies-list'],
    queryFn: () => api.get('/multicurrency/currencies').then((r) => r.data),
  })
  const currencies: any[] = Array.isArray(currenciesData) ? currenciesData : []

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">{t('cashbank:pageTitle')}</h1>
        <p className="text-sm text-gray-500 mt-0.5">{t('cashbank:pageSubtitle')}</p>
      </div>

      {/* Tabs */}
      <div className="border-b border-gray-200">
        <nav className="flex gap-1">
          {[
            { key: 'accounts', label: t('cashbank:tab_accounts'), icon: <Wallet className="h-4 w-4" /> },
            { key: 'transactions', label: t('cashbank:tab_transactions'), icon: <Banknote className="h-4 w-4" /> },
            { key: 'petty-cash', label: t('cashbank:tab_pettyCash'), icon: <Receipt className="h-4 w-4" /> },
            { key: 'transfers', label: t('cashbank:tab_transfers'), icon: <ArrowLeftRight className="h-4 w-4" /> },
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

      {tab === 'accounts' && (
        <AccountsTab entityId={entityId} cashAccounts={cashAccounts} refetch={refetchCashAcc} qc={qc} />
      )}
      {tab === 'transactions' && (
        <TransactionsTab entityId={entityId} accountOptions={accountOptions} currencies={currencies} qc={qc} />
      )}
      {tab === 'petty-cash' && (
        <PettyCashTab entityId={entityId} cashAccounts={cashAccounts.filter((c) => c.account_type === 'petty_cash')} currencies={currencies} qc={qc} />
      )}
      {tab === 'transfers' && (
        <TransfersTab entityId={entityId} accountOptions={accountOptions} cashAccounts={cashAccounts} currencies={currencies} qc={qc} />
      )}
    </div>
  )
}

// ════════════════════════════════════════════════════════════════════════════
// TAB 1: CASH ACCOUNTS (master + balance)
// ════════════════════════════════════════════════════════════════════════════
function AccountsTab({ entityId, cashAccounts, refetch, qc }: any) {
  const { t } = useTranslation(['cashbank', 'common'])
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({ account_name: '', coa_code: '', account_type: 'cash', custodian_name: '', float_amount: '' })

  const balanceQueries = useQueries({
    queries: cashAccounts.map((acc: any) => ({
      queryKey: ['cash-balance', acc.id],
      queryFn: () => api.get(`/cash-bank/cash-accounts/${acc.id}/balance`).then((r) => r.data),
      enabled: !!acc.id,
    })),
  })

  const createMutation = useMutation({
    mutationFn: () => api.post('/cash-bank/cash-accounts', {
      entity_id: entityId, account_name: form.account_name, coa_code: form.coa_code,
      account_type: form.account_type, custodian_name: form.custodian_name || undefined,
      float_amount: parseFloat(form.float_amount) || 0,
    }),
    onSuccess: () => {
      showToast(t('cashbank:accounts_createSuccess'))
      setShowForm(false)
      setForm({ account_name: '', coa_code: '', account_type: 'cash', custodian_name: '', float_amount: '' })
      qc.invalidateQueries({ queryKey: ['cash-accounts'] })
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('cashbank:accounts_createFailed'), 'error'),
  })

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <button onClick={() => setShowForm(true)} className="btn-primary">
          <Plus className="h-4 w-4" /> {t('cashbank:accounts_newAccount')}
        </button>
      </div>

      {showForm && (
        <Card>
          <div className="flex items-center justify-between mb-4">
            <p className="text-sm font-semibold text-gray-700">{t('cashbank:accounts_formTitle')}</p>
            <button onClick={() => setShowForm(false)} className="text-gray-400 hover:text-gray-600"><X className="h-5 w-5" /></button>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
            <div>
              <label className="form-label">{t('cashbank:accounts_type')}</label>
              <select value={form.account_type} onChange={(e) => setForm({ ...form, account_type: e.target.value })} className="form-select">
                <option value="cash">{t('cashbank:accounts_typeCash')}</option>
                <option value="petty_cash">{t('cashbank:accounts_typePettyCash')}</option>
              </select>
            </div>
            <div>
              <label className="form-label">{t('cashbank:accounts_accountName')}</label>
              <input value={form.account_name} onChange={(e) => setForm({ ...form, account_name: e.target.value })}
                className="form-input" placeholder={t('cashbank:accounts_accountNamePlaceholder')} />
            </div>
            <div>
              <label className="form-label">{t('cashbank:accounts_coaCode')}</label>
              <input value={form.coa_code} onChange={(e) => setForm({ ...form, coa_code: e.target.value })}
                className="form-input" placeholder="1-1-001" />
            </div>
            <div>
              <label className="form-label">{t('cashbank:accounts_custodian')}</label>
              <input value={form.custodian_name} onChange={(e) => setForm({ ...form, custodian_name: e.target.value })}
                className="form-input" placeholder={t('cashbank:accounts_custodianPlaceholder')} />
            </div>
            <div>
              <label className="form-label">{t('cashbank:accounts_floatAmount')}</label>
              <input type="number" value={form.float_amount} onChange={(e) => setForm({ ...form, float_amount: e.target.value })}
                className="form-input" placeholder="0" />
            </div>
          </div>
          <div className="flex gap-3 mt-4">
            <button onClick={() => createMutation.mutate()}
              disabled={createMutation.isPending || !form.account_name || !form.coa_code}
              className="btn-primary">
              {createMutation.isPending ? t('common:saving') : t('cashbank:accounts_save')}
            </button>
            <button onClick={() => setShowForm(false)} className="btn-secondary">{t('common:cancel')}</button>
          </div>
        </Card>
      )}

      {cashAccounts.length === 0 ? (
        <div className="card"><EmptyState title={t('cashbank:accounts_emptyTitle')} description={t('cashbank:accounts_emptyDescription')} /></div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {cashAccounts.map((acc: any, idx: number) => {
            const bal = balanceQueries[idx]?.data
            return (
              <div key={acc.id} className="card p-5">
                <div className="flex items-start justify-between mb-3">
                  <div className="flex items-center gap-2">
                    <div className="h-8 w-8 bg-amber-50 rounded-lg flex items-center justify-center">
                      <Wallet className="h-4 w-4 text-amber-600" />
                    </div>
                    <div>
                      <p className="text-sm font-semibold text-gray-900">{acc.account_name}</p>
                      <p className="text-xs text-gray-400">{acc.coa_code} · {acc.coa_name}</p>
                    </div>
                  </div>
                  <span className="text-xs bg-gray-100 text-gray-500 px-2 py-0.5 rounded-full">
                    {acc.account_type === 'petty_cash' ? t('cashbank:accounts_badgePettyCash') : t('cashbank:accounts_badgeCash')}
                  </span>
                </div>
                {acc.custodian_name && (
                  <p className="text-xs text-gray-400 mb-2">{t('cashbank:accounts_picLabel', { name: acc.custodian_name })}</p>
                )}
                <p className="text-xl font-bold text-gray-900">
                  Rp {bal ? formatRupiah((bal as any).balance) : '...'}
                </p>
              </div>
            )
          })}
        </div>
      )}

      <button onClick={() => refetch()} className="btn-secondary text-xs">
        <RefreshCw className="h-3.5 w-3.5" /> {t('common:refresh')}
      </button>
    </div>
  )
}

// ════════════════════════════════════════════════════════════════════════════
// TAB 2: CASH & BANK TRANSACTIONS (kas/bank masuk-keluar non-AP/AR)
// ════════════════════════════════════════════════════════════════════════════
function TransactionsTab({ entityId, accountOptions, currencies, qc }: any) {
  const { t } = useTranslation(['cashbank', 'common'])
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({ account: '', transaction_date: todayISO(), direction: 'in', description: '', currency: 'IDR' })
  const [lines, setLines] = useState([newCtxLine()])

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['cash-transactions', entityId],
    queryFn: () => api.get('/cash-bank/transactions', { params: { entity_id: entityId } }).then((r) => r.data),
    enabled: !!entityId,
  })
  const txns: any[] = Array.isArray(data) ? data : []

  const total = lines.reduce((s, l) => s + (parseFloat(l.amount) || 0), 0)

  const createMutation = useMutation({
    mutationFn: () => {
      const [accountType, accountId] = form.account.split(':')
      return api.post('/cash-bank/transactions', {
        entity_id: entityId, account_type: accountType, account_id: accountId, transaction_date: form.transaction_date,
        direction: form.direction, description: form.description, currency: form.currency,
        lines: lines.map(({ _key, ...l }) => ({ ...l, amount: parseFloat(l.amount) || 0 })),
      })
    },
    onSuccess: () => {
      showToast(t('cashbank:transactions_createSuccess'))
      setShowForm(false)
      setForm({ account: '', transaction_date: todayISO(), direction: 'in', description: '', currency: 'IDR' })
      setLines([newCtxLine()])
      qc.invalidateQueries({ queryKey: ['cash-transactions'] })
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('cashbank:transactions_createFailed'), 'error'),
  })

  const postMutation = useMutation({
    mutationFn: (id: string) => api.post(`/cash-bank/transactions/${id}/post`, { posted_by: 'user' }),
    onSuccess: () => {
      showToast(t('cashbank:transactions_postSuccess'))
      qc.invalidateQueries({ queryKey: ['cash-transactions'] })
      qc.invalidateQueries({ queryKey: ['cash-balance'] })
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('cashbank:transactions_postFailed'), 'error'),
  })

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <button onClick={() => setShowForm(true)} className="btn-primary"><Plus className="h-4 w-4" /> {t('cashbank:transactions_newTransaction')}</button>
      </div>

      {showForm && (
        <Card>
          <div className="flex items-center justify-between mb-4">
            <p className="text-sm font-semibold text-gray-700">{t('cashbank:transactions_formTitle')}</p>
            <button onClick={() => setShowForm(false)} className="text-gray-400 hover:text-gray-600"><X className="h-5 w-5" /></button>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
            <div>
              <label className="form-label">{t('cashbank:transactions_cash')}</label>
              <select value={form.account} onChange={(e) => setForm({ ...form, account: e.target.value })} className="form-select">
                <option value="">{t('cashbank:transactions_selectCash')}</option>
                {accountOptions.map((o: any) => <option key={`${o.type}:${o.id}`} value={`${o.type}:${o.id}`}>{o.label}</option>)}
              </select>
            </div>
            <div>
              <label className="form-label">{t('common:date')}</label>
              <input type="date" value={form.transaction_date} onChange={(e) => setForm({ ...form, transaction_date: e.target.value })} className="form-input" />
            </div>
            <div>
              <label className="form-label">{t('cashbank:transactions_direction')}</label>
              <select value={form.direction} onChange={(e) => setForm({ ...form, direction: e.target.value })} className="form-select">
                <option value="in">{t('cashbank:transactions_directionIn')}</option>
                <option value="out">{t('cashbank:transactions_directionOut')}</option>
              </select>
            </div>
            <div className="col-span-2 md:col-span-1">
              <label className="form-label">{t('common:description')}</label>
              <input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} className="form-input" placeholder={t('cashbank:transactions_descriptionPlaceholder')} />
            </div>
            <div>
              <label className="form-label">{t('cashbank:currency')}</label>
              <select value={form.currency} onChange={(e) => setForm({ ...form, currency: e.target.value })} className="form-select">
                {currencies.map((c: any) => <option key={c.currency_code} value={c.currency_code}>{c.currency_code}</option>)}
              </select>
            </div>
          </div>
          <p className="text-xs text-gray-400 mb-3">{t('cashbank:transactions_exampleHint')}</p>
          {form.currency !== 'IDR' && (
            <p className="text-xs text-amber-700 bg-amber-50 rounded-md px-3 py-2 mb-3">{t('cashbank:currencyFcyHint')}</p>
          )}

          <div className="mb-3">
            <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
              {t('cashbank:transactions_linesTitle', { side: form.direction === 'in' ? t('cashbank:transactions_linesSideIn') : t('cashbank:transactions_linesSideOut') })}
            </p>
            <div className="border rounded-lg overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="text-left px-3 py-2 font-medium text-gray-600 w-36">{t('cashbank:transactions_colAccountCode')}</th>
                    <th className="text-left px-3 py-2 font-medium text-gray-600">{t('common:description')}</th>
                    <th className="text-right px-3 py-2 font-medium text-gray-600 w-36">{t('cashbank:transactions_amountColumn')}</th>
                    <th className="w-8" />
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {lines.map((l) => (
                    <tr key={l._key}>
                      <td className="px-2 py-1.5">
                        <input value={l.account_code}
                          onChange={(e) => setLines((ls) => ls.map((x) => x._key === l._key ? { ...x, account_code: e.target.value } : x))}
                          className="form-input text-xs font-mono" placeholder="6-9-013" />
                      </td>
                      <td className="px-2 py-1.5">
                        <input value={l.description}
                          onChange={(e) => setLines((ls) => ls.map((x) => x._key === l._key ? { ...x, description: e.target.value } : x))}
                          className="form-input text-xs w-full" placeholder={t('cashbank:transactions_lineDescriptionPlaceholder')} />
                      </td>
                      <td className="px-2 py-1.5">
                        <input type="number" value={l.amount}
                          onChange={(e) => setLines((ls) => ls.map((x) => x._key === l._key ? { ...x, amount: e.target.value } : x))}
                          className="form-input text-xs text-right w-full" placeholder="0" />
                      </td>
                      <td className="px-1">
                        {lines.length > 1 && (
                          <button onClick={() => setLines((ls) => ls.filter((x) => x._key !== l._key))} className="text-gray-300 hover:text-red-400">
                            <Trash2 className="h-4 w-4" />
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
                <tfoot className="bg-gray-50">
                  <tr>
                    <td colSpan={2} className="px-3 py-2 text-right text-sm font-semibold text-gray-700">{t('common:total')}:</td>
                    <td className="px-3 py-2 text-right font-bold text-primary-700">Rp {formatRupiah(total)}</td>
                    <td />
                  </tr>
                </tfoot>
              </table>
            </div>
            <button onClick={() => setLines((ls) => [...ls, newCtxLine()])} className="mt-2 text-xs text-primary-600 hover:underline flex items-center gap-1">
              <Plus className="h-3 w-3" /> {t('cashbank:transactions_addLine')}
            </button>
          </div>

          <div className="flex gap-3">
            <button onClick={() => createMutation.mutate()}
              disabled={createMutation.isPending || !form.account || !form.description || total <= 0}
              className="btn-primary">
              {createMutation.isPending ? t('common:saving') : t('cashbank:transactions_saveDraft')}
            </button>
            <button onClick={() => setShowForm(false)} className="btn-secondary">{t('common:cancel')}</button>
          </div>
        </Card>
      )}

      <Card noPad>
        <CardHeader title={t('cashbank:transactions_listTitle')} subtitle={t('cashbank:transactions_listSubtitle', { count: txns.length })}
          actions={<button onClick={() => refetch()} className="btn-secondary"><RefreshCw className="h-4 w-4" /></button>} />
        {isLoading ? (
          <div className="flex justify-center py-16"><Spinner size="lg" /></div>
        ) : txns.length === 0 ? (
          <EmptyState title={t('cashbank:transactions_emptyTitle')} />
        ) : (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>{t('cashbank:transactions_colNo')}</th><th>{t('common:date')}</th><th>{t('cashbank:transactions_colCash')}</th><th>{t('cashbank:transactions_colDirection')}</th>
                  <th>{t('common:description')}</th><th className="right">{t('cashbank:transactions_colAmount')}</th><th>{t('cashbank:colCurrency')}</th><th>{t('common:status')}</th><th>{t('common:action')}</th>
                </tr>
              </thead>
              <tbody>
                {txns.map((tx: any) => (
                  <tr key={tx.id}>
                    <td className="font-mono text-xs text-gray-500">{tx.transaction_no}</td>
                    <td className="text-sm text-gray-500 whitespace-nowrap">{formatDate(tx.transaction_date)}</td>
                    <td className="text-sm">{tx.account_name}</td>
                    <td>
                      <span className={`text-xs px-2 py-0.5 rounded-full ${tx.direction === 'in' ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'}`}>
                        {tx.direction === 'in' ? t('cashbank:transactions_directionInBadge') : t('cashbank:transactions_directionOutBadge')}
                      </span>
                    </td>
                    <td className="text-sm max-w-xs truncate" title={tx.description}>{tx.description}</td>
                    <td className="right">Rp {formatRupiah(tx.amount)}</td>
                    <td className="text-xs text-gray-500">
                      {tx.currency && tx.currency !== 'IDR' ? `${tx.currency} ${formatRupiah(tx.amount_fcy)}` : 'IDR'}
                    </td>
                    <td><Badge status={tx.status} /></td>
                    <td>
                      {tx.status === 'draft' && (
                        <button onClick={() => postMutation.mutate(tx.id)}
                          className="text-xs px-2 py-1 rounded-md bg-blue-50 text-blue-600 hover:bg-blue-100">
                          {t('common:post')}
                        </button>
                      )}
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

// ════════════════════════════════════════════════════════════════════════════
// TAB 3: PETTY CASH EXPENSE
// ════════════════════════════════════════════════════════════════════════════
function PettyCashTab({ entityId, cashAccounts, currencies, qc }: any) {
  const { t } = useTranslation(['cashbank', 'common'])
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({
    cash_account_id: '', expense_date: todayISO(), account_code: '', amount: '', description: '', receipt_ref: '', currency: 'IDR',
  })

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['petty-cash-expenses', entityId],
    queryFn: () => api.get('/cash-bank/petty-cash/expenses', { params: { entity_id: entityId } }).then((r) => r.data),
    enabled: !!entityId,
  })
  const expenses: any[] = Array.isArray(data) ? data : []

  const createMutation = useMutation({
    mutationFn: () => api.post('/cash-bank/petty-cash/expenses', {
      entity_id: entityId, cash_account_id: form.cash_account_id, expense_date: form.expense_date,
      account_code: form.account_code, amount: parseFloat(form.amount) || 0,
      description: form.description || undefined, receipt_ref: form.receipt_ref || undefined,
      currency: form.currency,
    }),
    onSuccess: () => {
      showToast(t('cashbank:pettyCash_createSuccess'))
      setShowForm(false)
      setForm({ cash_account_id: '', expense_date: todayISO(), account_code: '', amount: '', description: '', receipt_ref: '', currency: 'IDR' })
      qc.invalidateQueries({ queryKey: ['petty-cash-expenses'] })
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('cashbank:pettyCash_createFailed'), 'error'),
  })

  const postMutation = useMutation({
    mutationFn: (id: string) => api.post(`/cash-bank/petty-cash/expenses/${id}/post`, { posted_by: 'user' }),
    onSuccess: () => {
      showToast(t('cashbank:pettyCash_postSuccess'))
      qc.invalidateQueries({ queryKey: ['petty-cash-expenses'] })
      qc.invalidateQueries({ queryKey: ['cash-balance'] })
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('cashbank:pettyCash_postFailed'), 'error'),
  })

  if (cashAccounts.length === 0) {
    return <div className="card"><EmptyState title={t('cashbank:pettyCash_emptyAccountsTitle')} description={t('cashbank:pettyCash_emptyAccountsDescription')} /></div>
  }

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <button onClick={() => setShowForm(true)} className="btn-primary"><Plus className="h-4 w-4" /> {t('cashbank:pettyCash_newExpense')}</button>
      </div>

      {showForm && (
        <Card>
          <div className="flex items-center justify-between mb-4">
            <p className="text-sm font-semibold text-gray-700">{t('cashbank:pettyCash_formTitle')}</p>
            <button onClick={() => setShowForm(false)} className="text-gray-400 hover:text-gray-600"><X className="h-5 w-5" /></button>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
            <div>
              <label className="form-label">{t('cashbank:pettyCash_cashAccount')}</label>
              <select value={form.cash_account_id} onChange={(e) => setForm({ ...form, cash_account_id: e.target.value })} className="form-select">
                <option value="">{t('cashbank:pettyCash_selectAccount')}</option>
                {cashAccounts.map((a: any) => <option key={a.id} value={a.id}>{a.account_name}</option>)}
              </select>
            </div>
            <div>
              <label className="form-label">{t('common:date')}</label>
              <input type="date" value={form.expense_date} onChange={(e) => setForm({ ...form, expense_date: e.target.value })} className="form-input" />
            </div>
            <div>
              <label className="form-label">{t('cashbank:pettyCash_accountCode')}</label>
              <input value={form.account_code} onChange={(e) => setForm({ ...form, account_code: e.target.value })} className="form-input font-mono" placeholder="6-9-013" />
            </div>
            <div>
              <label className="form-label">{t('cashbank:pettyCash_amountRp')}</label>
              <input type="number" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} className="form-input" placeholder="0" />
            </div>
            <div>
              <label className="form-label">{t('cashbank:pettyCash_receiptRef')}</label>
              <input value={form.receipt_ref} onChange={(e) => setForm({ ...form, receipt_ref: e.target.value })} className="form-input" placeholder={t('cashbank:pettyCash_receiptPlaceholder')} />
            </div>
            <div>
              <label className="form-label">{t('cashbank:currency')}</label>
              <select value={form.currency} onChange={(e) => setForm({ ...form, currency: e.target.value })} className="form-select">
                {currencies.map((c: any) => <option key={c.currency_code} value={c.currency_code}>{c.currency_code}</option>)}
              </select>
            </div>
            <div className="col-span-2 md:col-span-3">
              <label className="form-label">{t('common:description')}</label>
              <input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} className="form-input" placeholder={t('cashbank:pettyCash_descriptionPlaceholder')} />
            </div>
          </div>
          {form.currency !== 'IDR' && (
            <p className="text-xs text-amber-700 bg-amber-50 rounded-md px-3 py-2 mt-3">{t('cashbank:currencyFcyHint')}</p>
          )}
          <div className="flex gap-3 mt-4">
            <button onClick={() => createMutation.mutate()}
              disabled={createMutation.isPending || !form.cash_account_id || !form.account_code || !(parseFloat(form.amount) > 0)}
              className="btn-primary">
              {createMutation.isPending ? t('common:saving') : t('cashbank:pettyCash_saveDraft')}
            </button>
            <button onClick={() => setShowForm(false)} className="btn-secondary">{t('common:cancel')}</button>
          </div>
        </Card>
      )}

      <Card noPad>
        <CardHeader title={t('cashbank:pettyCash_listTitle')} subtitle={t('cashbank:pettyCash_listSubtitle', { count: expenses.length })}
          actions={<button onClick={() => refetch()} className="btn-secondary"><RefreshCw className="h-4 w-4" /></button>} />
        {isLoading ? (
          <div className="flex justify-center py-16"><Spinner size="lg" /></div>
        ) : expenses.length === 0 ? (
          <EmptyState title={t('cashbank:pettyCash_emptyTitle')} />
        ) : (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>{t('common:date')}</th><th>{t('cashbank:pettyCash_colCash')}</th><th>{t('cashbank:pettyCash_colAccount')}</th><th>{t('common:description')}</th>
                  <th>{t('cashbank:pettyCash_colReceipt')}</th><th className="right">{t('common:amount')}</th><th>{t('cashbank:colCurrency')}</th><th>{t('common:status')}</th><th>{t('cashbank:pettyCash_colReplenish')}</th><th>{t('common:action')}</th>
                </tr>
              </thead>
              <tbody>
                {expenses.map((e: any) => (
                  <tr key={e.id}>
                    <td className="text-sm text-gray-500 whitespace-nowrap">{formatDate(e.expense_date)}</td>
                    <td className="text-sm">{e.cash_account_name}</td>
                    <td className="font-mono text-xs">{e.account_code}</td>
                    <td className="text-sm max-w-xs truncate" title={e.description}>{e.description}</td>
                    <td className="text-xs text-gray-500">{e.receipt_ref ?? '—'}</td>
                    <td className="right">Rp {formatRupiah(e.amount)}</td>
                    <td className="text-xs text-gray-500">
                      {e.currency && e.currency !== 'IDR' ? `${e.currency} ${formatRupiah(e.amount_fcy)}` : 'IDR'}
                    </td>
                    <td><Badge status={e.status} /></td>
                    <td>
                      {e.status === 'posted' && (
                        <span className={`text-xs px-2 py-0.5 rounded-full ${e.replenished ? 'bg-green-100 text-green-700' : 'bg-amber-100 text-amber-700'}`}>
                          {e.replenished ? t('cashbank:pettyCash_replenishedYes') : t('cashbank:pettyCash_replenishedNo')}
                        </span>
                      )}
                    </td>
                    <td>
                      {e.status === 'draft' && (
                        <button onClick={() => postMutation.mutate(e.id)}
                          className="text-xs px-2 py-1 rounded-md bg-blue-50 text-blue-600 hover:bg-blue-100">
                          {t('common:post')}
                        </button>
                      )}
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

// ════════════════════════════════════════════════════════════════════════════
// TAB 4: IN-HOUSE TRANSFER
// ════════════════════════════════════════════════════════════════════════════
function TransfersTab({ entityId, accountOptions, cashAccounts, currencies, qc }: any) {
  const { t } = useTranslation(['cashbank', 'common'])
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({
    transfer_date: todayISO(), source: '', dest: '', amount: '', purpose: 'transfer', description: '', currency: 'IDR',
  })

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['in-house-transfers', entityId],
    queryFn: () => api.get('/cash-bank/transfers', { params: { entity_id: entityId } }).then((r) => r.data),
    enabled: !!entityId,
  })
  const transfers: any[] = Array.isArray(data) ? data : []

  const destPettyCashId = form.purpose === 'petty_cash_topup' ? form.dest.split(':')[1] : null
  const { data: outstandingData } = useQuery({
    queryKey: ['petty-cash-outstanding', destPettyCashId],
    queryFn: () => api.get(`/cash-bank/petty-cash/cash-accounts/${destPettyCashId}/outstanding`).then((r) => r.data),
    enabled: !!destPettyCashId,
  })

  const createMutation = useMutation({
    mutationFn: () => {
      const [sType, sId] = form.source.split(':')
      const [dType, dId] = form.dest.split(':')
      return api.post('/cash-bank/transfers', {
        entity_id: entityId, transfer_date: form.transfer_date,
        source_type: sType, source_id: sId, dest_type: dType, dest_id: dId,
        amount: parseFloat(form.amount) || 0, purpose: form.purpose, description: form.description || undefined,
        currency: form.currency,
      })
    },
    onSuccess: () => {
      showToast(t('cashbank:transfers_createSuccess'))
      setShowForm(false)
      setForm({ transfer_date: todayISO(), source: '', dest: '', amount: '', purpose: 'transfer', description: '', currency: 'IDR' })
      qc.invalidateQueries({ queryKey: ['in-house-transfers'] })
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('cashbank:transfers_createFailed'), 'error'),
  })

  const postMutation = useMutation({
    mutationFn: async (tr: any) => {
      let replenish_expense_ids: string[] | undefined
      if (tr.purpose === 'petty_cash_topup' && tr.dest_type === 'cash') {
        const outstanding = await api.get('/cash-bank/petty-cash/expenses', {
          params: { entity_id: entityId, cash_account_id: tr.dest_id, status: 'posted', replenished: false },
        }).then((r) => r.data)
        replenish_expense_ids = Array.isArray(outstanding) ? outstanding.map((e: any) => e.id) : []
      }
      return api.post(`/cash-bank/transfers/${tr.id}/post`, { posted_by: 'user', replenish_expense_ids })
    },
    onSuccess: (res: any) => {
      const n = res?.data?.replenished_expenses
      showToast(n ? t('cashbank:transfers_postSuccessReplenished', { count: n }) : t('cashbank:transfers_postSuccess'))
      qc.invalidateQueries({ queryKey: ['in-house-transfers'] })
      qc.invalidateQueries({ queryKey: ['cash-balance'] })
      qc.invalidateQueries({ queryKey: ['petty-cash-expenses'] })
      qc.invalidateQueries({ queryKey: ['petty-cash-outstanding'] })
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('cashbank:transfers_postFailed'), 'error'),
  })

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <button onClick={() => setShowForm(true)} className="btn-primary"><Plus className="h-4 w-4" /> {t('cashbank:transfers_newTransfer')}</button>
      </div>

      {showForm && (
        <Card>
          <div className="flex items-center justify-between mb-4">
            <p className="text-sm font-semibold text-gray-700">{t('cashbank:transfers_formTitle')}</p>
            <button onClick={() => setShowForm(false)} className="text-gray-400 hover:text-gray-600"><X className="h-5 w-5" /></button>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
            <div>
              <label className="form-label">{t('common:date')}</label>
              <input type="date" value={form.transfer_date} onChange={(e) => setForm({ ...form, transfer_date: e.target.value })} className="form-input" />
            </div>
            <div>
              <label className="form-label">{t('cashbank:transfers_purpose')}</label>
              <select value={form.purpose} onChange={(e) => setForm({ ...form, purpose: e.target.value })} className="form-select">
                <option value="transfer">{t('cashbank:transfers_purposeTransfer')}</option>
                <option value="petty_cash_topup">{t('cashbank:transfers_purposeTopup')}</option>
              </select>
            </div>
            <div>
              <label className="form-label">{t('cashbank:transfers_amountRp')}</label>
              <input type="number" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} className="form-input" placeholder="0" />
            </div>
            <div>
              <label className="form-label">{t('cashbank:transfers_source')}</label>
              <select value={form.source} onChange={(e) => setForm({ ...form, source: e.target.value })} className="form-select">
                <option value="">{t('cashbank:transfers_selectSource')}</option>
                {accountOptions.map((o: any) => <option key={`${o.type}:${o.id}`} value={`${o.type}:${o.id}`}>{o.label}</option>)}
              </select>
            </div>
            <div>
              <label className="form-label">{t('cashbank:transfers_dest')}</label>
              <select value={form.dest} onChange={(e) => setForm({ ...form, dest: e.target.value })} className="form-select">
                <option value="">{t('cashbank:transfers_selectDest')}</option>
                {accountOptions.map((o: any) => <option key={`${o.type}:${o.id}`} value={`${o.type}:${o.id}`}>{o.label}</option>)}
              </select>
            </div>
            <div>
              <label className="form-label">{t('common:description')}</label>
              <input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} className="form-input" placeholder={t('cashbank:transfers_descriptionOptional')} />
            </div>
            <div>
              <label className="form-label">{t('cashbank:currency')}</label>
              <select value={form.currency} onChange={(e) => setForm({ ...form, currency: e.target.value })} className="form-select">
                {currencies.map((c: any) => <option key={c.currency_code} value={c.currency_code}>{c.currency_code}</option>)}
              </select>
            </div>
          </div>
          {form.currency !== 'IDR' && (
            <p className="text-xs text-amber-700 bg-amber-50 rounded-md px-3 py-2 mt-3">{t('cashbank:currencyFcyHint')}</p>
          )}
          {destPettyCashId && outstandingData && outstandingData.total_outstanding > 0 && (
            <p className="text-xs text-amber-700 bg-amber-50 rounded-md px-3 py-2 mt-3">
              {t('cashbank:transfers_outstandingHint', { amount: formatRupiah(outstandingData.total_outstanding), count: outstandingData.count_items })}
            </p>
          )}
          <div className="flex gap-3 mt-4">
            <button onClick={() => createMutation.mutate()}
              disabled={createMutation.isPending || !form.source || !form.dest || !(parseFloat(form.amount) > 0)}
              className="btn-primary">
              {createMutation.isPending ? t('common:saving') : t('cashbank:transfers_saveDraft')}
            </button>
            <button onClick={() => setShowForm(false)} className="btn-secondary">{t('common:cancel')}</button>
          </div>
        </Card>
      )}

      <Card noPad>
        <CardHeader title={t('cashbank:transfers_listTitle')} subtitle={t('cashbank:transfers_listSubtitle', { count: transfers.length })}
          actions={<button onClick={() => refetch()} className="btn-secondary"><RefreshCw className="h-4 w-4" /></button>} />
        {isLoading ? (
          <div className="flex justify-center py-16"><Spinner size="lg" /></div>
        ) : transfers.length === 0 ? (
          <EmptyState title={t('cashbank:transfers_emptyTitle')} />
        ) : (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>{t('cashbank:transfers_colNo')}</th><th>{t('common:date')}</th><th>{t('cashbank:transfers_colFrom')}</th><th>{t('cashbank:transfers_colTo')}</th>
                  <th>{t('cashbank:transfers_colPurpose')}</th><th className="right">{t('cashbank:transfers_colAmount')}</th><th>{t('cashbank:colCurrency')}</th><th>{t('common:status')}</th><th>{t('common:action')}</th>
                </tr>
              </thead>
              <tbody>
                {transfers.map((tr: any) => (
                  <tr key={tr.id}>
                    <td className="font-mono text-xs text-gray-500">{tr.transfer_no}</td>
                    <td className="text-sm text-gray-500 whitespace-nowrap">{formatDate(tr.transfer_date)}</td>
                    <td className="text-sm">{tr.source_name ?? '—'}</td>
                    <td className="text-sm">{tr.dest_name ?? '—'}</td>
                    <td>
                      <span className="text-xs px-2 py-0.5 rounded-full bg-purple-50 text-purple-700">
                        {tr.purpose === 'petty_cash_topup' ? t('cashbank:transfers_purposeTopup') : t('cashbank:transfers_purposeTransfer')}
                      </span>
                    </td>
                    <td className="right">Rp {formatRupiah(tr.amount)}</td>
                    <td className="text-xs text-gray-500">
                      {tr.currency && tr.currency !== 'IDR' ? `${tr.currency} ${formatRupiah(tr.amount_fcy)}` : 'IDR'}
                    </td>
                    <td><Badge status={tr.status} /></td>
                    <td>
                      {tr.status === 'draft' && (
                        <button onClick={() => postMutation.mutate(tr)}
                          className="text-xs px-2 py-1 rounded-md bg-blue-50 text-blue-600 hover:bg-blue-100">
                          {t('common:post')}
                        </button>
                      )}
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
