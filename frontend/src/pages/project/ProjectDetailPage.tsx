import { useState, Fragment } from 'react'
import { useParams, Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useQuery, useMutation } from '@tanstack/react-query'
import { ArrowLeft, Plus, RefreshCw, Zap, CheckCircle2, Pencil, Paperclip, Trash2, UserPlus } from 'lucide-react'
import api from '../../lib/api'
import { useAuth } from '../../contexts/AuthContext'
import { Card, CardHeader } from '../../components/ui/Card'
import Spinner from '../../components/ui/Spinner'
import EmptyState from '../../components/ui/EmptyState'
import Badge from '../../components/ui/Badge'
import AttachmentPanel from '../../components/shared/AttachmentPanel'
import { formatRupiah, formatDate, currentYear } from '../../lib/utils'
import { showToast } from '../../components/ui/Toast'

const TABS = ['overview', 'team', 'tasks', 'budget', 'finance', 'health'] as const
type Tab = typeof TABS[number]
const ROLE_OPTS = ['project_director', 'sponsor', 'project_manager', 'work_package_manager', 'team_lead', 'member', 'consultant', 'reviewer', 'stakeholder']

function OverviewTab({ projectId, project, onRefetchProject }: { projectId: string; project: any; onRefetchProject: () => void }) {
  const { t } = useTranslation(['project', 'common'])
  const { entityId, user } = useAuth()
  const isAdmin = user?.role === 'admin' || user?.role === 'superadmin'

  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({ item_type: 'in_scope', description: '', sequence: 1 })

  const [showHeaderEdit, setShowHeaderEdit] = useState(false)
  const [headerForm, setHeaderForm] = useState({
    project_name: project.project_name ?? '',
    industry_type: project.industry_type ?? 'general',
    priority: project.priority ?? 'medium',
    start_date: project.start_date?.slice(0, 10) ?? '',
    end_date: project.end_date?.slice(0, 10) ?? '',
    budget_amount: String(project.budget_amount ?? ''),
    project_manager_id: project.project_manager_id ?? '',
    sponsor_id: project.sponsor_id ?? '',
  })

  const [showObjEdit, setShowObjEdit] = useState(false)
  const [objForm, setObjForm] = useState('')

  const { data: employeesData } = useQuery({
    queryKey: ['employees', entityId],
    queryFn: () => api.get('/employees/', { params: { entity_id: entityId } }).then((r) => r.data),
    enabled: !!entityId && isAdmin,
  })
  const employees: any[] = Array.isArray(employeesData) ? employeesData : []

  const { data: scope, isLoading, refetch } = useQuery({
    queryKey: ['project-scope', projectId],
    queryFn: () => api.get(`/projects/${projectId}/scope`).then((r) => r.data),
  })

  const addItemMutation = useMutation({
    mutationFn: () => api.post(`/projects/${projectId}/scope/items`, form),
    onSuccess: () => {
      showToast(t('project:overview_addItemSuccess'))
      setShowForm(false)
      setForm({ item_type: 'in_scope', description: '', sequence: 1 })
      refetch()
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('project:overview_addItemFailed'), 'error'),
  })

  const updateHeaderMutation = useMutation({
    mutationFn: () => api.put(`/projects/${projectId}`, {
      project_name: headerForm.project_name,
      industry_type: headerForm.industry_type,
      priority: headerForm.priority,
      start_date: headerForm.start_date,
      end_date: headerForm.end_date,
      budget_amount: parseFloat(headerForm.budget_amount) || 0,
      project_manager_id: headerForm.project_manager_id || null,
      sponsor_id: headerForm.sponsor_id || null,
    }),
    onSuccess: () => { showToast(t('project:overview_updateSuccess')); setShowHeaderEdit(false); onRefetchProject() },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('project:overview_updateFailed'), 'error'),
  })

  const updateObjMutation = useMutation({
    mutationFn: () => api.put(`/projects/${projectId}/scope`, { objective: objForm }),
    onSuccess: () => { showToast(t('project:overview_updateSuccess')); setShowObjEdit(false); refetch() },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('project:overview_updateFailed'), 'error'),
  })

  const pmName = employees.find((e) => e.employee_id === project.project_manager_id)?.full_name
  const sponsorName = employees.find((e) => e.employee_id === project.sponsor_id)?.full_name

  const groups: [string, string][] = [
    ['in_scope', t('project:overview_inScope')],
    ['out_of_scope', t('project:overview_outScope')],
    ['assumption', t('project:overview_assumptions')],
    ['constraint', t('project:overview_constraints')],
  ]

  return (
    <div className="space-y-4">
      {isAdmin && (
        <Card noPad>
          <CardHeader title={t('project:overview_headerTitle')}
            actions={<button onClick={() => setShowHeaderEdit((s) => !s)} className="btn-secondary"><Pencil className="h-4 w-4" /> {t('project:overview_editBtn')}</button>} />
          <div className="card-body space-y-4">
            {showHeaderEdit ? (
              <>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                  <div className="md:col-span-2">
                    <label className="form-label">{t('project:nameLabel')}</label>
                    <input value={headerForm.project_name} onChange={(e) => setHeaderForm({ ...headerForm, project_name: e.target.value })} className="form-input" />
                  </div>
                  <div>
                    <label className="form-label">{t('project:industryLabel')}</label>
                    <select value={headerForm.industry_type} onChange={(e) => setHeaderForm({ ...headerForm, industry_type: e.target.value })} className="form-select">
                      {['general', 'construction', 'software', 'consulting', 'research', 'internal'].map((o) => (
                        <option key={o} value={o}>{t(`project:industry_${o}`)}</option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label className="form-label">{t('project:priorityLabel')}</label>
                    <select value={headerForm.priority} onChange={(e) => setHeaderForm({ ...headerForm, priority: e.target.value })} className="form-select">
                      {['low', 'medium', 'high', 'critical'].map((p) => <option key={p} value={p}>{t(p, { ns: 'common' })}</option>)}
                    </select>
                  </div>
                  <div>
                    <label className="form-label">{t('project:startDateLabel')}</label>
                    <input type="date" value={headerForm.start_date} onChange={(e) => setHeaderForm({ ...headerForm, start_date: e.target.value })} className="form-input" />
                  </div>
                  <div>
                    <label className="form-label">{t('project:endDateLabel')}</label>
                    <input type="date" value={headerForm.end_date} onChange={(e) => setHeaderForm({ ...headerForm, end_date: e.target.value })} className="form-input" />
                  </div>
                  <div>
                    <label className="form-label">{t('project:budgetLabel')}</label>
                    <input type="number" value={headerForm.budget_amount} onChange={(e) => setHeaderForm({ ...headerForm, budget_amount: e.target.value })} className="form-input" />
                  </div>
                  <div>
                    <label className="form-label">{t('project:overview_pmLabel')}</label>
                    <select value={headerForm.project_manager_id} onChange={(e) => setHeaderForm({ ...headerForm, project_manager_id: e.target.value })} className="form-select">
                      <option value="">{t('project:overview_pmNone')}</option>
                      {employees.map((e) => <option key={e.employee_id} value={e.employee_id}>{e.full_name} ({e.employee_no})</option>)}
                    </select>
                  </div>
                  <div>
                    <label className="form-label">{t('project:overview_sponsorLabel')}</label>
                    <select value={headerForm.sponsor_id} onChange={(e) => setHeaderForm({ ...headerForm, sponsor_id: e.target.value })} className="form-select">
                      <option value="">{t('project:overview_pmNone')}</option>
                      {employees.map((e) => <option key={e.employee_id} value={e.employee_id}>{e.full_name} ({e.employee_no})</option>)}
                    </select>
                  </div>
                </div>
                <div className="flex justify-end gap-2">
                  <button onClick={() => setShowHeaderEdit(false)} className="btn-secondary">{t('common:cancel')}</button>
                  <button onClick={() => updateHeaderMutation.mutate()} disabled={updateHeaderMutation.isPending} className="btn-primary">
                    {t('common:save')}
                  </button>
                </div>
              </>
            ) : (
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                <div><p className="text-xs text-gray-500">{t('project:industryLabel')}</p><p className="font-medium">{t(`project:industry_${project.industry_type}`)}</p></div>
                <div><p className="text-xs text-gray-500">{t('project:priorityLabel')}</p><p className="font-medium capitalize">{project.priority ?? '—'}</p></div>
                <div><p className="text-xs text-gray-500">{t('project:budgetLabel')}</p><p className="font-medium">Rp {formatRupiah(project.budget_amount)}</p></div>
                <div><p className="text-xs text-gray-500">{t('project:colDates')}</p><p className="font-medium">{formatDate(project.start_date)} – {formatDate(project.end_date)}</p></div>
                <div><p className="text-xs text-gray-500">{t('project:overview_pmLabel')}</p><p className="font-medium">{pmName ?? t('project:overview_pmNone')}</p></div>
                <div><p className="text-xs text-gray-500">{t('project:overview_sponsorLabel')}</p><p className="font-medium">{sponsorName ?? t('project:overview_pmNone')}</p></div>
              </div>
            )}
          </div>
        </Card>
      )}

      <Card noPad>
        <CardHeader title={t('project:overview_objective')}
          actions={isAdmin && (
            <button onClick={() => { setObjForm(scope?.objective ?? ''); setShowObjEdit((s) => !s) }} className="btn-secondary">
              <Pencil className="h-4 w-4" /> {t('project:overview_editObjectiveBtn')}
            </button>
          )} />
        <div className="card-body">
          {showObjEdit ? (
            <div className="space-y-3">
              <textarea value={objForm} onChange={(e) => setObjForm(e.target.value)} className="form-input" rows={3} />
              <div className="flex justify-end gap-2">
                <button onClick={() => setShowObjEdit(false)} className="btn-secondary">{t('common:cancel')}</button>
                <button onClick={() => updateObjMutation.mutate()} disabled={updateObjMutation.isPending} className="btn-primary">{t('common:save')}</button>
              </div>
            </div>
          ) : (
            <p className="text-sm text-gray-600">{scope?.objective ?? '—'}</p>
          )}
        </div>
      </Card>

      <Card noPad>
        <CardHeader title={t('project:overview_scopeTitle')}
          actions={<button onClick={() => setShowForm((s) => !s)} className="btn-secondary"><Plus className="h-4 w-4" /> {t('project:overview_addItemBtn')}</button>} />
        <div className="card-body space-y-4">
          {showForm && (
            <div className="border border-gray-100 rounded-lg p-4 space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="form-label">{t('project:overview_itemTypeLabel')}</label>
                  <select value={form.item_type} onChange={(e) => setForm({ ...form, item_type: e.target.value })} className="form-select">
                    {['in_scope', 'out_of_scope', 'assumption', 'constraint', 'acceptance'].map((it) => (
                      <option key={it} value={it}>{t(`project:scopeType_${it}`)}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="form-label">{t('project:overview_itemDescLabel')}</label>
                  <input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} className="form-input" />
                </div>
              </div>
              <div className="flex justify-end gap-2">
                <button onClick={() => setShowForm(false)} className="btn-secondary">{t('common:cancel')}</button>
                <button onClick={() => addItemMutation.mutate()} disabled={addItemMutation.isPending || !form.description} className="btn-primary">
                  {t('common:save')}
                </button>
              </div>
            </div>
          )}
          {isLoading ? (
            <Spinner size="lg" />
          ) : !scope ? (
            <EmptyState title={t('project:overview_noScope')} />
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {groups.map(([key, label]) => (
                <div key={key}>
                  <p className="text-xs font-semibold text-gray-500 mb-1">{label}</p>
                  {(scope.items?.[key] ?? []).length === 0 ? (
                    <p className="text-xs text-gray-400">—</p>
                  ) : (
                    <ul className="text-sm text-gray-700 list-disc list-inside space-y-0.5">
                      {scope.items[key].map((it: any, idx: number) => <li key={idx}>{it.description}</li>)}
                    </ul>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </Card>
    </div>
  )
}

function TeamTab({ projectId }: { projectId: string }) {
  const { t } = useTranslation(['project', 'common'])
  const { entityId, user } = useAuth()
  const isAdmin = user?.role === 'admin' || user?.role === 'superadmin'

  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({ employee_id: '', role_in_project: 'member', wbs_item_id: '', allocation_pct: '100' })

  const { data: teamData, isLoading, refetch } = useQuery({
    queryKey: ['project-team', projectId],
    queryFn: () => api.get(`/projects/${projectId}/team`).then((r) => r.data),
  })
  const team: any[] = Array.isArray(teamData) ? teamData : []

  const { data: employeesData } = useQuery({
    queryKey: ['employees', entityId],
    queryFn: () => api.get('/employees/', { params: { entity_id: entityId } }).then((r) => r.data),
    enabled: !!entityId,
  })
  const employees: any[] = Array.isArray(employeesData) ? employeesData : []

  const { data: wbsData } = useQuery({
    queryKey: ['project-wbs', projectId],
    queryFn: () => api.get(`/projects/${projectId}/wbs`).then((r) => r.data),
  })
  const wbsItems: any[] = Array.isArray(wbsData) ? wbsData : []

  const addMutation = useMutation({
    mutationFn: () => api.post(`/projects/${projectId}/team`, {
      employee_id: form.employee_id,
      role_in_project: form.role_in_project,
      wbs_item_id: form.wbs_item_id || null,
      allocation_pct: parseFloat(form.allocation_pct) || 100,
    }),
    onSuccess: () => {
      showToast(t('project:team_addSuccess'))
      setShowForm(false)
      setForm({ employee_id: '', role_in_project: 'member', wbs_item_id: '', allocation_pct: '100' })
      refetch()
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('project:team_addFailed'), 'error'),
  })

  const removeMutation = useMutation({
    mutationFn: (employeeId: string) => api.delete(`/projects/${projectId}/team/${employeeId}`),
    onSuccess: () => { showToast(t('project:team_removeSuccess')); refetch() },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('project:team_removeFailed'), 'error'),
  })

  return (
    <Card noPad>
      <CardHeader title={t('project:team_listTitle')}
        actions={isAdmin && (
          <button onClick={() => setShowForm((s) => !s)} className="btn-primary"><UserPlus className="h-4 w-4" /> {t('project:team_addBtn')}</button>
        )} />
      <div className="card-body space-y-4">
        {showForm && (
          <div className="border border-gray-100 rounded-lg p-4 space-y-3">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <div>
                <label className="form-label">{t('project:team_employeeLabel')}</label>
                <select value={form.employee_id} onChange={(e) => setForm({ ...form, employee_id: e.target.value })} className="form-select">
                  <option value="">—</option>
                  {employees.map((e) => <option key={e.employee_id} value={e.employee_id}>{e.full_name} ({e.employee_no})</option>)}
                </select>
              </div>
              <div>
                <label className="form-label">{t('project:team_roleLabel')}</label>
                <select value={form.role_in_project} onChange={(e) => setForm({ ...form, role_in_project: e.target.value })} className="form-select">
                  {ROLE_OPTS.map((r) => <option key={r} value={r}>{t(`project:role_${r}`)}</option>)}
                </select>
              </div>
              <div>
                <label className="form-label">{t('project:team_wbsLabel')}</label>
                <select value={form.wbs_item_id} onChange={(e) => setForm({ ...form, wbs_item_id: e.target.value })} className="form-select">
                  <option value="">{t('project:team_wbsNone')}</option>
                  {wbsItems.map((w: any) => <option key={w.id} value={w.id}>{w.wbs_code} — {w.wbs_name}</option>)}
                </select>
              </div>
              <div>
                <label className="form-label">{t('project:team_allocLabel')}</label>
                <input type="number" value={form.allocation_pct} onChange={(e) => setForm({ ...form, allocation_pct: e.target.value })} className="form-input" />
              </div>
            </div>
            <div className="flex justify-end gap-2">
              <button onClick={() => setShowForm(false)} className="btn-secondary">{t('common:cancel')}</button>
              <button onClick={() => addMutation.mutate()} disabled={addMutation.isPending || !form.employee_id} className="btn-primary">
                {t('common:save')}
              </button>
            </div>
          </div>
        )}
        {isLoading ? (
          <Spinner size="lg" />
        ) : team.length === 0 ? (
          <EmptyState title={t('project:team_emptyTitle')} />
        ) : (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>{t('project:team_colName')}</th>
                  <th>{t('project:team_colRole')}</th>
                  <th>{t('project:team_colWbs')}</th>
                  <th className="right">{t('project:team_colAlloc')}</th>
                  {isAdmin && <th>{t('common:action')}</th>}
                </tr>
              </thead>
              <tbody>
                {team.map((m: any) => (
                  <tr key={m.id}>
                    <td className="text-sm font-medium">{m.full_name} <span className="text-xs text-gray-400">({m.employee_no})</span></td>
                    <td className="text-sm text-gray-700">{t(`project:role_${m.role_in_project}`)}</td>
                    <td className="text-sm text-gray-500">{m.wbs_code ? `${m.wbs_code} — ${m.wbs_name}` : t('project:team_wbsNone')}</td>
                    <td className="right text-sm">{m.allocation_pct}%</td>
                    {isAdmin && (
                      <td>
                        <button
                          onClick={() => { if (window.confirm(t('project:team_removeConfirm', { name: m.full_name }))) removeMutation.mutate(m.employee_id) }}
                          className="text-xs px-2 py-1 rounded-md bg-red-50 text-red-700 hover:bg-red-100 inline-flex items-center gap-1"
                        >
                          <Trash2 className="h-3 w-3" /> {t('common:delete')}
                        </button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </Card>
  )
}

function TasksTab({ projectId, project }: { projectId: string; project: any }) {
  const { t } = useTranslation(['project', 'common'])
  const { user } = useAuth()
  const isSuperAdmin = user?.role === 'superadmin'
  const [showTaskForm, setShowTaskForm] = useState(false)
  const [taskForm, setTaskForm] = useState({ task_name: '', planned_start: '', planned_end: '', planned_cost: '', milestone_id: '' })
  const [showMsForm, setShowMsForm] = useState(false)
  const [msForm, setMsForm] = useState({ milestone_name: '', target_start_date: '', target_date: '' })

  const [actualTask, setActualTask] = useState<any | null>(null)
  const [actualForm, setActualForm] = useState({ actual_start: '', actual_end: '', actual_hours: '', actual_cost: '', progress_pct: '0', status: 'not_started' })
  const [attachTarget, setAttachTarget] = useState<{ refType: 'project_task' | 'project_milestone'; refId: string; title: string } | null>(null)
  const [issueDraft, setIssueDraft] = useState<Record<string, string>>({})

  const [editMs, setEditMs] = useState<any | null>(null)
  const [editMsForm, setEditMsForm] = useState({ milestone_name: '', target_start_date: '', target_date: '' })
  const [editTask, setEditTask] = useState<any | null>(null)
  const [editTaskForm, setEditTaskForm] = useState({ task_name: '', planned_start: '', planned_end: '', planned_cost: '', milestone_id: '' })

  const { data: tasksData, isLoading: tasksLoading, refetch: refetchTasks } = useQuery({
    queryKey: ['project-tasks', projectId],
    queryFn: () => api.get(`/projects/${projectId}/tasks`).then((r) => r.data),
  })
  const tasks: any[] = Array.isArray(tasksData) ? tasksData : []

  const { data: msData, isLoading: msLoading, refetch: refetchMs } = useQuery({
    queryKey: ['project-milestones', projectId],
    queryFn: () => api.get(`/projects/${projectId}/milestones`).then((r) => r.data),
  })
  const milestones: any[] = Array.isArray(msData) ? msData : []

  const createTaskMutation = useMutation({
    mutationFn: () => api.post(`/projects/${projectId}/tasks`, {
      task_name: taskForm.task_name,
      planned_start: taskForm.planned_start,
      planned_end: taskForm.planned_end,
      planned_cost: parseFloat(taskForm.planned_cost) || 0,
      milestone_id: taskForm.milestone_id || null,
    }),
    onSuccess: (res) => {
      showToast(t('project:task_createSuccess', { code: res.data.task_code }))
      setShowTaskForm(false)
      setTaskForm({ task_name: '', planned_start: '', planned_end: '', planned_cost: '', milestone_id: '' })
      refetchTasks()
      refetchMs()
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('project:task_createFailed'), 'error'),
  })

  const updateActualMutation = useMutation({
    mutationFn: () => api.put(`/projects/${projectId}/tasks/${actualTask.id}`, {
      actual_start: actualForm.actual_start || null,
      actual_end: actualForm.actual_end || null,
      actual_hours: actualForm.actual_hours ? parseFloat(actualForm.actual_hours) : null,
      actual_cost: actualForm.actual_cost ? parseFloat(actualForm.actual_cost) : null,
      progress_pct: Math.max(0, Math.min(100, parseInt(actualForm.progress_pct, 10) || 0)),
      status: actualForm.status,
    }),
    onSuccess: () => { showToast(t('project:task_updateActualSuccess')); setActualTask(null); refetchTasks(); refetchMs() },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('project:task_updateActualFailed'), 'error'),
  })

  const updateMsPlanMutation = useMutation({
    mutationFn: () => api.put(`/projects/${projectId}/milestones/${editMs.id}`, {
      milestone_name: editMsForm.milestone_name,
      target_start_date: editMsForm.target_start_date || null,
      target_date: editMsForm.target_date,
    }),
    onSuccess: () => { showToast(t('project:plan_editSuccess')); setEditMs(null); refetchMs() },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('project:plan_editFailed'), 'error'),
  })

  const updateTaskPlanMutation = useMutation({
    mutationFn: () => api.put(`/projects/${projectId}/tasks/${editTask.id}`, {
      task_name: editTaskForm.task_name,
      planned_start: editTaskForm.planned_start,
      planned_end: editTaskForm.planned_end,
      planned_cost: parseFloat(editTaskForm.planned_cost) || 0,
      milestone_id: editTaskForm.milestone_id || '',
    }),
    onSuccess: () => { showToast(t('project:plan_editSuccess')); setEditTask(null); refetchTasks(); refetchMs() },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('project:plan_editFailed'), 'error'),
  })

  const cpmMutation = useMutation({
    mutationFn: () => api.post(`/projects/${projectId}/compute-cpm`),
    onSuccess: (res) => { showToast(t('project:task_cpmSuccess', { days: res.data.project_duration_days })); refetchTasks() },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('project:task_cpmFailed'), 'error'),
  })

  const createMsMutation = useMutation({
    mutationFn: () => api.post(`/projects/${projectId}/milestones`, {
      milestone_name: msForm.milestone_name,
      target_start_date: msForm.target_start_date || null,
      target_date: msForm.target_date,
    }),
    onSuccess: () => {
      showToast(t('project:milestone_createSuccess'))
      setShowMsForm(false)
      setMsForm({ milestone_name: '', target_start_date: '', target_date: '' })
      refetchMs()
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('project:milestone_createFailed'), 'error'),
  })

  const updateMsMutation = useMutation({
    mutationFn: ({ id, status, progress_pct }: { id: string; status: string; progress_pct?: number }) =>
      api.put(`/projects/${projectId}/milestones/${id}`, {
        status,
        actual_date: status === 'achieved' ? new Date().toISOString().slice(0, 10) : undefined,
        progress_pct,
      }),
    onSuccess: () => { showToast(t('project:milestone_updateSuccess')); refetchMs() },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('project:milestone_updateFailed'), 'error'),
  })

  const updateMsProgressMutation = useMutation({
    mutationFn: ({ id, progress_pct }: { id: string; progress_pct: number }) =>
      api.put(`/projects/${projectId}/milestones/${id}`, { progress_pct }),
    onSuccess: () => { showToast(t('project:milestone_updateSuccess')); refetchMs() },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('project:milestone_updateFailed'), 'error'),
  })

  const updateMsProgressStatusMutation = useMutation({
    mutationFn: ({ id, progress_status }: { id: string; progress_status: string }) =>
      api.put(`/projects/${projectId}/milestones/${id}`, { progress_status }),
    onSuccess: () => { showToast(t('project:milestone_updateSuccess')); refetchMs() },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('project:milestone_updateFailed'), 'error'),
  })

  const updateMsIssueMutation = useMutation({
    mutationFn: ({ id, issue_notes }: { id: string; issue_notes: string }) =>
      api.put(`/projects/${projectId}/milestones/${id}`, { issue_notes }),
    onSuccess: () => { showToast(t('project:milestone_updateSuccess')); refetchMs() },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('project:milestone_updateFailed'), 'error'),
  })

  const openActualForm = (tk: any) => {
    setActualTask(tk)
    setActualForm({
      actual_start: tk.actual_start ?? '',
      actual_end: tk.actual_end ?? '',
      actual_hours: String(tk.actual_hours ?? ''),
      actual_cost: String(tk.actual_cost ?? ''),
      progress_pct: String(tk.progress_pct ?? 0),
      status: tk.status ?? 'not_started',
    })
  }

  const openEditMs = (ms: any) => {
    setEditMs(ms)
    setEditMsForm({
      milestone_name: ms.milestone_name ?? '',
      target_start_date: ms.target_start_date ?? '',
      target_date: ms.target_date ?? '',
    })
  }

  const openEditTask = (tk: any) => {
    setEditTask(tk)
    setEditTaskForm({
      task_name: tk.task_name ?? '',
      planned_start: tk.planned_start ?? '',
      planned_end: tk.planned_end ?? '',
      planned_cost: String(tk.planned_cost ?? ''),
      milestone_id: tk.milestone_id ?? '',
    })
  }

  const tasksByMilestone: Record<string, any[]> = {}
  const unassignedTasks: any[] = []
  for (const tk of tasks) {
    if (tk.milestone_id) {
      (tasksByMilestone[tk.milestone_id] ??= []).push(tk)
    } else {
      unassignedTasks.push(tk)
    }
  }
  const isLoadingGroup = tasksLoading || msLoading
  const isEmptyGroup = tasks.length === 0 && milestones.length === 0

  return (
    <div className="space-y-6">
      <Card noPad>
        <CardHeader title={t('project:plan_title')}
          actions={
            <div className="flex items-center gap-2">
              <button onClick={() => cpmMutation.mutate()} disabled={cpmMutation.isPending} className="btn-secondary">
                <Zap className="h-4 w-4" /> {t('project:task_computeCpmBtn')}
              </button>
              {isSuperAdmin && (
                <>
                  <button onClick={() => setShowMsForm((s) => !s)} className="btn-secondary">
                    <Plus className="h-4 w-4" /> {t('project:milestone_newBtn')}
                  </button>
                  <button onClick={() => setShowTaskForm((s) => !s)} className="btn-primary">
                    <Plus className="h-4 w-4" /> {t('project:task_newBtn')}
                  </button>
                </>
              )}
            </div>
          } />
        <div className="card-body space-y-4">
          {showMsForm && (
            <div className="border border-gray-100 rounded-lg p-4 space-y-3">
              <p className="text-xs font-semibold text-gray-500">{t('project:milestone_newBtn')}</p>
              <div className="grid grid-cols-3 gap-3">
                <div>
                  <label className="form-label">{t('project:milestone_nameLabel')}</label>
                  <input value={msForm.milestone_name} onChange={(e) => setMsForm({ ...msForm, milestone_name: e.target.value })} className="form-input" />
                </div>
                <div>
                  <label className="form-label">{t('project:milestone_targetStartLabel')}</label>
                  <input type="date" value={msForm.target_start_date} onChange={(e) => setMsForm({ ...msForm, target_start_date: e.target.value })} className="form-input" />
                </div>
                <div>
                  <label className="form-label">{t('project:milestone_targetDateLabel')}</label>
                  <input type="date" value={msForm.target_date} onChange={(e) => setMsForm({ ...msForm, target_date: e.target.value })} className="form-input" />
                </div>
              </div>
              <div className="flex justify-end gap-2">
                <button onClick={() => setShowMsForm(false)} className="btn-secondary">{t('common:cancel')}</button>
                <button onClick={() => createMsMutation.mutate()} disabled={createMsMutation.isPending || !msForm.milestone_name || !msForm.target_date} className="btn-primary">
                  {t('common:save')}
                </button>
              </div>
            </div>
          )}
          {showTaskForm && (
            <div className="border border-gray-100 rounded-lg p-4 space-y-3">
              <p className="text-xs font-semibold text-gray-500">{t('project:task_newBtn')}</p>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <div className="md:col-span-2">
                  <label className="form-label">{t('project:task_nameLabel')}</label>
                  <input value={taskForm.task_name} onChange={(e) => setTaskForm({ ...taskForm, task_name: e.target.value })} className="form-input" />
                </div>
                <div>
                  <label className="form-label">{t('project:task_startLabel')}</label>
                  <input type="date" value={taskForm.planned_start} onChange={(e) => setTaskForm({ ...taskForm, planned_start: e.target.value })} className="form-input" />
                </div>
                <div>
                  <label className="form-label">{t('project:task_endLabel')}</label>
                  <input type="date" value={taskForm.planned_end} onChange={(e) => setTaskForm({ ...taskForm, planned_end: e.target.value })} className="form-input" />
                </div>
                <div>
                  <label className="form-label">{t('project:task_costLabel')}</label>
                  <input type="number" value={taskForm.planned_cost} onChange={(e) => setTaskForm({ ...taskForm, planned_cost: e.target.value })} className="form-input" />
                </div>
                <div>
                  <label className="form-label">{t('project:task_milestoneLabel')}</label>
                  <select value={taskForm.milestone_id} onChange={(e) => setTaskForm({ ...taskForm, milestone_id: e.target.value })} className="form-select">
                    <option value="">{t('project:task_milestoneNone')}</option>
                    {milestones.map((ms: any) => <option key={ms.id} value={ms.id}>{ms.milestone_name}</option>)}
                  </select>
                </div>
              </div>
              <div className="flex justify-end gap-2">
                <button onClick={() => setShowTaskForm(false)} className="btn-secondary">{t('common:cancel')}</button>
                <button onClick={() => createTaskMutation.mutate()}
                  disabled={createTaskMutation.isPending || !taskForm.task_name || !taskForm.planned_start || !taskForm.planned_end}
                  className="btn-primary">{t('common:save')}</button>
              </div>
            </div>
          )}

          {isLoadingGroup ? (
            <Spinner size="lg" />
          ) : isEmptyGroup ? (
            <EmptyState title={t('project:milestone_emptyTitle')} />
          ) : (
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>{t('project:plan_colName')}</th>
                    <th>{t('project:plan_colPeriod')}</th>
                    <th>{t('project:plan_colWeight')}</th>
                    <th>{t('common:status')}</th>
                    <th className="right">{t('project:task_colProgress')}</th>
                    <th>{t('project:task_colCritical')}</th>
                    <th>{t('common:action')}</th>
                  </tr>
                </thead>
                <tbody>
                  {milestones.map((ms: any) => (
                    <Fragment key={ms.id}>
                      <tr className="bg-gray-50">
                        <td className="text-sm font-semibold text-gray-900">{ms.milestone_name}</td>
                        <td className="text-xs text-gray-500">
                          {ms.target_start_date ? `${formatDate(ms.target_start_date)} – ` : ''}{formatDate(ms.target_date)}
                        </td>
                        <td className="text-xs text-gray-500">
                          {Number(ms.total_planned_hours) > 0
                            ? t('project:plan_weightTotalHours', { hours: Number(ms.total_planned_hours) })
                            : Number(ms.total_planned_cost) > 0
                              ? t('project:plan_weightTotalCost', { cost: formatRupiah(Number(ms.total_planned_cost)) })
                              : '—'}
                        </td>
                        <td><Badge status={ms.status} /></td>
                        <td className="right text-sm font-medium">{ms.progress_pct ?? 0}%</td>
                        <td />
                        <td>
                          <div className="flex items-center gap-1.5">
                            {ms.status === 'pending' && (
                              <>
                                <button onClick={() => updateMsMutation.mutate({ id: ms.id, status: 'achieved', progress_pct: 100 })}
                                  className="text-xs px-2 py-1 rounded-md bg-green-50 text-green-700 hover:bg-green-100">
                                  {t('project:milestone_markAchieved')}
                                </button>
                                <button onClick={() => updateMsMutation.mutate({ id: ms.id, status: 'missed' })}
                                  className="text-xs px-2 py-1 rounded-md bg-red-50 text-red-700 hover:bg-red-100">
                                  {t('project:milestone_markMissed')}
                                </button>
                              </>
                            )}
                            {isSuperAdmin && (
                              <button onClick={() => openEditMs(ms)} className="text-xs px-2 py-1 rounded-md bg-gray-50 text-gray-700 hover:bg-gray-100 inline-flex items-center gap-1">
                                <Pencil className="h-3 w-3" /> {t('common:edit')}
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                      {(tasksByMilestone[ms.id] ?? []).map((tk: any) => (
                        <tr key={tk.id}>
                          <td className="text-sm text-gray-700 pl-6">↳ {tk.task_name}</td>
                          <td className="text-xs text-gray-400">{formatDate(tk.planned_start)} – {formatDate(tk.planned_end)} ({tk.duration_days}d)</td>
                          <td className="text-xs text-gray-500">
                            {Number(tk.planned_hours) > 0
                              ? t('project:plan_weightHours', { hours: Number(tk.planned_hours) })
                              : Number(tk.planned_cost) > 0
                                ? formatRupiah(Number(tk.planned_cost))
                                : '—'}
                          </td>
                          <td><Badge status={tk.status} /></td>
                          <td className="right text-sm">{tk.progress_pct ?? 0}%</td>
                          <td>{tk.is_critical && <Badge status="critical" label={t('common:critical')} />}</td>
                          <td>
                            {isSuperAdmin && (
                              <button onClick={() => openEditTask(tk)} className="text-xs px-2 py-1 rounded-md bg-gray-50 text-gray-700 hover:bg-gray-100 inline-flex items-center gap-1">
                                <Pencil className="h-3 w-3" /> {t('common:edit')}
                              </button>
                            )}
                          </td>
                        </tr>
                      ))}
                    </Fragment>
                  ))}
                  {unassignedTasks.length > 0 && (
                    <>
                      <tr className="bg-gray-50">
                        <td colSpan={7} className="text-xs text-gray-400 italic">{t('project:plan_noMilestoneGroup')}</td>
                      </tr>
                      {unassignedTasks.map((tk: any) => (
                        <tr key={tk.id}>
                          <td className="text-sm text-gray-700 pl-6">↳ {tk.task_name}</td>
                          <td className="text-xs text-gray-400">{formatDate(tk.planned_start)} – {formatDate(tk.planned_end)} ({tk.duration_days}d)</td>
                          <td className="text-xs text-gray-500">
                            {Number(tk.planned_hours) > 0
                              ? t('project:plan_weightHours', { hours: Number(tk.planned_hours) })
                              : Number(tk.planned_cost) > 0
                                ? formatRupiah(Number(tk.planned_cost))
                                : '—'}
                          </td>
                          <td><Badge status={tk.status} /></td>
                          <td className="right text-sm">{tk.progress_pct ?? 0}%</td>
                          <td>{tk.is_critical && <Badge status="critical" label={t('common:critical')} />}</td>
                          <td>
                            {isSuperAdmin && (
                              <button onClick={() => openEditTask(tk)} className="text-xs px-2 py-1 rounded-md bg-gray-50 text-gray-700 hover:bg-gray-100 inline-flex items-center gap-1">
                                <Pencil className="h-3 w-3" /> {t('common:edit')}
                              </button>
                            )}
                          </td>
                        </tr>
                      ))}
                    </>
                  )}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </Card>

      <Card noPad>
        <CardHeader title={t('project:actual_title')} subtitle={t('project:actual_subtitle')} />
        <div className="card-body space-y-4">
          {isLoadingGroup ? (
            <Spinner size="lg" />
          ) : isEmptyGroup ? (
            <EmptyState title={t('project:milestone_emptyTitle')} />
          ) : (
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>{t('project:plan_colName')}</th>
                    <th>{t('project:milestone_colProgressStatus')}</th>
                    <th className="right">{t('project:milestone_colProgress')}</th>
                    <th>{t('project:milestone_colActualDate')}</th>
                    <th>{t('project:milestone_colIssue')}</th>
                    <th>{t('common:action')}</th>
                  </tr>
                </thead>
                <tbody>
                  {milestones.map((ms: any) => (
                    <Fragment key={ms.id}>
                      <tr className="bg-gray-50">
                        <td className="text-sm font-semibold text-gray-900">{ms.milestone_name}</td>
                        <td>
                          <select
                            value={ms.progress_status ?? 'not_started'}
                            onChange={(e) => updateMsProgressStatusMutation.mutate({ id: ms.id, progress_status: e.target.value })}
                            className="form-select text-xs py-1"
                          >
                            {['not_started', 'in_progress', 'completed'].map((s) => (
                              <option key={s} value={s}>{t(`project:progressStatus_${s}`)}</option>
                            ))}
                          </select>
                        </td>
                        <td className="right">
                          {(ms.task_count ?? 0) > 0 ? (
                            <span className="text-sm text-gray-700" title={t('project:milestone_progressAutoHint', { count: ms.task_count })}>
                              {ms.progress_pct ?? 0}% <span className="text-xs text-gray-400">({t('project:milestone_progressAutoLabel')})</span>
                            </span>
                          ) : (
                            <button
                              onClick={() => {
                                const v = window.prompt(t('project:milestone_updateProgressPrompt'), String(ms.progress_pct ?? 0))
                                if (v === null) return
                                const pct = Math.max(0, Math.min(100, parseInt(v, 10) || 0))
                                updateMsProgressMutation.mutate({ id: ms.id, progress_pct: pct })
                              }}
                              className="text-sm text-primary-700 hover:underline"
                            >
                              {ms.progress_pct ?? 0}%
                            </button>
                          )}
                        </td>
                        <td className="text-xs text-gray-400">{ms.actual_date ? formatDate(ms.actual_date) : '—'}</td>
                        <td>
                          <input
                            value={issueDraft[ms.id] ?? ms.issue_notes ?? ''}
                            onChange={(e) => setIssueDraft({ ...issueDraft, [ms.id]: e.target.value })}
                            placeholder={t('project:milestone_issuePlaceholder')}
                            className="form-input text-xs py-1 w-48"
                          />
                        </td>
                        <td>
                          <div className="flex items-center gap-1.5">
                            <button
                              onClick={() => updateMsIssueMutation.mutate({ id: ms.id, issue_notes: issueDraft[ms.id] ?? ms.issue_notes ?? '' })}
                              className="text-xs px-2 py-1 rounded-md bg-primary-50 text-primary-700 hover:bg-primary-100"
                            >
                              {t('common:save')}
                            </button>
                            <button
                              onClick={() => setAttachTarget({ refType: 'project_milestone', refId: ms.id, title: t('project:milestone_attachModalTitle', { name: ms.milestone_name }) })}
                              disabled={(ms.task_count ?? 0) > 0 && (ms.progress_pct ?? 0) < 100}
                              title={(ms.task_count ?? 0) > 0 && (ms.progress_pct ?? 0) < 100 ? t('project:milestone_attachLockedHint') : undefined}
                              className="text-xs px-2 py-1 rounded-md bg-gray-50 text-gray-700 hover:bg-gray-100 inline-flex items-center gap-1 disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-gray-50"
                            >
                              <Paperclip className="h-3 w-3" /> {t('project:milestone_attachBtn')}
                            </button>
                          </div>
                        </td>
                      </tr>
                      {(tasksByMilestone[ms.id] ?? []).map((tk: any) => (
                        <tr key={tk.id}>
                          <td className="text-sm text-gray-700 pl-6">↳ {tk.task_name}</td>
                          <td />
                          <td className="right text-sm">{tk.progress_pct ?? 0}%</td>
                          <td className="text-xs text-gray-400">{tk.actual_end ? formatDate(tk.actual_end) : '—'}</td>
                          <td />
                          <td>
                            <div className="flex items-center gap-1.5">
                              <button onClick={() => openActualForm(tk)} className="text-xs px-2 py-1 rounded-md bg-primary-50 text-primary-700 hover:bg-primary-100 inline-flex items-center gap-1">
                                <Pencil className="h-3 w-3" /> {t('project:task_updateActualBtn')}
                              </button>
                              <button onClick={() => setAttachTarget({ refType: 'project_task', refId: tk.id, title: t('project:task_attachModalTitle', { name: tk.task_name }) })}
                                className="text-xs px-2 py-1 rounded-md bg-gray-50 text-gray-700 hover:bg-gray-100 inline-flex items-center gap-1">
                                <Paperclip className="h-3 w-3" /> {t('project:task_attachBtn')}
                              </button>
                            </div>
                          </td>
                        </tr>
                      ))}
                    </Fragment>
                  ))}
                  {unassignedTasks.length > 0 && (
                    <>
                      <tr className="bg-gray-50">
                        <td colSpan={6} className="text-xs text-gray-400 italic">{t('project:plan_noMilestoneGroup')}</td>
                      </tr>
                      {unassignedTasks.map((tk: any) => (
                        <tr key={tk.id}>
                          <td className="text-sm text-gray-700 pl-6">↳ {tk.task_name}</td>
                          <td />
                          <td className="right text-sm">{tk.progress_pct ?? 0}%</td>
                          <td className="text-xs text-gray-400">{tk.actual_end ? formatDate(tk.actual_end) : '—'}</td>
                          <td />
                          <td>
                            <div className="flex items-center gap-1.5">
                              <button onClick={() => openActualForm(tk)} className="text-xs px-2 py-1 rounded-md bg-primary-50 text-primary-700 hover:bg-primary-100 inline-flex items-center gap-1">
                                <Pencil className="h-3 w-3" /> {t('project:task_updateActualBtn')}
                              </button>
                              <button onClick={() => setAttachTarget({ refType: 'project_task', refId: tk.id, title: t('project:task_attachModalTitle', { name: tk.task_name }) })}
                                className="text-xs px-2 py-1 rounded-md bg-gray-50 text-gray-700 hover:bg-gray-100 inline-flex items-center gap-1">
                                <Paperclip className="h-3 w-3" /> {t('project:task_attachBtn')}
                              </button>
                            </div>
                          </td>
                        </tr>
                      ))}
                    </>
                  )}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </Card>

      {actualTask && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-xl w-full max-w-lg p-5 space-y-4">
            <p className="font-semibold text-gray-900">{t('project:task_actualModalTitle', { name: actualTask.task_name })}</p>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="form-label">{t('project:task_actualStartLabel')}</label>
                <input type="date" value={actualForm.actual_start ?? ''} onChange={(e) => setActualForm({ ...actualForm, actual_start: e.target.value })} className="form-input" />
              </div>
              <div>
                <label className="form-label">{t('project:task_actualEndLabel')}</label>
                <input type="date" value={actualForm.actual_end ?? ''} onChange={(e) => setActualForm({ ...actualForm, actual_end: e.target.value })} className="form-input" />
              </div>
              <div>
                <label className="form-label">{t('project:task_actualHoursLabel')}</label>
                <input type="number" value={actualForm.actual_hours} onChange={(e) => setActualForm({ ...actualForm, actual_hours: e.target.value })} className="form-input" />
              </div>
              <div>
                <label className="form-label">{t('project:task_actualCostLabel')}</label>
                <input type="number" value={actualForm.actual_cost} onChange={(e) => setActualForm({ ...actualForm, actual_cost: e.target.value })} className="form-input" />
              </div>
              <div>
                <label className="form-label">{t('project:task_colProgress')}</label>
                <input type="number" min={0} max={100} value={actualForm.progress_pct} onChange={(e) => setActualForm({ ...actualForm, progress_pct: e.target.value })} className="form-input" />
              </div>
              <div>
                <label className="form-label">{t('project:task_statusLabel')}</label>
                <select value={actualForm.status} onChange={(e) => setActualForm({ ...actualForm, status: e.target.value })} className="form-select">
                  {['not_started', 'in_progress', 'completed', 'blocked', 'on_hold', 'cancelled'].map((s) => (
                    <option key={s} value={s}>{t(`project:status_${s}`)}</option>
                  ))}
                </select>
              </div>
            </div>
            <div className="flex justify-end gap-2">
              <button onClick={() => setActualTask(null)} className="btn-secondary">{t('common:cancel')}</button>
              <button onClick={() => updateActualMutation.mutate()} disabled={updateActualMutation.isPending} className="btn-primary">{t('common:save')}</button>
            </div>
          </div>
        </div>
      )}

      {editMs && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-xl w-full max-w-lg p-5 space-y-4">
            <p className="font-semibold text-gray-900">{t('project:plan_editMsModalTitle', { name: editMs.milestone_name })}</p>
            <div className="grid grid-cols-2 gap-3">
              <div className="col-span-2">
                <label className="form-label">{t('project:milestone_nameLabel')}</label>
                <input value={editMsForm.milestone_name} onChange={(e) => setEditMsForm({ ...editMsForm, milestone_name: e.target.value })} className="form-input" />
              </div>
              <div>
                <label className="form-label">{t('project:milestone_targetStartLabel')}</label>
                <input type="date" value={editMsForm.target_start_date} onChange={(e) => setEditMsForm({ ...editMsForm, target_start_date: e.target.value })} className="form-input" />
              </div>
              <div>
                <label className="form-label">{t('project:milestone_targetDateLabel')}</label>
                <input type="date" value={editMsForm.target_date} onChange={(e) => setEditMsForm({ ...editMsForm, target_date: e.target.value })} className="form-input" />
              </div>
            </div>
            <div className="flex justify-end gap-2">
              <button onClick={() => setEditMs(null)} className="btn-secondary">{t('common:cancel')}</button>
              <button onClick={() => updateMsPlanMutation.mutate()}
                disabled={updateMsPlanMutation.isPending || !editMsForm.milestone_name || !editMsForm.target_date}
                className="btn-primary">{t('common:save')}</button>
            </div>
          </div>
        </div>
      )}

      {editTask && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-xl w-full max-w-lg p-5 space-y-4">
            <p className="font-semibold text-gray-900">{t('project:plan_editTaskModalTitle', { name: editTask.task_name })}</p>
            <div className="grid grid-cols-2 gap-3">
              <div className="col-span-2">
                <label className="form-label">{t('project:task_nameLabel')}</label>
                <input value={editTaskForm.task_name} onChange={(e) => setEditTaskForm({ ...editTaskForm, task_name: e.target.value })} className="form-input" />
              </div>
              <div>
                <label className="form-label">{t('project:task_startLabel')}</label>
                <input type="date" value={editTaskForm.planned_start} onChange={(e) => setEditTaskForm({ ...editTaskForm, planned_start: e.target.value })} className="form-input" />
              </div>
              <div>
                <label className="form-label">{t('project:task_endLabel')}</label>
                <input type="date" value={editTaskForm.planned_end} onChange={(e) => setEditTaskForm({ ...editTaskForm, planned_end: e.target.value })} className="form-input" />
              </div>
              <div>
                <label className="form-label">{t('project:task_costLabel')}</label>
                <input type="number" value={editTaskForm.planned_cost} onChange={(e) => setEditTaskForm({ ...editTaskForm, planned_cost: e.target.value })} className="form-input" />
              </div>
              <div>
                <label className="form-label">{t('project:task_milestoneLabel')}</label>
                <select value={editTaskForm.milestone_id} onChange={(e) => setEditTaskForm({ ...editTaskForm, milestone_id: e.target.value })} className="form-select">
                  <option value="">{t('project:task_milestoneNone')}</option>
                  {milestones.map((ms: any) => <option key={ms.id} value={ms.id}>{ms.milestone_name}</option>)}
                </select>
              </div>
            </div>
            <div className="flex justify-end gap-2">
              <button onClick={() => setEditTask(null)} className="btn-secondary">{t('common:cancel')}</button>
              <button onClick={() => updateTaskPlanMutation.mutate()}
                disabled={updateTaskPlanMutation.isPending || !editTaskForm.task_name || !editTaskForm.planned_start || !editTaskForm.planned_end}
                className="btn-primary">{t('common:save')}</button>
            </div>
          </div>
        </div>
      )}

      {attachTarget && (
        <AttachmentPanel
          refType={attachTarget.refType}
          refId={attachTarget.refId}
          entityId={project.entity_id}
          title={attachTarget.title}
          onClose={() => setAttachTarget(null)}
        />
      )}
    </div>
  )
}

function BudgetTab({ projectId }: { projectId: string }) {
  const { t } = useTranslation(['project', 'common'])
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({ cost_type: 'direct_labor', description: '', quantity: '1', unit_price: '' })

  const { data: summary, isLoading: summaryLoading, refetch: refetchSummary } = useQuery({
    queryKey: ['project-budget-summary', projectId],
    queryFn: () => api.get(`/projects/${projectId}/budget`).then((r) => r.data),
  })

  const { data: linesData, isLoading: linesLoading, refetch: refetchLines } = useQuery({
    queryKey: ['project-budget-lines', projectId],
    queryFn: () => api.get(`/projects/${projectId}/budget/lines`).then((r) => r.data),
  })
  const lines: any[] = Array.isArray(linesData) ? linesData : []

  const createMutation = useMutation({
    mutationFn: () => api.post(`/projects/${projectId}/budget`, {
      cost_type: form.cost_type,
      description: form.description,
      quantity: parseFloat(form.quantity) || 1,
      unit_price: parseFloat(form.unit_price) || 0,
    }),
    onSuccess: () => {
      showToast(t('project:budget_createSuccess'))
      setShowForm(false)
      setForm({ cost_type: 'direct_labor', description: '', quantity: '1', unit_price: '' })
      refetchSummary(); refetchLines()
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('project:budget_createFailed'), 'error'),
  })

  const updateActualMutation = useMutation({
    mutationFn: ({ id, actual_amount }: { id: string; actual_amount: number }) =>
      api.put(`/projects/${projectId}/budget/${id}/actual`, null, { params: { actual_amount } }),
    onSuccess: () => { showToast(t('project:budget_updateActualSuccess')); refetchSummary(); refetchLines() },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('common:failed'), 'error'),
  })

  const costTypes = ['direct_labor', 'direct_material', 'software', 'hardware', 'travel', 'subcontractor', 'indirect', 'contingency', 'other']
  const summaryLines: any[] = summary?.lines ?? []

  return (
    <div className="space-y-6">
      <Card noPad>
        <CardHeader title={t('project:budget_summaryTitle')}
          actions={<button onClick={() => { refetchSummary(); refetchLines() }} className="btn-secondary"><RefreshCw className="h-4 w-4" /></button>} />
        {summaryLoading ? (
          <div className="flex justify-center py-10"><Spinner size="lg" /></div>
        ) : summaryLines.length === 0 ? (
          <EmptyState title={t('project:budget_emptyTitle')} />
        ) : (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>{t('project:budget_colCostType')}</th>
                  <th className="right">{t('project:budget_colPlanned')}</th>
                  <th className="right">{t('project:budget_colActual')}</th>
                  <th className="right">{t('project:budget_colVariance')}</th>
                  <th className="right">{t('project:budget_colBurn')}</th>
                </tr>
              </thead>
              <tbody>
                {summaryLines.map((l: any) => (
                  <tr key={l.cost_type}>
                    <td className="text-sm">{t(`project:costType_${l.cost_type}`)}</td>
                    <td className="right text-sm">Rp {formatRupiah(l.planned)}</td>
                    <td className="right text-sm">Rp {formatRupiah(l.actual)}</td>
                    <td className="right text-sm">Rp {formatRupiah(l.variance)}</td>
                    <td className="right text-sm">{l.burn_pct ?? 0}%</td>
                  </tr>
                ))}
                <tr className="font-semibold border-t border-gray-200">
                  <td className="text-sm">{t('common:total')}</td>
                  <td className="right text-sm">Rp {formatRupiah(summary?.total_planned)}</td>
                  <td className="right text-sm">Rp {formatRupiah(summary?.total_actual)}</td>
                  <td className="right text-sm">Rp {formatRupiah(summary?.total_variance)}</td>
                  <td className="right text-sm">{summary?.burn_pct ?? 0}%</td>
                </tr>
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card noPad>
        <CardHeader title={t('project:budget_linesTitle')}
          actions={<button onClick={() => setShowForm((s) => !s)} className="btn-primary"><Plus className="h-4 w-4" /> {t('project:budget_newBtn')}</button>} />
        <div className="card-body space-y-4">
          {showForm && (
            <div className="border border-gray-100 rounded-lg p-4 space-y-3">
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <div>
                  <label className="form-label">{t('project:budget_costTypeLabel')}</label>
                  <select value={form.cost_type} onChange={(e) => setForm({ ...form, cost_type: e.target.value })} className="form-select">
                    {costTypes.map((c) => <option key={c} value={c}>{t(`project:costType_${c}`)}</option>)}
                  </select>
                </div>
                <div className="md:col-span-2">
                  <label className="form-label">{t('project:budget_descLabel')}</label>
                  <input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} className="form-input" />
                </div>
                <div>
                  <label className="form-label">{t('project:budget_qtyLabel')}</label>
                  <input type="number" value={form.quantity} onChange={(e) => setForm({ ...form, quantity: e.target.value })} className="form-input" />
                </div>
                <div>
                  <label className="form-label">{t('project:budget_unitPriceLabel')}</label>
                  <input type="number" value={form.unit_price} onChange={(e) => setForm({ ...form, unit_price: e.target.value })} className="form-input" />
                </div>
              </div>
              <div className="flex justify-end gap-2">
                <button onClick={() => setShowForm(false)} className="btn-secondary">{t('common:cancel')}</button>
                <button onClick={() => createMutation.mutate()} disabled={createMutation.isPending || !form.description} className="btn-primary">
                  {t('common:save')}
                </button>
              </div>
            </div>
          )}
          {linesLoading ? (
            <Spinner size="lg" />
          ) : lines.length === 0 ? (
            <EmptyState title={t('project:budget_emptyTitle')} />
          ) : (
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>{t('project:budget_costTypeLabel')}</th>
                    <th>{t('project:budget_descLabel')}</th>
                    <th className="right">{t('project:budget_colPlanned')}</th>
                    <th className="right">{t('project:budget_colActual')}</th>
                  </tr>
                </thead>
                <tbody>
                  {lines.map((l: any) => (
                    <tr key={l.id}>
                      <td className="text-sm">{t(`project:costType_${l.cost_type}`)}</td>
                      <td className="text-sm text-gray-500">{l.description}</td>
                      <td className="right text-sm">Rp {formatRupiah(l.planned_amount)}</td>
                      <td className="right text-sm">
                        <button
                          onClick={() => {
                            const v = window.prompt(t('project:budget_updateActualPrompt'), String(l.actual_amount ?? 0))
                            if (v === null) return
                            updateActualMutation.mutate({ id: l.id, actual_amount: parseFloat(v) || 0 })
                          }}
                          className="text-primary-700 hover:underline"
                        >
                          Rp {formatRupiah(l.actual_amount)}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </Card>
    </div>
  )
}

function FinanceTab({ project }: { project: any }) {
  const { t } = useTranslation(['project', 'common'])
  const [year, setYear] = useState(String(currentYear()))
  const ccCode: string | null = project?.cost_center_code ?? null
  const [invoiceDate, setInvoiceDate] = useState(new Date().toISOString().slice(0, 10))
  const [genResult, setGenResult] = useState<any>(null)

  const generateInvoiceMutation = useMutation({
    mutationFn: () => api.post('/costing/timesheets/generate-invoice', {
      project_id: project.id, invoice_date: invoiceDate,
    }),
    onSuccess: (r) => {
      setGenResult(r.data)
      if (r.data.status === 'created') showToast(t('project:finance_genInvoiceSuccess', { no: r.data.invoice_no }))
      else showToast(r.data.reason, 'error')
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('project:finance_genInvoiceFailed'), 'error'),
  })

  const { data: defRevData, isLoading: defRevLoading, refetch: refetchDefRev } = useQuery({
    queryKey: ['deferred-revenue-summary', project?.id],
    queryFn: () => api.get(`/deferred-revenue/projects/${project.id}/summary`).then((r) => r.data),
    enabled: !!project?.id,
  })
  const defRevRows: any[] = Array.isArray(defRevData) ? defRevData : []

  const [payMs, setPayMs] = useState<any | null>(null)
  const [payForm, setPayForm] = useState({ amount: '', payment_date: new Date().toISOString().slice(0, 10) })

  const recordPaymentMutation = useMutation({
    mutationFn: () => api.post(`/deferred-revenue/milestones/${payMs.milestone_id}/payment`, {
      amount: Number(payForm.amount), payment_date: payForm.payment_date,
    }),
    onSuccess: (r) => {
      showToast(t('project:finance_drPaymentSuccess', { no: r.data.journal_no }))
      setPayMs(null)
      refetchDefRev()
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('project:finance_drPaymentFailed'), 'error'),
  })

  const recognizeMutation = useMutation({
    mutationFn: (milestoneId: string) => api.post(`/deferred-revenue/milestones/${milestoneId}/recognize`, {}),
    onSuccess: (r) => {
      const d = r.data
      if (d.status === 'skipped') showToast(t('project:finance_drRecognizeSkipped', { reason: d.reason }), 'error')
      else if (d.shortfall > 0) showToast(t('project:finance_drRecognizeShortfall', { amount: formatRupiah(d.released), shortfall: formatRupiah(d.shortfall) }))
      else showToast(t('project:finance_drRecognizeSuccess', { amount: formatRupiah(d.released) }))
      refetchDefRev()
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('project:finance_drRecognizeFailed'), 'error'),
  })

  const { data: utilData, isLoading: utilLoading } = useQuery({
    queryKey: ['budget-utilization', project?.entity_id, ccCode, year],
    queryFn: () => api.get('/budget/utilization', { params: { entity_id: project.entity_id, year, cost_center: ccCode } }).then((r) => r.data),
    enabled: !!project?.entity_id && !!ccCode,
  })
  const utilRows: any[] = Array.isArray(utilData) ? utilData : []

  const { data: commitData, isLoading: commitLoading } = useQuery({
    queryKey: ['budget-commitment', project?.entity_id, ccCode, year],
    queryFn: () => api.get('/budget/commitment', { params: { entity_id: project.entity_id, year, cost_center: ccCode, status: 'active' } }).then((r) => r.data),
    enabled: !!project?.entity_id && !!ccCode,
  })
  const commitRows: any[] = Array.isArray(commitData) ? commitData : []

  if (!ccCode) {
    return <EmptyState title={t('project:finance_utilEmptyTitle')} />
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <span className="text-sm text-gray-500">{t('project:finance_costCenterLabel')}:</span>
        <span className="text-sm font-medium text-gray-800">{ccCode}</span>
        <div className="ml-auto flex items-center gap-2">
          <label className="text-xs text-gray-500">{t('project:finance_yearLabel')}</label>
          <input type="number" value={year} onChange={(e) => setYear(e.target.value)} className="form-input w-24 text-xs py-1" />
        </div>
      </div>

      <Card>
        <p className="text-sm font-semibold text-gray-800 mb-1">{t('project:finance_genInvoiceTitle')}</p>
        <p className="text-xs text-gray-500 mb-3">{t('project:finance_genInvoiceHint')}</p>
        <div className="flex items-end gap-3">
          <div>
            <label className="form-label">{t('project:finance_genInvoiceDateLabel')}</label>
            <input type="date" value={invoiceDate} onChange={(e) => setInvoiceDate(e.target.value)} className="form-input" />
          </div>
          <button onClick={() => generateInvoiceMutation.mutate()} disabled={generateInvoiceMutation.isPending} className="btn-primary">
            {t('project:finance_genInvoiceBtn')}
          </button>
        </div>
        {genResult && (
          <div className={`mt-3 text-sm rounded-md p-3 ${genResult.status === 'created' ? 'bg-green-50 text-green-800' : 'bg-amber-50 text-amber-800'}`}>
            {genResult.status === 'created' ? (
              <>
                <p className="font-medium">{t('project:finance_genInvoiceSuccess', { no: genResult.invoice_no })}</p>
                <p>{t('project:finance_genInvoiceSubtotal')}: Rp {formatRupiah(genResult.subtotal)} + PPN Rp {formatRupiah(genResult.ppn_amount)} = Rp {formatRupiah(genResult.total_amount)}</p>
                <p>{t('project:finance_genInvoiceLines', { count: genResult.line_count, ts: genResult.timesheets_billed })}</p>
              </>
            ) : (
              <p>{genResult.reason}{genResult.unrated_employees?.length > 0 && ` — ${genResult.unrated_employees.join(', ')}`}</p>
            )}
          </div>
        )}
      </Card>

      <Card noPad>
        <CardHeader title={t('project:finance_drTitle')} subtitle={t('project:finance_drHint')} />
        {defRevLoading ? (
          <div className="flex justify-center py-10"><Spinner size="lg" /></div>
        ) : defRevRows.length === 0 ? (
          <EmptyState title={t('project:finance_drEmptyTitle')} />
        ) : (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>{t('project:finance_drColMilestone')}</th>
                  <th className="right">{t('project:finance_drColBillingAmount')}</th>
                  <th className="right">{t('project:finance_drColProgress')}</th>
                  <th className="right">{t('project:finance_drColPaid')}</th>
                  <th className="right">{t('project:finance_drColRecognized')}</th>
                  <th className="right">{t('project:finance_drColBalance')}</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {defRevRows.map((r: any) => (
                  <tr key={r.milestone_id}>
                    <td className="text-sm font-medium">{r.milestone_name}</td>
                    <td className="right text-sm">Rp {formatRupiah(r.billing_amount)}</td>
                    <td className="right text-sm">{r.progress_pct}%</td>
                    <td className="right text-sm">Rp {formatRupiah(r.total_paid)}</td>
                    <td className="right text-sm">Rp {formatRupiah(r.total_recognized)}</td>
                    <td className="right text-sm">Rp {formatRupiah(r.deferred_balance)}</td>
                    <td className="text-right whitespace-nowrap">
                      <button onClick={() => { setPayMs(r); setPayForm({ amount: '', payment_date: new Date().toISOString().slice(0, 10) }) }} className="btn-secondary text-xs py-1 mr-2">
                        {t('project:finance_drRecordPaymentBtn')}
                      </button>
                      <button onClick={() => recognizeMutation.mutate(r.milestone_id)} disabled={recognizeMutation.isPending} className="btn-primary text-xs py-1">
                        {t('project:finance_drRecognizeBtn')}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card noPad>
        <CardHeader title={t('project:finance_utilTitle')} subtitle={t('project:finance_utilHint')} />
        {utilLoading ? (
          <div className="flex justify-center py-10"><Spinner size="lg" /></div>
        ) : utilRows.length === 0 ? (
          <EmptyState title={t('project:finance_utilEmptyTitle')} />
        ) : (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>{t('project:finance_colAccount')}</th>
                  <th>{t('project:finance_colMonth')}</th>
                  <th className="right">{t('project:finance_colBudgeted')}</th>
                  <th className="right">{t('project:finance_colActual')}</th>
                  <th className="right">{t('project:finance_colCommitted')}</th>
                  <th className="right">{t('project:finance_colAvailable')}</th>
                </tr>
              </thead>
              <tbody>
                {utilRows.map((r: any, idx: number) => (
                  <tr key={idx}>
                    <td className="text-sm">{r.account_code}</td>
                    <td className="text-sm text-gray-500">{r.month}</td>
                    <td className="right text-sm">Rp {formatRupiah(r.budgeted_amount)}</td>
                    <td className="right text-sm">Rp {formatRupiah(r.actual_amount)}</td>
                    <td className="right text-sm">Rp {formatRupiah(r.commitment_amount)}</td>
                    <td className="right text-sm">Rp {formatRupiah(r.available_amount)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card noPad>
        <CardHeader title={t('project:finance_commitTitle')} />
        {commitLoading ? (
          <div className="flex justify-center py-10"><Spinner size="lg" /></div>
        ) : commitRows.length === 0 ? (
          <EmptyState title={t('project:finance_commitEmptyTitle')} />
        ) : (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>{t('project:finance_colSourceType')}</th>
                  <th>{t('project:finance_colSourceRef')}</th>
                  <th>{t('project:finance_colAccount')}</th>
                  <th className="right">{t('project:finance_colCommittedAmount')}</th>
                  <th>{t('common:status')}</th>
                </tr>
              </thead>
              <tbody>
                {commitRows.map((r: any) => (
                  <tr key={r.id}>
                    <td className="text-sm text-gray-500">{r.source_type}</td>
                    <td className="text-sm font-medium">{r.source_ref}</td>
                    <td className="text-sm">{r.account_code}</td>
                    <td className="right text-sm">Rp {formatRupiah(r.net_committed)}</td>
                    <td><Badge status={r.status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {payMs && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-xl w-full max-w-md p-5 space-y-4">
            <p className="font-semibold text-gray-900">{t('project:finance_drPaymentModalTitle', { name: payMs.milestone_name })}</p>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="form-label">{t('project:finance_drAmountLabel')}</label>
                <input type="number" value={payForm.amount} onChange={(e) => setPayForm({ ...payForm, amount: e.target.value })} className="form-input" />
              </div>
              <div>
                <label className="form-label">{t('project:finance_drDateLabel')}</label>
                <input type="date" value={payForm.payment_date} onChange={(e) => setPayForm({ ...payForm, payment_date: e.target.value })} className="form-input" />
              </div>
            </div>
            <div className="flex justify-end gap-2">
              <button onClick={() => setPayMs(null)} className="btn-secondary">{t('common:cancel')}</button>
              <button onClick={() => recordPaymentMutation.mutate()}
                disabled={recordPaymentMutation.isPending || !payForm.amount || Number(payForm.amount) <= 0}
                className="btn-primary">{t('common:save')}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function HealthTab({ projectId }: { projectId: string }) {
  const { t } = useTranslation(['project', 'common'])
  const { data: health, isLoading } = useQuery({
    queryKey: ['project-health', projectId],
    queryFn: () => api.get(`/projects/${projectId}/health`).then((r) => r.data),
  })
  const { data: evm } = useQuery({
    queryKey: ['project-evm', projectId],
    queryFn: () => api.get(`/projects/${projectId}/evm`).then((r) => r.data),
  })
  const { data: mandays } = useQuery({
    queryKey: ['project-mandays', projectId],
    queryFn: () => api.get(`/projects/${projectId}/mandays`).then((r) => r.data),
  })
  const byEmployee: any[] = mandays?.by_employee ?? []
  const burnRate = health?.burn_rate
  const svAlerts: any[] = health?.schedule_variance_alerts ?? []

  if (isLoading || !health) return <div className="flex justify-center py-16"><Spinner size="lg" /></div>

  const ragColor = health.rag_status === 'GREEN' ? 'bg-green-100 text-green-700'
    : health.rag_status === 'AMBER' ? 'bg-amber-100 text-amber-700' : 'bg-red-100 text-red-700'

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Card>
          <p className="text-xs text-gray-500">{t('project:health_rag')}</p>
          <span className={`inline-flex mt-1 px-2.5 py-1 rounded-full text-sm font-semibold ${ragColor}`}>{health.rag_status}</span>
        </Card>
        <Card>
          <p className="text-xs text-gray-500">{t('project:health_completion')}</p>
          <p className="text-xl font-bold text-gray-900 mt-1">{health.completion_pct}%</p>
        </Card>
        <Card>
          <p className="text-xs text-gray-500">{t('project:health_totalTasks')}</p>
          <p className="text-xl font-bold text-gray-900 mt-1">{health.completed_tasks}/{health.total_tasks}</p>
        </Card>
        <Card>
          <p className="text-xs text-gray-500">{t('project:health_criticalTasks')}</p>
          <p className="text-xl font-bold text-gray-900 mt-1">{health.critical_tasks}</p>
        </Card>
      </div>

      <Card>
        <p className="text-sm font-semibold text-gray-800 mb-3">{t('project:health_budgetSection')}</p>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
          <div><p className="text-xs text-gray-500">{t('project:health_bac')}</p><p className="font-medium">Rp {formatRupiah(evm?.bac)}</p></div>
          <div><p className="text-xs text-gray-500">{t('project:health_pv')}</p><p className="font-medium">Rp {formatRupiah(evm?.pv)}</p></div>
          <div><p className="text-xs text-gray-500">{t('project:health_ev')}</p><p className="font-medium">Rp {formatRupiah(evm?.ev)}</p></div>
          <div><p className="text-xs text-gray-500">{t('project:health_eac')}</p><p className="font-medium">Rp {formatRupiah(evm?.eac)}</p></div>
          <div><p className="text-xs text-gray-500">{t('project:health_vac')}</p><p className="font-medium">Rp {formatRupiah(evm?.vac)}</p></div>
          <div><p className="text-xs text-gray-500">{t('project:health_spi')}</p><p className="font-medium">{evm?.spi ?? '—'}</p></div>
          <div><p className="text-xs text-gray-500">{t('project:health_cpi')}</p><p className="font-medium">{evm?.cpi ?? '—'}</p></div>
        </div>
      </Card>

      {burnRate && (
        <Card>
          <p className="text-sm font-semibold text-gray-800 mb-1">{t('project:health_costBurnSection')}</p>
          <p className="text-xs text-gray-500 mb-3">{t('project:health_costBurnHint')}</p>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
            <div><p className="text-xs text-gray-500">{t('project:health_bac')}</p><p className="font-medium">Rp {formatRupiah(burnRate.bac)}</p></div>
            <div><p className="text-xs text-gray-500">{t('project:health_costBurned')}</p><p className="font-medium">Rp {formatRupiah(burnRate.burned_cost)}</p></div>
            <div>
              <p className="text-xs text-gray-500">{t('project:health_costBurnPct')}</p>
              <p className={`font-medium ${(burnRate.burn_pct ?? 0) > 100 ? 'text-red-600' : ''}`}>{burnRate.burn_pct ?? '—'}%</p>
            </div>
            <div>
              <p className="text-xs text-gray-500">{t('project:health_ratedHours')}</p>
              <p className="font-medium">
                {burnRate.rated_hours}
                {burnRate.unrated_hours > 0 && (
                  <span className="text-xs text-amber-600 ml-1" title={t('project:health_unratedHoursHint')}>
                    (+{burnRate.unrated_hours} {t('project:health_unratedHours')})
                  </span>
                )}
              </p>
            </div>
          </div>
        </Card>
      )}

      {svAlerts.length > 0 && (
        <Card noPad>
          <CardHeader title={t('project:health_scheduleVarianceSection')} subtitle={t('project:health_scheduleVarianceHint')} />
          <div className="card-body">
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>{t('project:health_svColTask')}</th>
                    <th className="right">{t('project:health_svColPlanned')}</th>
                    <th className="right">{t('project:health_svColActual')}</th>
                    <th className="right">{t('project:health_svColOverrun')}</th>
                    <th className="right">{t('project:health_svColProgress')}</th>
                  </tr>
                </thead>
                <tbody>
                  {svAlerts.map((a) => (
                    <tr key={a.task_id}>
                      <td className="text-sm">{a.task_code} — {a.task_name}</td>
                      <td className="right text-sm">{a.planned_hours}j</td>
                      <td className="right text-sm">{a.actual_hours}j</td>
                      <td className="right text-sm text-red-600 font-medium">+{a.overrun_hours}j</td>
                      <td className="right text-sm">{a.progress_pct}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </Card>
      )}

      <Card>
        <p className="text-sm font-semibold text-gray-800 mb-3">{t('project:health_riskSection')}</p>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
          <div><p className="text-xs text-gray-500">{t('project:health_totalRisks')}</p><p className="font-medium">{health.risks?.total ?? 0}</p></div>
          <div><p className="text-xs text-gray-500">{t('project:health_financialExposure')}</p><p className="font-medium">Rp {formatRupiah(health.risks?.financial_exposure)}</p></div>
          <div><p className="text-xs text-gray-500">{t('project:health_pendingMilestones')}</p><p className="font-medium">{health.pending_milestones}</p></div>
          <div>
            <p className="text-xs text-gray-500">{t('project:health_overdueMilestones')}</p>
            <p className={`font-medium ${(health.overdue_milestones ?? 0) > 0 ? 'text-red-600' : ''}`}>{health.overdue_milestones ?? 0}</p>
          </div>
        </div>
      </Card>

      {mandays && (
        <Card noPad>
          <CardHeader title={t('project:health_mandaysSection')} subtitle={t('project:health_mandaysHint')} />
          <div className="card-body space-y-4">
            <div className="grid grid-cols-3 gap-4 text-sm">
              <div><p className="text-xs text-gray-500">{t('project:health_plannedMandays')}</p><p className="font-medium">{mandays.planned_mandays}</p></div>
              <div><p className="text-xs text-gray-500">{t('project:health_actualMandays')}</p><p className="font-medium">{mandays.actual_mandays}</p></div>
              <div><p className="text-xs text-gray-500">{t('project:health_burnPct')}</p><p className="font-medium">{mandays.burn_pct}%</p></div>
            </div>
            {byEmployee.length > 0 && (
              <div>
                <p className="text-xs font-semibold text-gray-500 mb-2">{t('project:health_byEmployee')}</p>
                <div className="overflow-x-auto">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>{t('project:health_colEmployee')}</th>
                        <th className="right">{t('project:health_colActualHours')}</th>
                        <th className="right">{t('project:health_colActualMandays')}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {byEmployee.map((e: any) => (
                        <tr key={e.employee_id}>
                          <td className="text-sm">{e.full_name}</td>
                          <td className="right text-sm">{e.actual_hours}</td>
                          <td className="right text-sm">{e.actual_mandays}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        </Card>
      )}
    </div>
  )
}

export default function ProjectDetailPage() {
  const { id } = useParams<{ id: string }>()
  const { t } = useTranslation(['project', 'common'])
  const [tab, setTab] = useState<Tab>('overview')

  const { data: project, isLoading, refetch: refetchProject } = useQuery({
    queryKey: ['project-detail', id],
    queryFn: () => api.get(`/projects/${id}`).then((r) => r.data),
    enabled: !!id,
  })

  if (isLoading || !project || !id) {
    return <div className="flex justify-center py-20"><Spinner size="lg" /></div>
  }

  return (
    <div className="space-y-6">
      <Link to="/projects" className="inline-flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-700">
        <ArrowLeft className="h-4 w-4" /> {t('project:backToList')}
      </Link>

      <div className="flex items-center justify-between flex-wrap gap-2">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">{project.project_name}</h1>
          <p className="text-sm text-gray-500 mt-0.5">{project.project_code} · {t(`project:industry_${project.industry_type}`)}</p>
        </div>
        <div className="flex items-center gap-2">
          <Badge status={project.charter_status} />
          {project.completion_pct >= 100 && <CheckCircle2 className="h-5 w-5 text-green-600" />}
        </div>
      </div>

      <div className="flex gap-1 border-b border-gray-200">
        {TABS.map((tb) => (
          <button key={tb} onClick={() => setTab(tb)}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
              tab === tb ? 'border-primary-600 text-primary-700' : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}>
            {t(`project:tab_${tb}`)}
          </button>
        ))}
      </div>

      {tab === 'overview' && <OverviewTab projectId={id} project={project} onRefetchProject={refetchProject} />}
      {tab === 'team' && <TeamTab projectId={id} />}
      {tab === 'tasks' && <TasksTab projectId={id} project={project} />}
      {tab === 'budget' && <BudgetTab projectId={id} />}
      {tab === 'finance' && <FinanceTab project={project} />}
      {tab === 'health' && <HealthTab projectId={id} />}
    </div>
  )
}
