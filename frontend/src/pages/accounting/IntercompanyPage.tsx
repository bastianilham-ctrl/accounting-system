import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, ArrowRightLeft } from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext'
import api from '../../lib/api'
import { Card } from '../../components/ui/Card'
import Spinner from '../../components/ui/Spinner'
import EmptyState from '../../components/ui/EmptyState'
import Badge from '../../components/ui/Badge'
import { formatRupiah, formatDate } from '../../lib/utils'
import { showToast } from '../../components/ui/Toast'


export default function IntercompanyPage() {
  const { entityId, user } = useAuth()
  const qc = useQueryClient()
  const [selected, setSelected] = useState<any>(null)
  const [showCreate, setShowCreate] = useState(false)
  const [form, setForm] = useState({
    transaction_type_id: '', counterparty_entity_id: '',
    amount: '', currency: 'IDR', description: '',
    journal_date: new Date().toISOString().slice(0, 10),
  })

  const { data: txTypes } = useQuery({
    queryKey: ['ic-tx-types'],
    queryFn: () => api.get('/intercompany/transaction-types').then(r => r.data),
  })

  const { data, isLoading } = useQuery({
    queryKey: ['intercompany-txns', entityId],
    queryFn: () => api.get('/intercompany/transactions', { params: { entity_id: entityId } }).then(r => r.data),
    enabled: !!entityId,
  })

  const rows: any[] = Array.isArray(data) ? data : (data?.items ?? [])
  const types: any[] = Array.isArray(txTypes) ? txTypes : (txTypes?.items ?? [])
  const invalidate = () => qc.invalidateQueries({ queryKey: ['intercompany-txns'] })

  const createMutation = useMutation({
    mutationFn: () => api.post('/intercompany/transactions', {
      entity_id: entityId, ...form, amount: +form.amount,
      created_by: user?.username,
    }),
    onSuccess: () => { showToast('Transaksi intercompany dibuat'); setShowCreate(false); invalidate() },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? 'Gagal', 'error'),
  })

  const doAction = (path: string, body: any = {}) =>
    api.post(`/intercompany/transactions/${selected?.id}${path}`, body)
      .then(() => { showToast('Berhasil'); setSelected(null); invalidate() })
      .catch((e: any) => showToast(e?.response?.data?.detail ?? 'Gagal', 'error'))

  const submitM = useMutation({ mutationFn: () => doAction('/submit', { submitted_by: user?.username }) })
  const confirmM = useMutation({ mutationFn: () => doAction('/confirm', { confirmed_by: user?.username }) })
  const postM = useMutation({ mutationFn: () => doAction('/post', { posted_by: user?.username }) })
  const settleM = useMutation({ mutationFn: () => doAction('/settle', { settled_by: user?.username }) })
  const cancelM = useMutation({ mutationFn: () => doAction('/cancel', { cancelled_by: user?.username }) })

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Transaksi Intercompany</h1>
          <p className="text-sm text-gray-500 mt-0.5">Transaksi dan eliminasi antar entitas dalam grup</p>
        </div>
        <button onClick={() => setShowCreate(true)} className="btn-primary"><Plus className="h-4 w-4" /> Transaksi Baru</button>
      </div>

      <Card>
        {isLoading ? <div className="py-8 flex justify-center"><Spinner /></div>
          : rows.length === 0
            ? <EmptyState title="Belum ada transaksi intercompany" description="Buat transaksi pertama antar entitas." />
            : (
              <table className="min-w-full text-sm">
                <thead><tr className="border-b border-gray-200">
                  {['Referensi', 'Jenis', 'Counterparty', 'Jumlah', 'Tgl', 'Status', 'Aksi'].map(h => (
                    <th key={h} className="text-left py-2 pr-4 font-medium text-gray-600">{h}</th>
                  ))}
                </tr></thead>
                <tbody>
                  {rows.map((r: any) => (
                    <tr key={r.id} className="border-b border-gray-50 hover:bg-gray-50">
                      <td className="py-2 pr-4 font-mono text-xs">{r.reference_no ?? r.id?.slice(0, 8)}</td>
                      <td className="py-2 pr-4 text-gray-600">{r.transaction_type ?? r.type}</td>
                      <td className="py-2 pr-4 text-gray-600">{r.counterparty_name ?? r.counterparty_entity_id?.slice(0, 8)}</td>
                      <td className="py-2 pr-4 font-medium">{formatRupiah(r.amount)}</td>
                      <td className="py-2 pr-4 text-gray-500 text-xs">{r.journal_date ? formatDate(r.journal_date) : ''}</td>
                      <td className="py-2 pr-4"><Badge status={r.status} /></td>
                      <td className="py-2"><button onClick={() => setSelected(r)} className="text-xs text-blue-600 hover:underline">Kelola</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
      </Card>

      {/* Action modal */}
      {selected && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl p-6 w-80 shadow-xl space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="text-lg font-bold text-gray-900 flex items-center gap-2">
                <ArrowRightLeft className="h-4 w-4" /> Kelola Transaksi
              </h3>
              <button onClick={() => setSelected(null)} className="text-gray-400 text-xl">×</button>
            </div>
            <div className="text-sm text-gray-600 space-y-1">
              <p>Jumlah: <strong>{formatRupiah(selected.amount)}</strong></p>
              <p>Status: <Badge status={selected.status} /></p>
            </div>
            <div className="space-y-2">
              {selected.status === 'draft' && <button onClick={() => submitM.mutate()} disabled={submitM.isPending} className="btn-primary w-full text-sm">{submitM.isPending ? '...' : 'Submit'}</button>}
              {selected.status === 'submitted' && <button onClick={() => confirmM.mutate()} disabled={confirmM.isPending} className="btn-primary w-full text-sm">{confirmM.isPending ? '...' : 'Konfirmasi'}</button>}
              {selected.status === 'confirmed' && <button onClick={() => postM.mutate()} disabled={postM.isPending} className="btn-primary w-full text-sm">{postM.isPending ? '...' : 'Posting GL'}</button>}
              {selected.status === 'posted' && <button onClick={() => settleM.mutate()} disabled={settleM.isPending} className="btn-primary w-full text-sm">{settleM.isPending ? '...' : 'Selesaikan (Settle)'}</button>}
              {['draft', 'submitted'].includes(selected.status) && (
                <button onClick={() => cancelM.mutate()} disabled={cancelM.isPending} className="w-full text-sm text-red-600 border border-red-300 rounded-lg py-2 hover:bg-red-50">
                  {cancelM.isPending ? '...' : 'Batalkan'}
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Create modal */}
      {showCreate && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl p-6 w-96 shadow-xl space-y-4">
            <h3 className="text-lg font-bold text-gray-900">Transaksi Intercompany Baru</h3>
            <div>
              <label className="form-label">Jenis Transaksi *</label>
              <select value={form.transaction_type_id} onChange={e => setForm(f => ({ ...f, transaction_type_id: e.target.value }))} className="form-select w-full">
                <option value="">Pilih...</option>
                {types.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
              </select>
            </div>
            <div>
              <label className="form-label">Counterparty Entity ID</label>
              <input value={form.counterparty_entity_id} onChange={e => setForm(f => ({ ...f, counterparty_entity_id: e.target.value }))} className="form-input w-full" placeholder="UUID entity tujuan" />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="form-label">Jumlah</label>
                <input type="number" value={form.amount} onChange={e => setForm(f => ({ ...f, amount: e.target.value }))} className="form-input w-full" min={0} />
              </div>
              <div>
                <label className="form-label">Tanggal</label>
                <input type="date" value={form.journal_date} onChange={e => setForm(f => ({ ...f, journal_date: e.target.value }))} className="form-input w-full" />
              </div>
            </div>
            <div>
              <label className="form-label">Deskripsi</label>
              <input value={form.description} onChange={e => setForm(f => ({ ...f, description: e.target.value }))} className="form-input w-full" />
            </div>
            <div className="flex gap-3 justify-end">
              <button onClick={() => setShowCreate(false)} className="btn-secondary">Batal</button>
              <button onClick={() => createMutation.mutate()} disabled={createMutation.isPending || !form.transaction_type_id} className="btn-primary">
                {createMutation.isPending ? 'Membuat...' : 'Buat'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
