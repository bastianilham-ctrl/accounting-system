import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Plus, Search, Filter, RefreshCw, Send, CheckCircle, XCircle, FileText } from 'lucide-react'
import { Link } from 'react-router-dom'
import { useAuth } from '../../contexts/AuthContext'
import api from '../../lib/api'
import { Card, CardHeader } from '../../components/ui/Card'
import Spinner from '../../components/ui/Spinner'
import EmptyState from '../../components/ui/EmptyState'
import Badge from '../../components/ui/Badge'
import { formatRupiah, formatDate } from '../../lib/utils'
import { showToast } from '../../components/ui/Toast'

export default function JournalListPage() {
  const { entityId, user } = useAuth()
  const { t } = useTranslation(['journalList', 'common'])
  const qc = useQueryClient()

  const STATUS_OPTS = [
    { value: '', label: t('common:allStatus') },
    { value: 'draft', label: t('common:draft') },
    { value: 'submitted', label: t('journalList:statusSubmitted') },
    { value: 'approved', label: t('common:approved') },
    { value: 'posted', label: t('common:posted') },
    { value: 'rejected', label: t('journalList:statusRejected') },
    { value: 'cancelled', label: t('common:cancelled') },
  ]

  const TYPE_OPTS = [
    { value: '', label: t('journalList:typeAll') },
    { value: 'general', label: t('journalList:typeGeneral') },
    { value: 'adjustment', label: t('journalList:typeAdjustment') },
    { value: 'accrual', label: t('journalList:typeAccrual') },
    { value: 'prepaid', label: t('journalList:typePrepaid') },
    { value: 'depreciation', label: t('journalList:typeDepreciation') },
    { value: 'provision', label: t('journalList:typeProvision') },
    { value: 'closing', label: t('journalList:typeClosing') },
  ]
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('')
  const [jType, setJType] = useState('')
  const [page, setPage] = useState(1)
  const pageSize = 30

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['journal-entries', entityId, status, jType, page],
    queryFn: () =>
      api.get('/journal-entries', {
        params: {
          entity_id: entityId,
          status: status || undefined,
          journal_type: jType || undefined,
          page, size: pageSize,
        },
      }).then((r) => r.data),
    enabled: !!entityId,
  })

  const items: any[] = Array.isArray(data) ? data : (data?.items ?? data?.entries ?? [])
  const total: number = data?.total ?? items.length

  const filtered = items.filter((j) =>
    !search ||
    j.journal_number?.toLowerCase().includes(search.toLowerCase()) ||
    j.description?.toLowerCase().includes(search.toLowerCase()),
  )

  const submitMutation = useMutation({
    mutationFn: (id: string) => api.post(`/journal-entries/${id}/submit`),
    onSuccess: () => { showToast(t('journalList:toastSubmitSuccess')); qc.invalidateQueries({ queryKey: ['journal-entries'] }) },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('journalList:toastSubmitFailed'), 'error'),
  })

  const approveMutation = useMutation({
    mutationFn: (id: string) => api.post(`/journal-entries/${id}/approve`),
    onSuccess: () => { showToast(t('journalList:toastApproveSuccess')); qc.invalidateQueries({ queryKey: ['journal-entries'] }) },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('journalList:toastApproveFailed'), 'error'),
  })

  const postMutation = useMutation({
    mutationFn: (id: string) => api.post(`/journal-entries/${id}/post`),
    onSuccess: () => { showToast(t('journalList:toastPostSuccess')); qc.invalidateQueries({ queryKey: ['journal-entries'] }) },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('journalList:toastPostFailed'), 'error'),
  })

  const canApprove = user?.role === 'admin' || user?.role === 'approver' || user?.role === 'superadmin'

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">{t('journalList:title')}</h1>
          <p className="text-sm text-gray-500 mt-0.5">{t('journalList:subtitle')}</p>
        </div>
        <Link to="/journals/new" className="btn-primary">
          <Plus className="h-4 w-4" /> {t('journalList:createButton')}
        </Link>
      </div>

      <Card noPad>
        <CardHeader
          title={t('journalList:listTitle')}
          subtitle={`${filtered.length} ${t('journalList:journalUnit')}`}
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
            <input value={search} onChange={(e) => setSearch(e.target.value)}
              placeholder={t('journalList:searchPlaceholder')} className="form-input pl-9" />
          </div>
          <div className="flex items-center gap-2">
            <Filter className="h-4 w-4 text-gray-400" />
            <select value={status} onChange={(e) => { setStatus(e.target.value); setPage(1) }}
              className="form-select w-44">
              {STATUS_OPTS.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
            </select>
            <select value={jType} onChange={(e) => { setJType(e.target.value); setPage(1) }}
              className="form-select w-36">
              {TYPE_OPTS.map((opt) => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
            </select>
          </div>
        </div>

        {isLoading ? (
          <div className="flex justify-center py-16"><Spinner size="lg" /></div>
        ) : filtered.length === 0 ? (
          <EmptyState
            title={t('journalList:emptyTitle')}
            description={t('journalList:emptyDescription')}
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>{t('journalList:colJournalNo')}</th>
                  <th>{t('common:date')}</th>
                  <th>{t('common:type')}</th>
                  <th>{t('common:description')}</th>
                  <th className="right">{t('journalList:colTotalDr')}</th>
                  <th>{t('common:status')}</th>
                  <th>{t('common:actions')}</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((j) => (
                  <tr key={j.id}>
                    <td className="font-mono text-xs font-medium text-primary-600">
                      {j.journal_number ?? j.id?.substring(0, 8)}
                    </td>
                    <td className="text-sm">{formatDate(j.journal_date)}</td>
                    <td>
                      <span className="text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded-full capitalize">
                        {j.journal_type}
                      </span>
                    </td>
                    <td className="text-sm max-w-xs truncate">{j.description}</td>
                    <td className="right text-sm">{formatRupiah(j.total_debit ?? j.total_amount)}</td>
                    <td><Badge status={j.status ?? 'draft'} /></td>
                    <td>
                      <div className="flex items-center gap-1.5">
                        {j.status === 'draft' && (
                          <button
                            onClick={() => submitMutation.mutate(j.id)}
                            disabled={submitMutation.isPending}
                            className="inline-flex items-center gap-1 text-xs px-2 py-1 bg-blue-50 text-blue-600 hover:bg-blue-100 rounded-md transition-colors"
                          >
                            <Send className="h-3 w-3" /> {t('journalList:actionSubmit')}
                          </button>
                        )}
                        {j.status === 'submitted' && canApprove && (
                          <button
                            onClick={() => approveMutation.mutate(j.id)}
                            disabled={approveMutation.isPending}
                            className="inline-flex items-center gap-1 text-xs px-2 py-1 bg-green-50 text-green-600 hover:bg-green-100 rounded-md transition-colors"
                          >
                            <CheckCircle className="h-3 w-3" /> {t('journalList:actionApprove')}
                          </button>
                        )}
                        {j.status === 'approved' && canApprove && (
                          <button
                            onClick={() => postMutation.mutate(j.id)}
                            disabled={postMutation.isPending}
                            className="inline-flex items-center gap-1 text-xs px-2 py-1 bg-primary-50 text-primary-600 hover:bg-primary-100 rounded-md transition-colors"
                          >
                            <FileText className="h-3 w-3" /> {t('journalList:actionPostGL')}
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {total > pageSize && (
          <div className="px-6 py-3 border-t border-gray-100 flex items-center justify-between text-sm text-gray-500">
            <span>{t('common:page')} {page} {t('common:of')} {Math.ceil(total / pageSize)}</span>
            <div className="flex gap-2">
              <button disabled={page === 1} onClick={() => setPage(p => p - 1)} className="btn-secondary py-1">{t('common:previous')}</button>
              <button disabled={page * pageSize >= total} onClick={() => setPage(p => p + 1)} className="btn-secondary py-1">{t('common:next')}</button>
            </div>
          </div>
        )}
      </Card>
    </div>
  )
}
