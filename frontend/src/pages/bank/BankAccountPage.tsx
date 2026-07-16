import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Plus, RefreshCw, Building2, TrendingUp, TrendingDown } from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext'
import api from '../../lib/api'
import { Card, CardHeader } from '../../components/ui/Card'
import Spinner from '../../components/ui/Spinner'
import EmptyState from '../../components/ui/EmptyState'
import { formatRupiah, formatDate } from '../../lib/utils'
import { showToast } from '../../components/ui/Toast'

export default function BankAccountPage() {
  const { entityId } = useAuth()
  const { t } = useTranslation(['bank', 'common'])
  const qc = useQueryClient()
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({
    bank_name: '', account_no: '', account_name: '', currency: 'IDR', coa_code: '',
  })

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['bank-accounts', entityId],
    queryFn: () =>
      api.get('/bank/accounts', { params: { entity_id: entityId } }).then((r) => r.data),
    enabled: !!entityId,
  })

  const accounts: any[] = Array.isArray(data) ? data : (data?.accounts ?? data?.items ?? [])
  const totalBalance = accounts.reduce((s, a) => s + (a.current_balance ?? a.balance ?? 0), 0)

  const createMutation = useMutation({
    mutationFn: () => api.post('/bank/accounts', { ...form, entity_id: entityId }),
    onSuccess: () => {
      showToast(t('bank:accountPage_createSuccess'))
      setShowForm(false)
      setForm({ bank_name: '', account_no: '', account_name: '', currency: 'IDR', coa_code: '' })
      qc.invalidateQueries({ queryKey: ['bank-accounts'] })
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('bank:accountPage_createFailed'), 'error'),
  })

  const BANKS = ['BCA', 'Mandiri', 'BNI', 'BRI', 'CIMB', 'Permata', 'OCBC', 'Danamon', 'BTN', t('bank:accountPage_bankOther')]

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">{t('bank:accountPage_title')}</h1>
          <p className="text-sm text-gray-500 mt-0.5">{t('bank:accountPage_subtitle')}</p>
        </div>
        <button onClick={() => setShowForm(true)} className="btn-primary">
          <Plus className="h-4 w-4" /> {t('bank:accountPage_addAccount')}
        </button>
      </div>

      {/* Add form */}
      {showForm && (
        <Card>
          <p className="text-sm font-semibold text-gray-700 mb-4">{t('bank:accountPage_newAccountTitle')}</p>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
            <div>
              <label className="form-label">{t('bank:accountPage_bank')}</label>
              <select value={form.bank_name} onChange={(e) => setForm({ ...form, bank_name: e.target.value })}
                className="form-select">
                <option value="">{t('bank:accountPage_selectBank')}</option>
                {BANKS.map((b) => <option key={b} value={b}>{b}</option>)}
              </select>
            </div>
            <div>
              <label className="form-label">{t('bank:accountPage_accountNo')}</label>
              <input value={form.account_no} onChange={(e) => setForm({ ...form, account_no: e.target.value })}
                className="form-input" placeholder="1234567890" />
            </div>
            <div>
              <label className="form-label">{t('bank:accountPage_accountName')}</label>
              <input value={form.account_name} onChange={(e) => setForm({ ...form, account_name: e.target.value })}
                className="form-input" placeholder={t('bank:accountPage_accountNamePlaceholder')} />
            </div>
            <div>
              <label className="form-label">{t('bank:accountPage_currency')}</label>
              <select value={form.currency} onChange={(e) => setForm({ ...form, currency: e.target.value })}
                className="form-select">
                {['IDR', 'USD', 'SGD', 'EUR', 'JPY'].map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>
            <div>
              <label className="form-label">{t('bank:accountPage_coaCode')}</label>
              <input value={form.coa_code} onChange={(e) => setForm({ ...form, coa_code: e.target.value })}
                className="form-input" placeholder="1-1110" />
            </div>
          </div>
          <div className="flex gap-3 mt-4">
            <button onClick={() => createMutation.mutate()} disabled={createMutation.isPending || !form.bank_name || !form.account_no}
              className="btn-primary">
              {createMutation.isPending ? t('bank:accountPage_saving') : t('bank:accountPage_save')}
            </button>
            <button onClick={() => setShowForm(false)} className="btn-secondary">{t('bank:accountPage_cancel')}</button>
          </div>
        </Card>
      )}

      {/* Total balance card */}
      {accounts.length > 0 && (
        <div className="card p-5 flex items-center gap-4 border-l-4 border-primary-500">
          <div className="h-12 w-12 bg-primary-50 rounded-xl flex items-center justify-center">
            <Building2 className="h-6 w-6 text-primary-600" />
          </div>
          <div>
            <p className="text-sm text-gray-500">{t('bank:accountPage_totalBalance')}</p>
            <p className="text-2xl font-bold text-gray-900">Rp {formatRupiah(totalBalance)}</p>
          </div>
        </div>
      )}

      {/* Accounts grid */}
      {isLoading ? (
        <div className="flex justify-center py-16"><Spinner size="lg" /></div>
      ) : accounts.length === 0 ? (
        <div className="card">
          <EmptyState title={t('bank:accountPage_emptyTitle')} description={t('bank:accountPage_emptyDescription')} />
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {accounts.map((acc) => {
            const balance = acc.current_balance ?? acc.balance ?? 0
            return (
              <div key={acc.id} className="card p-5">
                <div className="flex items-start justify-between mb-3">
                  <div className="flex items-center gap-2">
                    <div className="h-8 w-8 bg-blue-50 rounded-lg flex items-center justify-center">
                      <Building2 className="h-4 w-4 text-blue-600" />
                    </div>
                    <div>
                      <p className="text-sm font-semibold text-gray-900">{acc.bank_name}</p>
                      <p className="text-xs text-gray-400">{acc.currency}</p>
                    </div>
                  </div>
                  <span className="text-xs bg-gray-100 text-gray-500 px-2 py-0.5 rounded-full">
                    {acc.account_no}
                  </span>
                </div>
                <p className="text-sm text-gray-500 mb-2">{acc.account_name}</p>
                <p className={`text-xl font-bold ${balance >= 0 ? 'text-gray-900' : 'text-red-600'}`}>
                  Rp {formatRupiah(balance)}
                </p>
                {acc.last_sync_at && (
                  <p className="text-xs text-gray-400 mt-2">
                    {t('bank:accountPage_syncedAt')}: {formatDate(acc.last_sync_at)}
                  </p>
                )}
                <div className="mt-3 pt-3 border-t border-gray-100 flex gap-2">
                  <button className="btn-secondary py-1 text-xs flex-1">{t('bank:accountPage_reconcile')}</button>
                  <button className="btn-secondary py-1 text-xs flex-1">{t('bank:accountPage_mutation')}</button>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
