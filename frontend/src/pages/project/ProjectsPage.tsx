import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { useQuery, useMutation } from '@tanstack/react-query'
import { Plus, RefreshCw, CheckCircle, XCircle, ArrowRight, Send } from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext'
import api from '../../lib/api'
import { Card, CardHeader } from '../../components/ui/Card'
import Spinner from '../../components/ui/Spinner'
import EmptyState from '../../components/ui/EmptyState'
import Badge from '../../components/ui/Badge'
import { formatRupiah, formatDate } from '../../lib/utils'
import { showToast } from '../../components/ui/Toast'

const INDUSTRY_OPTS = ['general', 'construction', 'software', 'consulting', 'research', 'internal']

export default function ProjectsPage() {
  const { t } = useTranslation(['project', 'common'])
  const { entityId, user } = useAuth()
  const isAdmin = user?.role === 'admin' || user?.role === 'superadmin'

  const [charterFilter, setCharterFilter] = useState('')
  const { data, isLoading, refetch } = useQuery({
    queryKey: ['projects', entityId, charterFilter],
    queryFn: () => api.get('/projects', {
      params: { entity_id: entityId, charter_status: charterFilter || undefined },
    }).then((r) => r.data),
    enabled: !!entityId,
  })
  const projects: any[] = data?.items ?? []

  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({
    project_code: '', project_name: '', industry_type: 'general', objective: '',
    start_date: '', end_date: '', budget_amount: '', priority: 'medium',
  })

  const createMutation = useMutation({
    mutationFn: () => api.post('/projects', {
      entity_id: entityId,
      project_code: form.project_code,
      project_name: form.project_name,
      industry_type: form.industry_type,
      objective: form.objective,
      start_date: form.start_date,
      end_date: form.end_date,
      budget_amount: parseFloat(form.budget_amount) || 0,
      priority: form.priority,
    }),
    onSuccess: (res) => {
      showToast(t('project:createSuccess', { code: res.data.project_code }))
      setShowForm(false)
      setForm({ project_code: '', project_name: '', industry_type: 'general', objective: '', start_date: '', end_date: '', budget_amount: '', priority: 'medium' })
      refetch()
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('project:createFailed'), 'error'),
  })

  const charterMutation = useMutation({
    mutationFn: ({ id, action }: { id: string; action: 'approved' | 'rejected' }) =>
      api.post(`/projects/${id}/approve-charter`, null, { params: { action } }),
    onSuccess: () => { showToast(t('common:success')); refetch() },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('common:failed'), 'error'),
  })

  const submitCharterMutation = useMutation({
    mutationFn: (id: string) => api.put(`/projects/${id}`, { charter_status: 'pending_approval' }),
    onSuccess: () => { showToast(t('common:success')); refetch() },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('common:failed'), 'error'),
  })

  const formValid = form.project_code && form.project_name && form.objective
    && form.start_date && form.end_date && parseFloat(form.budget_amount) >= 0

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">{t('project:pageTitle')}</h1>
        <p className="text-sm text-gray-500 mt-0.5">{t('project:pageSubtitle')}</p>
      </div>

      <div className="flex items-center justify-between">
        <select value={charterFilter} onChange={(e) => setCharterFilter(e.target.value)} className="form-select w-56">
          <option value="">{t('common:allStatus')}</option>
          {['draft', 'pending_approval', 'approved', 'rejected', 'on_hold', 'closed'].map((s) => (
            <option key={s} value={s}>{t(s, { ns: 'common' })}</option>
          ))}
        </select>
        {isAdmin && (
          <button onClick={() => setShowForm((s) => !s)} className="btn-primary">
            <Plus className="h-4 w-4" /> {t('project:newBtn')}
          </button>
        )}
      </div>

      {showForm && (
        <Card>
          <p className="text-sm font-semibold text-gray-800 mb-4">{t('project:formTitle')}</p>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-4 mb-4">
            <div>
              <label className="form-label">{t('project:codeLabel')}</label>
              <input value={form.project_code} onChange={(e) => setForm({ ...form, project_code: e.target.value })} className="form-input" placeholder="PRJ-2026-001" />
            </div>
            <div className="md:col-span-2">
              <label className="form-label">{t('project:nameLabel')}</label>
              <input value={form.project_name} onChange={(e) => setForm({ ...form, project_name: e.target.value })} className="form-input" />
            </div>
            <div>
              <label className="form-label">{t('project:industryLabel')}</label>
              <select value={form.industry_type} onChange={(e) => setForm({ ...form, industry_type: e.target.value })} className="form-select">
                {INDUSTRY_OPTS.map((o) => <option key={o} value={o}>{t(`project:industry_${o}`)}</option>)}
              </select>
            </div>
            <div>
              <label className="form-label">{t('project:priorityLabel')}</label>
              <select value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })} className="form-select">
                {['low', 'medium', 'high', 'critical'].map((p) => <option key={p} value={p}>{t(p, { ns: 'common' })}</option>)}
              </select>
            </div>
            <div>
              <label className="form-label">{t('project:budgetLabel')}</label>
              <input type="number" value={form.budget_amount} onChange={(e) => setForm({ ...form, budget_amount: e.target.value })} className="form-input" />
            </div>
            <div>
              <label className="form-label">{t('project:startDateLabel')}</label>
              <input type="date" value={form.start_date} onChange={(e) => setForm({ ...form, start_date: e.target.value })} className="form-input" />
            </div>
            <div>
              <label className="form-label">{t('project:endDateLabel')}</label>
              <input type="date" value={form.end_date} onChange={(e) => setForm({ ...form, end_date: e.target.value })} className="form-input" />
            </div>
          </div>
          <div className="mb-4">
            <label className="form-label">{t('project:objectiveLabel')}</label>
            <textarea value={form.objective} onChange={(e) => setForm({ ...form, objective: e.target.value })} className="form-input" rows={2} />
          </div>
          <div className="flex justify-end gap-3">
            <button onClick={() => setShowForm(false)} className="btn-secondary">{t('common:cancel')}</button>
            <button onClick={() => createMutation.mutate()} disabled={createMutation.isPending || !formValid} className="btn-primary">
              {createMutation.isPending ? t('common:saving') : t('common:save')}
            </button>
          </div>
        </Card>
      )}

      <Card noPad>
        <CardHeader title={t('project:listTitle')} subtitle={t('project:listSubtitle', { count: projects.length })}
          actions={<button onClick={() => refetch()} className="btn-secondary"><RefreshCw className="h-4 w-4" /></button>} />
        {isLoading ? (
          <div className="flex justify-center py-16"><Spinner size="lg" /></div>
        ) : projects.length === 0 ? (
          <EmptyState title={t('project:emptyTitle')} />
        ) : (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>{t('project:colCode')}</th>
                  <th>{t('project:colName')}</th>
                  <th>{t('project:colIndustry')}</th>
                  <th>{t('common:status')}</th>
                  <th className="right">{t('project:colBudget')}</th>
                  <th className="right">{t('project:colCompletion')}</th>
                  <th>{t('project:colDates')}</th>
                  <th>{t('common:action')}</th>
                </tr>
              </thead>
              <tbody>
                {projects.map((p: any) => (
                  <tr key={p.id}>
                    <td className="text-sm font-medium">
                      <Link to={`/projects/${p.id}`} className="text-primary-700 hover:underline inline-flex items-center gap-1">
                        {p.project_code} <ArrowRight className="h-3 w-3" />
                      </Link>
                    </td>
                    <td className="text-sm text-gray-700">{p.project_name}</td>
                    <td className="text-sm text-gray-500">{t(`project:industry_${p.industry_type}`)}</td>
                    <td><Badge status={p.charter_status} /></td>
                    <td className="right text-sm">Rp {formatRupiah(p.budget_amount)}</td>
                    <td className="right text-sm">{p.completion_pct ?? 0}%</td>
                    <td className="text-xs text-gray-400">{formatDate(p.start_date)} – {formatDate(p.end_date)}</td>
                    <td>
                      {isAdmin && p.charter_status === 'draft' && (
                        <button onClick={() => submitCharterMutation.mutate(p.id)} disabled={submitCharterMutation.isPending}
                          className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-md bg-blue-50 text-blue-700 hover:bg-blue-100">
                          <Send className="h-3 w-3" /> {t('project:submitCharterBtn')}
                        </button>
                      )}
                      {isAdmin && p.charter_status === 'pending_approval' && (
                        <div className="flex items-center gap-1.5">
                          <button onClick={() => charterMutation.mutate({ id: p.id, action: 'approved' })} disabled={charterMutation.isPending}
                            className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-md bg-green-50 text-green-700 hover:bg-green-100">
                            <CheckCircle className="h-3 w-3" /> {t('common:approve')}
                          </button>
                          <button onClick={() => charterMutation.mutate({ id: p.id, action: 'rejected' })} disabled={charterMutation.isPending}
                            className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-md bg-red-50 text-red-700 hover:bg-red-100">
                            <XCircle className="h-3 w-3" /> {t('common:reject')}
                          </button>
                        </div>
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
