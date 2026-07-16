import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Download, Shield, AlertTriangle, Activity } from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext'
import api from '../../lib/api'
import { Card } from '../../components/ui/Card'
import Spinner from '../../components/ui/Spinner'
import EmptyState from '../../components/ui/EmptyState'
import Badge from '../../components/ui/Badge'
import { formatDate } from '../../lib/utils'

const SEVERITIES = ['', 'info', 'warning', 'critical']

function StatCard({ label, value, icon, color }: { label: string; value: any; icon: React.ReactNode; color: string }) {
  return (
    <Card className="flex items-center gap-3">
      <div className={`rounded-xl p-2.5 ${color}`}>{icon}</div>
      <div>
        <p className="text-2xl font-bold text-gray-900">{value ?? 0}</p>
        <p className="text-xs text-gray-500">{label}</p>
      </div>
    </Card>
  )
}

export default function AuditTrailPage() {
  const { entityId } = useAuth()
  const [module, setModule] = useState('')
  const [action, setAction] = useState('')
  const [severity, setSeverity] = useState('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [page, setPage] = useState(1)

  const params: Record<string, any> = {
    page, size: 50,
    ...(module && { module }),
    ...(action && { action }),
    ...(severity && { severity }),
    ...(dateFrom && { date_from: dateFrom }),
    ...(dateTo && { date_to: dateTo }),
  }

  const { data, isLoading } = useQuery({
    queryKey: ['audit-log', entityId, params],
    queryFn: () => api.get(`/audit/entity/${entityId}`, { params }).then(r => r.data),
    enabled: !!entityId,
  })

  const { data: stats } = useQuery({
    queryKey: ['audit-stats', entityId],
    queryFn: () => api.get(`/audit/entity/${entityId}/stats`).then(r => r.data),
    enabled: !!entityId,
  })

  const { data: metaModules } = useQuery({
    queryKey: ['audit-modules'],
    queryFn: () => api.get('/audit/meta/modules').then(r => r.data),
  })

  const { data: metaActions } = useQuery({
    queryKey: ['audit-actions'],
    queryFn: () => api.get('/audit/meta/actions').then(r => r.data),
  })

  const rows: any[] = Array.isArray(data?.items) ? data.items : (Array.isArray(data) ? data : [])
  const total: number = data?.total ?? rows.length
  const modules: string[] = Array.isArray(metaModules) ? metaModules : []
  const actions: string[] = Array.isArray(metaActions) ? metaActions : []
  const statsData = stats ?? {}

  const handleExport = () => {
    const qs = new URLSearchParams({
      ...(module && { module }), ...(severity && { severity }),
      ...(dateFrom && { date_from: dateFrom }), ...(dateTo && { date_to: dateTo }),
    }).toString()
    window.open(`/api/audit/entity/${entityId}/export${qs ? '?' + qs : ''}`, '_blank')
  }

  const severityColor: Record<string, string> = {
    info: 'bg-blue-50',
    warning: 'bg-yellow-50',
    critical: 'bg-red-50',
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Audit Trail</h1>
          <p className="text-sm text-gray-500 mt-0.5">Log semua aktivitas dan perubahan data sistem</p>
        </div>
        <button onClick={handleExport} className="btn-secondary"><Download className="h-4 w-4" /> Export CSV</button>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-4 gap-4">
        <StatCard label="Total Aktivitas (30hr)" value={statsData.total_events} icon={<Activity className="h-5 w-5 text-blue-600" />} color="bg-blue-50" />
        <StatCard label="Warning" value={statsData.warning_count} icon={<AlertTriangle className="h-5 w-5 text-yellow-600" />} color="bg-yellow-50" />
        <StatCard label="Critical" value={statsData.critical_count} icon={<Shield className="h-5 w-5 text-red-600" />} color="bg-red-50" />
        <StatCard label="Modul Aktif" value={statsData.active_modules} icon={<Activity className="h-5 w-5 text-green-600" />} color="bg-green-50" />
      </div>

      {/* Filters */}
      <Card>
        <div className="flex flex-wrap gap-3 items-end">
          <div>
            <label className="form-label">Modul</label>
            <select value={module} onChange={e => { setModule(e.target.value); setPage(1) }} className="form-select w-36">
              <option value="">Semua</option>
              {modules.map((m: string) => <option key={m} value={m}>{m}</option>)}
            </select>
          </div>
          <div>
            <label className="form-label">Action</label>
            <select value={action} onChange={e => { setAction(e.target.value); setPage(1) }} className="form-select w-36">
              <option value="">Semua</option>
              {actions.map((a: string) => <option key={a} value={a}>{a}</option>)}
            </select>
          </div>
          <div>
            <label className="form-label">Severity</label>
            <select value={severity} onChange={e => { setSeverity(e.target.value); setPage(1) }} className="form-select w-28">
              {SEVERITIES.map(s => <option key={s} value={s}>{s || 'Semua'}</option>)}
            </select>
          </div>
          <div>
            <label className="form-label">Dari</label>
            <input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)} className="form-input w-36" />
          </div>
          <div>
            <label className="form-label">Sampai</label>
            <input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)} className="form-input w-36" />
          </div>
          {(module || action || severity || dateFrom || dateTo) && (
            <button onClick={() => { setModule(''); setAction(''); setSeverity(''); setDateFrom(''); setDateTo(''); setPage(1) }}
              className="text-xs text-blue-600 hover:underline self-end pb-1.5">Reset</button>
          )}
        </div>
      </Card>

      {/* Log table */}
      <Card>
        <div className="flex items-center justify-between mb-3">
          <p className="text-sm text-gray-500">{total} aktivitas ditemukan</p>
          {total > 50 && (
            <div className="flex gap-2">
              <button disabled={page === 1} onClick={() => setPage(p => p - 1)} className="btn-secondary text-xs py-1 px-2">← Prev</button>
              <span className="text-xs text-gray-500 self-center">Hal {page}</span>
              <button disabled={rows.length < 50} onClick={() => setPage(p => p + 1)} className="btn-secondary text-xs py-1 px-2">Next →</button>
            </div>
          )}
        </div>

        {isLoading ? (
          <div className="py-8 flex justify-center"><Spinner /></div>
        ) : rows.length === 0 ? (
          <EmptyState title="Belum ada aktivitas" description="Log akan muncul saat ada transaksi di sistem." />
        ) : (
          <table className="min-w-full text-sm">
            <thead>
              <tr className="border-b border-gray-200">
                {['Waktu', 'User', 'Modul', 'Aksi', 'Deskripsi', 'Severity'].map(h => (
                  <th key={h} className="text-left py-2 pr-4 text-xs font-medium text-gray-600">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r: any) => (
                <tr key={r.id} className={`border-b border-gray-50 hover:opacity-90 ${severityColor[r.severity] ?? ''}`}>
                  <td className="py-2 pr-4 text-xs text-gray-500 whitespace-nowrap">
                    {r.created_at ? formatDate(r.created_at) : '—'}
                  </td>
                  <td className="py-2 pr-4 text-xs font-medium">{r.user_name ?? r.user_id?.slice(0, 8) ?? '—'}</td>
                  <td className="py-2 pr-4 text-xs text-gray-600">{r.module}</td>
                  <td className="py-2 pr-4 text-xs font-mono">{r.action}</td>
                  <td className="py-2 pr-4 text-xs text-gray-700 max-w-xs truncate">{r.description}</td>
                  <td className="py-2 pr-4"><Badge status={r.severity ?? 'info'} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  )
}
