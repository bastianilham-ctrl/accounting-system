import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, CheckCircle, XCircle, Clock } from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext'
import api from '../../lib/api'
import { Card } from '../../components/ui/Card'
import Spinner from '../../components/ui/Spinner'
import EmptyState from '../../components/ui/EmptyState'
import Badge from '../../components/ui/Badge'
import { formatDate, currentYear } from '../../lib/utils'
import { showToast } from '../../components/ui/Toast'

type Tab = 'requests' | 'balance' | 'pending'
const BLANK_REQ = { employee_id: '', leave_type_id: '', date_from: '', date_to: '', reason: '' }

export default function LeavePage() {
  const { entityId, user } = useAuth()
  const qc = useQueryClient()
  const [tab, setTab] = useState<Tab>('requests')
  const [showCreate, setShowCreate] = useState(false)
  const [form, setForm] = useState(BLANK_REQ)
  const [rejectReason, setRejectReason] = useState('')
  const [actingOn, setActingOn] = useState<{ id: string; action: 'approve' | 'reject' } | null>(null)

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['leave-requests'] })
    qc.invalidateQueries({ queryKey: ['leave-balance'] })
    qc.invalidateQueries({ queryKey: ['leave-pending'] })
  }

  const { data: leaveTypes } = useQuery({
    queryKey: ['leave-types'],
    queryFn: () => api.get('/leave/types').then(r => r.data),
  })

  const { data: employees } = useQuery({
    queryKey: ['employees-for-leave', entityId],
    queryFn: () => api.get('/employees', { params: { entity_id: entityId } }).then(r => r.data),
    enabled: !!entityId,
  })

  const { data: requests, isLoading: reqLoading } = useQuery({
    queryKey: ['leave-requests', entityId],
    queryFn: () => api.get('/leave/requests', { params: { entity_id: entityId } }).then(r => r.data),
    enabled: !!entityId && tab === 'requests',
  })

  const { data: balance, isLoading: balLoading } = useQuery({
    queryKey: ['leave-balance', entityId],
    queryFn: () => api.get('/leave/entitlements', {
      params: { entity_id: entityId, fiscal_year: currentYear() }
    }).then(r => r.data),
    enabled: !!entityId && tab === 'balance',
  })

  const { data: pending, isLoading: pendLoading } = useQuery({
    queryKey: ['leave-pending', entityId],
    queryFn: () => api.get('/leave/reports/pending', { params: { entity_id: entityId } }).then(r => r.data),
    enabled: !!entityId && tab === 'pending',
  })

  const reqRows: any[] = Array.isArray(requests) ? requests : (requests?.items ?? [])
  const balRows: any[] = Array.isArray(balance) ? balance : (balance?.items ?? [])
  const pendRows: any[] = Array.isArray(pending) ? pending : (pending?.items ?? [])
  const types: any[] = Array.isArray(leaveTypes) ? leaveTypes : []
  const empList: any[] = Array.isArray(employees) ? employees : (employees?.items ?? [])

  const createMutation = useMutation({
    mutationFn: () => api.post('/leave/requests', { entity_id: entityId, ...form }),
    onSuccess: () => { showToast('Pengajuan cuti dibuat'); setShowCreate(false); setForm(BLANK_REQ); invalidate() },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? 'Gagal', 'error'),
  })

  const submitMutation = useMutation({
    mutationFn: (id: string) => api.post(`/leave/requests/${id}/submit`, { submitted_by: user?.username }),
    onSuccess: () => { showToast('Pengajuan disubmit'); invalidate() },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? 'Gagal', 'error'),
  })

  const approveMutation = useMutation({
    mutationFn: ({ id }: { id: string }) =>
      api.post(`/leave/requests/${id}/approve`, { approved_by: user?.username }),
    onSuccess: () => { showToast('Cuti disetujui'); setActingOn(null); invalidate() },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? 'Gagal', 'error'),
  })

  const rejectMutation = useMutation({
    mutationFn: ({ id }: { id: string }) =>
      api.post(`/leave/requests/${id}/reject`, { rejected_by: user?.username, reason: rejectReason }),
    onSuccess: () => { showToast('Cuti ditolak'); setActingOn(null); setRejectReason(''); invalidate() },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? 'Gagal', 'error'),
  })

  const f = (k: keyof typeof BLANK_REQ) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
    setForm(p => ({ ...p, [k]: e.target.value }))

  const TABS: { key: Tab; label: string }[] = [
    { key: 'requests', label: 'Semua Pengajuan' },
    { key: 'pending', label: 'Menunggu Approval' },
    { key: 'balance', label: 'Saldo Cuti' },
  ]

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Manajemen Cuti</h1>
          <p className="text-sm text-gray-500 mt-0.5">Pengajuan, approval, dan saldo cuti karyawan</p>
        </div>
        <button onClick={() => setShowCreate(true)} className="btn-primary"><Plus className="h-4 w-4" /> Ajukan Cuti</button>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-gray-200">
        {TABS.map(t => (
          <button key={t.key} onClick={() => setTab(t.key)}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${tab === t.key ? 'border-primary-500 text-primary-600' : 'border-transparent text-gray-500 hover:text-gray-700'}`}>
            {t.label}
          </button>
        ))}
      </div>

      {/* Requests tab */}
      {tab === 'requests' && (
        <Card>
          {reqLoading ? <div className="py-8 flex justify-center"><Spinner /></div>
            : reqRows.length === 0
              ? <EmptyState title="Belum ada pengajuan cuti" description="Klik 'Ajukan Cuti' untuk membuat pengajuan baru." />
              : (
                <table className="min-w-full text-sm">
                  <thead><tr className="border-b border-gray-200">
                    {['Karyawan', 'Tipe Cuti', 'Dari', 'Sampai', 'Hari', 'Alasan', 'Status', 'Aksi'].map(h => (
                      <th key={h} className="text-left py-2 pr-4 text-xs font-medium text-gray-600">{h}</th>
                    ))}
                  </tr></thead>
                  <tbody>
                    {reqRows.map((r: any) => (
                      <tr key={r.id} className="border-b border-gray-50 hover:bg-gray-50">
                        <td className="py-2 pr-4 font-medium">{r.employee_name ?? r.employee_id?.slice(0, 8)}</td>
                        <td className="py-2 pr-4 text-gray-600">{r.leave_type_name ?? r.leave_type_id?.slice(0, 8)}</td>
                        <td className="py-2 pr-4 text-xs">{formatDate(r.date_from)}</td>
                        <td className="py-2 pr-4 text-xs">{formatDate(r.date_to)}</td>
                        <td className="py-2 pr-4 text-center font-medium">{r.working_days ?? '—'}</td>
                        <td className="py-2 pr-4 text-xs text-gray-500 max-w-[120px] truncate">{r.reason ?? '—'}</td>
                        <td className="py-2 pr-4"><Badge status={r.status} /></td>
                        <td className="py-2 flex gap-2">
                          {r.status === 'draft' && (
                            <button onClick={() => submitMutation.mutate(r.id)} className="text-xs text-blue-600 hover:underline">Submit</button>
                          )}
                          {r.status === 'submitted' && (
                            <>
                              <button onClick={() => setActingOn({ id: r.id, action: 'approve' })} className="text-xs text-green-600 hover:underline">Approve</button>
                              <button onClick={() => setActingOn({ id: r.id, action: 'reject' })} className="text-xs text-red-500 hover:underline">Tolak</button>
                            </>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
        </Card>
      )}

      {/* Pending tab */}
      {tab === 'pending' && (
        <Card>
          {pendLoading ? <div className="py-8 flex justify-center"><Spinner /></div>
            : pendRows.length === 0
              ? <EmptyState title="Tidak ada pengajuan pending" description="Semua pengajuan sudah diproses." />
              : (
                <table className="min-w-full text-sm">
                  <thead><tr className="border-b border-gray-200">
                    {['Karyawan', 'Tipe Cuti', 'Dari', 'Sampai', 'Hari', 'Diajukan', 'Aksi'].map(h => (
                      <th key={h} className="text-left py-2 pr-4 text-xs font-medium text-gray-600">{h}</th>
                    ))}
                  </tr></thead>
                  <tbody>
                    {pendRows.map((r: any) => (
                      <tr key={r.id} className="border-b border-gray-50 hover:bg-yellow-50">
                        <td className="py-2 pr-4 font-medium">{r.employee_name ?? '—'}</td>
                        <td className="py-2 pr-4 text-gray-600">{r.leave_type_name ?? '—'}</td>
                        <td className="py-2 pr-4 text-xs">{formatDate(r.date_from)}</td>
                        <td className="py-2 pr-4 text-xs">{formatDate(r.date_to)}</td>
                        <td className="py-2 pr-4 text-center">{r.working_days ?? '—'}</td>
                        <td className="py-2 pr-4 text-xs text-gray-500">{r.submitted_at ? formatDate(r.submitted_at) : '—'}</td>
                        <td className="py-2 flex gap-2">
                          <button onClick={() => approveMutation.mutate({ id: r.id })}
                            disabled={approveMutation.isPending}
                            className="flex items-center gap-1 text-xs text-green-600 hover:underline">
                            <CheckCircle className="h-3.5 w-3.5" /> Approve
                          </button>
                          <button onClick={() => setActingOn({ id: r.id, action: 'reject' })}
                            className="flex items-center gap-1 text-xs text-red-500 hover:underline">
                            <XCircle className="h-3.5 w-3.5" /> Tolak
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
        </Card>
      )}

      {/* Balance tab */}
      {tab === 'balance' && (
        <Card>
          {balLoading ? <div className="py-8 flex justify-center"><Spinner /></div>
            : balRows.length === 0
              ? <EmptyState title="Belum ada data saldo cuti" description="Inisialisasi jatah cuti di awal tahun." />
              : (
                <table className="min-w-full text-sm">
                  <thead><tr className="border-b border-gray-200">
                    {['Karyawan', 'Tipe Cuti', 'Jatah', 'Terpakai', 'Pending', 'Sisa', 'Carry-Forward'].map(h => (
                      <th key={h} className="text-left py-2 pr-4 text-xs font-medium text-gray-600">{h}</th>
                    ))}
                  </tr></thead>
                  <tbody>
                    {balRows.map((r: any) => (
                      <tr key={r.id} className="border-b border-gray-50 hover:bg-gray-50">
                        <td className="py-2 pr-4 font-medium">{r.employee_name ?? '—'}</td>
                        <td className="py-2 pr-4 text-gray-600">{r.leave_type_name ?? '—'}</td>
                        <td className="py-2 pr-4 text-center">{r.entitled_days}</td>
                        <td className="py-2 pr-4 text-center text-red-600">{r.used_days}</td>
                        <td className="py-2 pr-4 text-center text-yellow-600">{r.pending_days ?? 0}</td>
                        <td className="py-2 pr-4 text-center font-bold text-green-700">
                          {(r.entitled_days ?? 0) - (r.used_days ?? 0) - (r.pending_days ?? 0)}
                        </td>
                        <td className="py-2 pr-4 text-center text-gray-500">{r.carried_forward_days ?? 0}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
        </Card>
      )}

      {/* Create request modal */}
      {showCreate && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl p-6 w-[26rem] shadow-xl space-y-4">
            <h3 className="text-lg font-bold">Ajukan Cuti</h3>
            <div>
              <label className="form-label">Karyawan *</label>
              <select value={form.employee_id} onChange={f('employee_id')} className="form-select w-full">
                <option value="">Pilih karyawan...</option>
                {empList.map((e: any) => <option key={e.id} value={e.id}>{e.full_name}</option>)}
              </select>
            </div>
            <div>
              <label className="form-label">Tipe Cuti *</label>
              <select value={form.leave_type_id} onChange={f('leave_type_id')} className="form-select w-full">
                <option value="">Pilih tipe...</option>
                {types.map((t: any) => <option key={t.id} value={t.id}>{t.name} ({t.max_days_per_year} hr/thn)</option>)}
              </select>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div><label className="form-label">Dari *</label>
                <input type="date" value={form.date_from} onChange={f('date_from')} className="form-input w-full" /></div>
              <div><label className="form-label">Sampai *</label>
                <input type="date" value={form.date_to} onChange={f('date_to')} className="form-input w-full" /></div>
            </div>
            <div>
              <label className="form-label">Alasan</label>
              <textarea value={form.reason} onChange={f('reason')} className="form-input w-full" rows={2} />
            </div>
            <div className="flex gap-3 justify-end">
              <button onClick={() => { setShowCreate(false); setForm(BLANK_REQ) }} className="btn-secondary">Batal</button>
              <button onClick={() => createMutation.mutate()}
                disabled={createMutation.isPending || !form.employee_id || !form.leave_type_id || !form.date_from || !form.date_to}
                className="btn-primary">{createMutation.isPending ? 'Menyimpan...' : 'Simpan Draft'}</button>
            </div>
          </div>
        </div>
      )}

      {/* Reject modal */}
      {actingOn?.action === 'reject' && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl p-6 w-80 shadow-xl space-y-4">
            <div className="flex items-center gap-2 text-red-600">
              <XCircle className="h-5 w-5" /> <h3 className="font-bold">Tolak Pengajuan</h3>
            </div>
            <div>
              <label className="form-label">Alasan Penolakan</label>
              <textarea value={rejectReason} onChange={e => setRejectReason(e.target.value)}
                className="form-input w-full" rows={3} placeholder="Wajib diisi..." />
            </div>
            <div className="flex gap-3 justify-end">
              <button onClick={() => { setActingOn(null); setRejectReason('') }} className="btn-secondary">Batal</button>
              <button onClick={() => rejectMutation.mutate({ id: actingOn.id })}
                disabled={rejectMutation.isPending || !rejectReason}
                className="bg-red-600 text-white rounded-lg px-4 py-2 text-sm hover:bg-red-700 disabled:opacity-50">
                {rejectMutation.isPending ? 'Menolak...' : 'Tolak'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Approve confirm */}
      {actingOn?.action === 'approve' && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl p-6 w-72 shadow-xl text-center space-y-4">
            <CheckCircle className="h-10 w-10 text-green-600 mx-auto" />
            <p className="text-sm text-gray-700">Setujui pengajuan cuti ini?</p>
            <div className="flex gap-3 justify-center">
              <button onClick={() => setActingOn(null)} className="btn-secondary">Batal</button>
              <button onClick={() => approveMutation.mutate({ id: actingOn.id })}
                disabled={approveMutation.isPending}
                className="btn-primary">{approveMutation.isPending ? '...' : 'Setujui'}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
