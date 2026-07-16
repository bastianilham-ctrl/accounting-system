import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Download, CheckCircle, Send, Banknote } from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext'
import api from '../../lib/api'
import { Card } from '../../components/ui/Card'
import Spinner from '../../components/ui/Spinner'
import EmptyState from '../../components/ui/EmptyState'
import Badge from '../../components/ui/Badge'
import { formatRupiah, formatDate, currentYear, MONTHS } from '../../lib/utils'
import { showToast } from '../../components/ui/Toast'


export default function PayrollDisbursementPage() {
  const { entityId, user } = useAuth()
  const qc = useQueryClient()
  const [year, setYear] = useState(currentYear())
  const [month, setMonth] = useState(new Date().getMonth() + 1)
  const [showCreate, setShowCreate] = useState(false)
  const [selected, setSelected] = useState<any>(null)

  const { data, isLoading } = useQuery({
    queryKey: ['payroll-disbursements', entityId, year, month],
    queryFn: () => api.get('/payroll-disbursement', { params: { entity_id: entityId, year, month } }).then(r => r.data),
    enabled: !!entityId,
  })

  const rows: any[] = Array.isArray(data) ? data : (data?.items ?? [])

  const invalidate = () => qc.invalidateQueries({ queryKey: ['payroll-disbursements'] })

  const createMutation = useMutation({
    mutationFn: () => api.post('/payroll-disbursement', { entity_id: entityId, year, month, created_by: user?.username ?? '' }),
    onSuccess: () => { showToast('Disbursement dibuat'); setShowCreate(false); invalidate() },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? 'Gagal membuat disbursement', 'error'),
  })

  const action = (path: string, body: any = {}) =>
    api.post(`/payroll-disbursement/${selected?.id}${path}`, body)
      .then(() => { showToast('Berhasil'); setSelected(null); invalidate() })
      .catch((e: any) => showToast(e?.response?.data?.detail ?? 'Gagal', 'error'))

  const submitMutation = useMutation({ mutationFn: () => action('/submit') })
  const approveMutation = useMutation({ mutationFn: () => action('/approve', { approved_by: user?.username }) })
  const accrualMutation = useMutation({ mutationFn: () => action('/post-accrual', { posted_by: user?.username }) })
  const disburseMutation = useMutation({ mutationFn: () => action('/disburse', { disbursed_by: user?.username }) })
  const transferMutation = useMutation({ mutationFn: () => action('/mark-transferred', { confirmed_by: user?.username }) })
  const cancelMutation = useMutation({ mutationFn: () => action('/cancel', { cancelled_by: user?.username, reason: 'Manual cancel' }) })

  const canRun = (m: any) => !m.isPending

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Disbursement Payroll</h1>
          <p className="text-sm text-gray-500 mt-0.5">Proses pembayaran gaji karyawan ke rekening bank</p>
        </div>
        <button onClick={() => setShowCreate(true)} className="btn-primary"><Send className="h-4 w-4" /> Buat Disbursement</button>
      </div>

      {/* Filter */}
      <Card>
        <div className="flex gap-4 items-end">
          <div>
            <label className="form-label">Bulan</label>
            <select value={month} onChange={e => setMonth(+e.target.value)} className="form-select w-36">
              {MONTHS.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
            </select>
          </div>
          <div>
            <label className="form-label">Tahun</label>
            <input type="number" value={year} onChange={e => setYear(+e.target.value)} className="form-input w-24" min={2020} max={2099} />
          </div>
        </div>
      </Card>

      {/* List */}
      <Card>
        {isLoading ? <div className="py-8 flex justify-center"><Spinner /></div>
          : rows.length === 0
            ? <EmptyState title="Belum ada disbursement" description="Klik 'Buat Disbursement' untuk memulai proses pembayaran gaji." />
            : (
              <table className="min-w-full text-sm">
                <thead><tr className="border-b border-gray-200">
                  {['Periode', 'Karyawan', 'Total', 'Status', 'Dibuat', 'Aksi'].map(h => (
                    <th key={h} className="text-left py-2 pr-4 font-medium text-gray-600">{h}</th>
                  ))}
                </tr></thead>
                <tbody>
                  {rows.map((r: any) => (
                    <tr key={r.id} className="border-b border-gray-50 hover:bg-gray-50">
                      <td className="py-2 pr-4 font-medium">{r.period ?? `${r.year ?? year}/${String(r.month ?? month).padStart(2, '0')}`}</td>
                      <td className="py-2 pr-4 text-gray-600">{r.employee_count ?? r.employees_count ?? '—'}</td>
                      <td className="py-2 pr-4">{formatRupiah(r.total_amount ?? r.total_net ?? 0)}</td>
                      <td className="py-2 pr-4"><Badge status={r.status} /></td>
                      <td className="py-2 pr-4 text-gray-500 text-xs">{r.created_at ? formatDate(r.created_at) : ''}</td>
                      <td className="py-2">
                        <button onClick={() => setSelected(r)} className="text-xs text-blue-600 hover:underline">Kelola</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
      </Card>

      {/* Action panel for selected disbursement */}
      {selected && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl p-6 w-96 shadow-xl space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="text-lg font-bold text-gray-900">Kelola Disbursement</h3>
              <button onClick={() => setSelected(null)} className="text-gray-400 hover:text-gray-600 text-xl">×</button>
            </div>
            <div className="text-sm text-gray-600 space-y-1">
              <p>Periode: <strong>{selected.period ?? `${selected.year}/${String(selected.month).padStart(2, '0')}`}</strong></p>
              <p>Total: <strong>{formatRupiah(selected.total_amount ?? selected.total_net ?? 0)}</strong></p>
              <p>Status: <Badge status={selected.status} /></p>
            </div>
            <div className="space-y-2">
              {selected.status === 'draft' && (
                <button onClick={() => submitMutation.mutate()} disabled={!canRun(submitMutation)} className="btn-secondary w-full text-sm">
                  <Send className="h-3.5 w-3.5" /> {submitMutation.isPending ? 'Proses...' : 'Submit'}
                </button>
              )}
              {selected.status === 'submitted' && (
                <button onClick={() => approveMutation.mutate()} disabled={!canRun(approveMutation)} className="btn-primary w-full text-sm">
                  <CheckCircle className="h-3.5 w-3.5" /> {approveMutation.isPending ? 'Proses...' : 'Approve'}
                </button>
              )}
              {selected.status === 'approved' && (
                <button onClick={() => accrualMutation.mutate()} disabled={!canRun(accrualMutation)} className="btn-primary w-full text-sm">
                  {accrualMutation.isPending ? 'Proses...' : 'Post Accrual Journal'}
                </button>
              )}
              {selected.status === 'accrual_posted' && (
                <button onClick={() => disburseMutation.mutate()} disabled={!canRun(disburseMutation)} className="btn-primary w-full text-sm">
                  <Banknote className="h-3.5 w-3.5" /> {disburseMutation.isPending ? 'Proses...' : 'Disburse (Kirim ke Bank)'}
                </button>
              )}
              {selected.status === 'disbursed' && (
                <button onClick={() => transferMutation.mutate()} disabled={!canRun(transferMutation)} className="btn-primary w-full text-sm">
                  {transferMutation.isPending ? 'Proses...' : 'Konfirmasi Transfer'}
                </button>
              )}
              {selected.status !== 'transferred' && selected.status !== 'cancelled' && (
                <>
                  <a href={`/api/payroll-disbursement/${selected.id}/export-bank-file`} target="_blank" rel="noreferrer"
                    className="btn-secondary w-full text-sm flex items-center justify-center gap-2">
                    <Download className="h-3.5 w-3.5" /> Export File Bank
                  </a>
                  <button onClick={() => cancelMutation.mutate()} disabled={!canRun(cancelMutation)}
                    className="w-full text-sm text-red-600 border border-red-300 rounded-lg py-2 hover:bg-red-50">
                    {cancelMutation.isPending ? 'Membatalkan...' : 'Batalkan Disbursement'}
                  </button>
                </>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Create modal */}
      {showCreate && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl p-6 w-80 shadow-xl space-y-4">
            <h3 className="text-lg font-bold text-gray-900">Buat Disbursement Baru</h3>
            <p className="text-sm text-gray-600">Periode: <strong>{MONTHS.find(m => m.value === month)?.label} {year}</strong></p>
            <div className="flex gap-3 justify-end">
              <button onClick={() => setShowCreate(false)} className="btn-secondary">Batal</button>
              <button onClick={() => createMutation.mutate()} disabled={createMutation.isPending} className="btn-primary">
                {createMutation.isPending ? 'Membuat...' : 'Buat'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
