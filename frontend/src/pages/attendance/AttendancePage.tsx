import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { RefreshCw, Plus, CheckCircle, XCircle, Clock, X } from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext'
import api from '../../lib/api'
import { Card, CardHeader } from '../../components/ui/Card'
import Spinner from '../../components/ui/Spinner'
import EmptyState from '../../components/ui/EmptyState'
import Badge from '../../components/ui/Badge'
import { formatDate, currentYear, currentMonth, MONTHS, todayISO, firstDayOfMonth, lastDayOfMonth } from '../../lib/utils'
import { showToast } from '../../components/ui/Toast'

export default function AttendancePage() {
  const { t } = useTranslation(['attendance', 'common'])
  const { entityId } = useAuth()
  const qc = useQueryClient()
  const [tab, setTab] = useState<'timesheet' | 'leave' | 'overtime'>('timesheet')
  const [year, setYear]   = useState(currentYear())
  const [month, setMonth] = useState(currentMonth())

  // ── Timesheet entity summary ─────────────────────────────────────────────────
  const { data: tsData, isLoading: tsLoading, refetch: refetchTs } = useQuery({
    queryKey: ['timesheet-entity', entityId, year, month],
    queryFn: () =>
      api.get(`/attendance/timesheet/entity/${entityId}`, { params: { year, month } }).then(r => r.data),
    enabled: !!entityId && tab === 'timesheet',
  })
  const timesheets: any[] = Array.isArray(tsData) ? tsData : (tsData?.items ?? [])

  // Process period mutation
  const processMutation = useMutation({
    mutationFn: () =>
      api.post('/attendance/process/period', {
        entity_id: entityId, year, month, processed_by: 'user',
      }),
    onSuccess: (r) => {
      showToast(t('attendance:timesheet_processSuccess', { count: r.data?.processed ?? 0 }))
      refetchTs()
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('attendance:timesheet_processFailed'), 'error'),
  })

  // ── Leave requests ───────────────────────────────────────────────────────────
  const [leaveStatus, setLeaveStatus] = useState('')
  const { data: leaveData, isLoading: leaveLoading, refetch: refetchLeave } = useQuery({
    queryKey: ['leave-requests', entityId, leaveStatus],
    queryFn: () =>
      api.get('/attendance/leave', {
        params: { entity_id: entityId, status: leaveStatus || undefined, size: 50 },
      }).then(r => r.data),
    enabled: !!entityId && tab === 'leave',
  })
  const leaves: any[] = Array.isArray(leaveData) ? leaveData : (leaveData?.items ?? [])

  const approveLeaveMutation = useMutation({
    mutationFn: ({ id, approved }: { id: string; approved: boolean }) =>
      api.post(`/attendance/leave/${id}/approve`, {
        approved_by: 'user', status: approved ? 'approved' : 'rejected',
      }),
    onSuccess: () => { showToast(t('attendance:leave_statusUpdated')); refetchLeave() },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('common:failed'), 'error'),
  })

  // New leave form
  const [showLeaveForm, setShowLeaveForm] = useState(false)
  const [leaveForm, setLeaveForm] = useState({
    employee_id: '', leave_type_id: '', start_date: todayISO(),
    end_date: todayISO(), reason: '',
  })
  const createLeaveMutation = useMutation({
    mutationFn: () =>
      api.post('/attendance/leave', {
        entity_id: entityId, ...leaveForm, created_by: 'user',
      }),
    onSuccess: () => {
      showToast(t('attendance:leave_createSuccess'))
      setShowLeaveForm(false)
      setLeaveForm({ employee_id: '', leave_type_id: '', start_date: todayISO(), end_date: todayISO(), reason: '' })
      refetchLeave()
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('common:failed'), 'error'),
  })

  // Leave types
  const { data: leaveTypesData } = useQuery({
    queryKey: ['leave-types', entityId],
    queryFn: () => api.get('/attendance/leave-type', { params: { entity_id: entityId } }).then(r => r.data),
    enabled: !!entityId && tab === 'leave',
  })
  const leaveTypes: any[] = Array.isArray(leaveTypesData) ? leaveTypesData : []

  // ── Overtime ─────────────────────────────────────────────────────────────────
  const [otStatus, setOtStatus] = useState('')
  const { data: otData, isLoading: otLoading, refetch: refetchOt } = useQuery({
    queryKey: ['overtime', entityId, otStatus],
    queryFn: () =>
      api.get('/attendance/overtime', {
        params: { entity_id: entityId, status: otStatus || undefined, size: 50 },
      }).then(r => r.data),
    enabled: !!entityId && tab === 'overtime',
  })
  const overtimes: any[] = Array.isArray(otData) ? otData : (otData?.items ?? [])

  const approveOtMutation = useMutation({
    mutationFn: ({ id, approved }: { id: string; approved: boolean }) =>
      api.post(`/attendance/overtime/${id}/approve`, {
        approved_by: 'user', status: approved ? 'approved' : 'rejected',
      }),
    onSuccess: () => { showToast(t('attendance:overtime_statusUpdated')); refetchOt() },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('common:failed'), 'error'),
  })

  const years = [currentYear(), currentYear() - 1]

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">{t('attendance:pageTitle')}</h1>
          <p className="text-sm text-gray-500 mt-0.5">{t('attendance:pageSubtitle')}</p>
        </div>
        <div className="flex items-center gap-2">
          <select value={month} onChange={e => setMonth(+e.target.value)} className="form-select w-36">
            {MONTHS.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
          </select>
          <select value={year} onChange={e => setYear(+e.target.value)} className="form-select w-24">
            {years.map(y => <option key={y} value={y}>{y}</option>)}
          </select>
        </div>
      </div>

      {/* Tabs */}
      <div className="border-b border-gray-200">
        <nav className="flex gap-1">
          {[
            { key: 'timesheet', label: t('attendance:tabTimesheet') },
            { key: 'leave',     label: t('attendance:tabLeave') },
            { key: 'overtime',  label: t('attendance:tabOvertime') },
          ].map(tb => (
            <button key={tb.key}
              onClick={() => setTab(tb.key as any)}
              className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                tab === tb.key ? 'border-primary-600 text-primary-700' : 'border-transparent text-gray-500 hover:text-gray-700'
              }`}>
              {tb.label}
            </button>
          ))}
        </nav>
      </div>

      {/* Tab: Timesheet */}
      {tab === 'timesheet' && (
        <Card noPad>
          <CardHeader
            title={t('attendance:timesheet_title', { month: MONTHS.find(m => m.value === month)?.label, year })}
            subtitle={t('attendance:timesheet_subtitle', { count: timesheets.length })}
            actions={
              <div className="flex items-center gap-2">
                <button onClick={() => processMutation.mutate()} disabled={processMutation.isPending}
                  className="btn-secondary">
                  <RefreshCw className="h-4 w-4" />
                  {processMutation.isPending ? t('attendance:timesheet_processing') : t('attendance:timesheet_processPeriod')}
                </button>
                <button onClick={() => refetchTs()} className="btn-secondary"><RefreshCw className="h-4 w-4" /></button>
              </div>
            }
          />
          {tsLoading ? (
            <div className="flex justify-center py-16"><Spinner size="lg" /></div>
          ) : timesheets.length === 0 ? (
            <EmptyState title={t('attendance:timesheet_emptyTitle')}
              description={t('attendance:timesheet_emptyDescription', { month: MONTHS.find(m => m.value === month)?.label, year })} />
          ) : (
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>{t('attendance:timesheet_colEmployee')}</th>
                    <th className="right">{t('attendance:timesheet_colWorkingDays')}</th>
                    <th className="right">{t('attendance:timesheet_colPresent')}</th>
                    <th className="right">{t('attendance:timesheet_colLate')}</th>
                    <th className="right">{t('attendance:timesheet_colLeave')}</th>
                    <th className="right">{t('attendance:timesheet_colPermission')}</th>
                    <th className="right">{t('attendance:timesheet_colAbsent')}</th>
                    <th className="right">{t('attendance:timesheet_colOvertimeHours')}</th>
                    <th>{t('common:status')}</th>
                  </tr>
                </thead>
                <tbody>
                  {timesheets.map((ts: any) => (
                    <tr key={ts.employee_id ?? ts.id}>
                      <td className="text-sm font-medium">{ts.employee_name ?? ts.full_name}</td>
                      <td className="right text-sm">{ts.working_days ?? ts.expected_days ?? '—'}</td>
                      <td className="right text-sm text-green-700">{ts.present_days ?? ts.days_present ?? '—'}</td>
                      <td className="right text-sm text-yellow-600">{ts.late_days ?? ts.days_late ?? 0}</td>
                      <td className="right text-sm text-blue-600">{ts.leave_days ?? ts.days_leave ?? 0}</td>
                      <td className="right text-sm text-purple-600">{ts.permission_days ?? ts.days_permission ?? 0}</td>
                      <td className="right text-sm text-red-600">{ts.absent_days ?? ts.days_absent ?? 0}</td>
                      <td className="right text-sm">{ts.overtime_hours ?? 0}</td>
                      <td><Badge status={ts.status ?? 'processed'} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      )}

      {/* Tab: Leave */}
      {tab === 'leave' && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <select value={leaveStatus} onChange={e => setLeaveStatus(e.target.value)} className="form-select w-36">
                <option value="">{t('common:allStatus')}</option>
                <option value="draft">{t('common:draft')}</option>
                <option value="submitted">{t('attendance:leave_statusSubmitted')}</option>
                <option value="approved">{t('common:approved')}</option>
                <option value="rejected">{t('attendance:leave_statusRejected')}</option>
              </select>
              <button onClick={() => refetchLeave()} className="btn-secondary"><RefreshCw className="h-4 w-4" /></button>
            </div>
            <button onClick={() => setShowLeaveForm(true)} className="btn-primary">
              <Plus className="h-4 w-4" /> {t('attendance:leave_requestBtn')}
            </button>
          </div>

          {showLeaveForm && (
            <Card>
              <div className="flex items-center justify-between mb-3">
                <p className="text-sm font-semibold text-gray-700">{t('attendance:leave_formTitle')}</p>
                <button onClick={() => setShowLeaveForm(false)}><X className="h-4 w-4 text-gray-400" /></button>
              </div>
              <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
                <div>
                  <label className="form-label">{t('attendance:employeeId')}</label>
                  <input value={leaveForm.employee_id}
                    onChange={e => setLeaveForm({ ...leaveForm, employee_id: e.target.value })}
                    className="form-input font-mono text-sm" placeholder="UUID karyawan" />
                </div>
                <div>
                  <label className="form-label">{t('attendance:leave_typeLabel')}</label>
                  <select value={leaveForm.leave_type_id}
                    onChange={e => setLeaveForm({ ...leaveForm, leave_type_id: e.target.value })}
                    className="form-select">
                    <option value="">{t('attendance:leave_selectPlaceholder')}</option>
                    {leaveTypes.map((lt: any) => (
                      <option key={lt.id} value={lt.id}>{lt.leave_name ?? lt.name}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="form-label">{t('attendance:leave_fromLabel')}</label>
                  <input type="date" value={leaveForm.start_date}
                    onChange={e => setLeaveForm({ ...leaveForm, start_date: e.target.value })}
                    className="form-input" />
                </div>
                <div>
                  <label className="form-label">{t('attendance:leave_toLabel')}</label>
                  <input type="date" value={leaveForm.end_date}
                    onChange={e => setLeaveForm({ ...leaveForm, end_date: e.target.value })}
                    className="form-input" />
                </div>
                <div className="col-span-2">
                  <label className="form-label">{t('attendance:leave_reasonLabel')}</label>
                  <input value={leaveForm.reason}
                    onChange={e => setLeaveForm({ ...leaveForm, reason: e.target.value })}
                    className="form-input" placeholder={t('attendance:leave_reasonPlaceholder')} />
                </div>
              </div>
              <div className="flex gap-3 mt-4">
                <button onClick={() => createLeaveMutation.mutate()}
                  disabled={createLeaveMutation.isPending || !leaveForm.employee_id}
                  className="btn-primary">
                  {createLeaveMutation.isPending ? t('common:saving') : t('common:save')}
                </button>
                <button onClick={() => setShowLeaveForm(false)} className="btn-secondary">{t('common:cancel')}</button>
              </div>
            </Card>
          )}

          <Card noPad>
            <CardHeader title={t('attendance:leave_listTitle')} subtitle={t('attendance:leave_listSubtitle', { count: leaves.length })} />
            {leaveLoading ? (
              <div className="flex justify-center py-16"><Spinner size="lg" /></div>
            ) : leaves.length === 0 ? (
              <EmptyState title={t('attendance:leave_emptyTitle')} />
            ) : (
              <div className="overflow-x-auto">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>{t('attendance:colEmployee')}</th>
                      <th>{t('attendance:leave_colType')}</th>
                      <th>{t('attendance:leave_colFrom')}</th>
                      <th>{t('attendance:leave_colTo')}</th>
                      <th className="right">{t('attendance:leave_colDays')}</th>
                      <th>{t('attendance:leave_colReason')}</th>
                      <th>{t('common:status')}</th>
                      <th>{t('common:action')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {leaves.map((l: any) => (
                      <tr key={l.id}>
                        <td className="text-sm font-medium">{l.employee_name ?? l.employee_id?.slice(0, 8)}</td>
                        <td className="text-sm">{l.leave_name ?? l.leave_type ?? '—'}</td>
                        <td className="text-sm text-gray-500 whitespace-nowrap">{formatDate(l.start_date)}</td>
                        <td className="text-sm text-gray-500 whitespace-nowrap">{formatDate(l.end_date)}</td>
                        <td className="right text-sm">{l.days ?? l.total_days ?? '—'}</td>
                        <td className="text-sm text-gray-500 max-w-xs truncate">{l.reason ?? '—'}</td>
                        <td><Badge status={l.status} /></td>
                        <td>
                          {l.status === 'submitted' && (
                            <div className="flex items-center gap-1">
                              <button onClick={() => approveLeaveMutation.mutate({ id: l.id, approved: true })}
                                className="inline-flex items-center gap-1 text-xs px-2 py-1 bg-green-50 text-green-600 hover:bg-green-100 rounded-md">
                                <CheckCircle className="h-3 w-3" /> {t('common:approve')}
                              </button>
                              <button onClick={() => approveLeaveMutation.mutate({ id: l.id, approved: false })}
                                className="inline-flex items-center gap-1 text-xs px-2 py-1 bg-red-50 text-red-600 hover:bg-red-100 rounded-md">
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
      )}

      {/* Tab: Overtime */}
      {tab === 'overtime' && (
        <Card noPad>
          <CardHeader
            title={t('attendance:overtime_title')}
            subtitle={t('attendance:overtime_subtitle', { count: overtimes.length })}
            actions={
              <div className="flex items-center gap-2">
                <select value={otStatus} onChange={e => setOtStatus(e.target.value)} className="form-select w-36">
                  <option value="">{t('common:allStatus')}</option>
                  <option value="draft">{t('common:draft')}</option>
                  <option value="submitted">{t('attendance:leave_statusSubmitted')}</option>
                  <option value="approved">{t('common:approved')}</option>
                  <option value="completed">{t('attendance:overtime_statusCompleted')}</option>
                  <option value="rejected">{t('attendance:leave_statusRejected')}</option>
                </select>
                <button onClick={() => refetchOt()} className="btn-secondary"><RefreshCw className="h-4 w-4" /></button>
              </div>
            }
          />
          {otLoading ? (
            <div className="flex justify-center py-16"><Spinner size="lg" /></div>
          ) : overtimes.length === 0 ? (
            <EmptyState title={t('attendance:overtime_emptyTitle')} />
          ) : (
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>{t('attendance:colEmployee')}</th>
                    <th>{t('attendance:overtime_colDate')}</th>
                    <th className="right">{t('attendance:overtime_colPlannedHours')}</th>
                    <th className="right">{t('attendance:overtime_colActualHours')}</th>
                    <th>{t('attendance:leave_colReason')}</th>
                    <th>{t('common:status')}</th>
                    <th>{t('common:action')}</th>
                  </tr>
                </thead>
                <tbody>
                  {overtimes.map((ot: any) => (
                    <tr key={ot.id}>
                      <td className="text-sm font-medium">{ot.employee_name ?? ot.employee_id?.slice(0, 8)}</td>
                      <td className="text-sm text-gray-500 whitespace-nowrap">{formatDate(ot.overtime_date ?? ot.date)}</td>
                      <td className="right text-sm">{ot.planned_hours ?? ot.hours_planned ?? '—'} {t('attendance:overtime_hoursSuffix')}</td>
                      <td className="right text-sm text-primary-700">{ot.actual_hours ?? ot.hours_actual ?? '—'} {t('attendance:overtime_hoursSuffix')}</td>
                      <td className="text-sm text-gray-500 max-w-xs truncate">{ot.reason ?? ot.description ?? '—'}</td>
                      <td><Badge status={ot.status} /></td>
                      <td>
                        {ot.status === 'submitted' && (
                          <div className="flex items-center gap-1">
                            <button onClick={() => approveOtMutation.mutate({ id: ot.id, approved: true })}
                              className="inline-flex items-center gap-1 text-xs px-2 py-1 bg-green-50 text-green-600 hover:bg-green-100 rounded-md">
                              <CheckCircle className="h-3 w-3" /> {t('common:approve')}
                            </button>
                            <button onClick={() => approveOtMutation.mutate({ id: ot.id, approved: false })}
                              className="inline-flex items-center gap-1 text-xs px-2 py-1 bg-red-50 text-red-600 hover:bg-red-100 rounded-md">
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
      )}
    </div>
  )
}
