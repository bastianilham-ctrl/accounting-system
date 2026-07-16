import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, FileCheck, AlertTriangle, DollarSign, BarChart2 } from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext'
import api from '../../lib/api'
import { Card } from '../../components/ui/Card'
import Spinner from '../../components/ui/Spinner'
import EmptyState from '../../components/ui/EmptyState'
import Badge from '../../components/ui/Badge'
import { formatRupiah, formatDate } from '../../lib/utils'
import { showToast } from '../../components/ui/Toast'
import { ContractDetailTabs } from './ContractDetailTabs'

type MainTab = 'list' | 'ar-aging' | 'expiry'
const BLANK = {
  project_id: '', client_id: '', contract_number: '', contract_title: '',
  total_value: '', currency: 'IDR', term_of_payment_days: '30',
  retention_pct: '0', start_date: '', end_date: '', po_number: '',
  scope_summary: '', notes: '',
}

function StatCard({ label, value, icon, color }: { label: string; value: string | number; icon: React.ReactNode; color: string }) {
  return (
    <Card className="flex items-center gap-3">
      <div className={`rounded-xl p-2.5 ${color}`}>{icon}</div>
      <div><p className="text-xl font-bold text-gray-900">{value}</p><p className="text-xs text-gray-500">{label}</p></div>
    </Card>
  )
}

export default function ContractPage() {
  const { entityId, user } = useAuth()
  const qc = useQueryClient()
  const [mainTab, setMainTab] = useState<MainTab>('list')
  const [selected, setSelected] = useState<any>(null)
  const [showCreate, setShowCreate] = useState(false)
  const [form, setForm] = useState(BLANK)

  const { data, isLoading } = useQuery({
    queryKey: ['contracts', entityId],
    queryFn: () => api.get('/contracts', { params: { entity_id: entityId } }).then(r => r.data),
    enabled: !!entityId && mainTab === 'list',
  })

  const { data: arAging } = useQuery({
    queryKey: ['contract-ar-aging', entityId],
    queryFn: () => api.get(`/contracts/entity/${entityId}/ar-aging`).then(r => r.data),
    enabled: !!entityId && mainTab === 'ar-aging',
  })

  const { data: expiry } = useQuery({
    queryKey: ['contract-expiry', entityId],
    queryFn: () => api.get(`/contracts/entity/${entityId}/expiry-alerts`).then(r => r.data),
    enabled: !!entityId && mainTab === 'expiry',
  })

  const contracts: any[] = Array.isArray(data) ? data : (data?.items ?? [])
  const agingData: any = arAging ?? {}
  const expiryList: any[] = Array.isArray(expiry) ? expiry : (expiry?.items ?? [])

  const invalidate = () => qc.invalidateQueries({ queryKey: ['contracts'] })

  const createMutation = useMutation({
    mutationFn: () => api.post('/contracts', {
      entity_id: entityId, created_by: user?.username,
      ...form, total_value: +form.total_value,
      term_of_payment_days: +form.term_of_payment_days,
      retention_pct: +form.retention_pct,
    }),
    onSuccess: () => { showToast('Kontrak dibuat'); setShowCreate(false); setForm(BLANK); invalidate() },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? 'Gagal', 'error'),
  })

  const activateMutation = useMutation({
    mutationFn: (id: string) => api.post(`/contracts/${id}/activate`, {
      signed_date: new Date().toISOString().slice(0, 10),
      signed_by: user?.username,
    }),
    onSuccess: () => { showToast('Kontrak diaktifkan'); invalidate(); setSelected((s: any) => ({ ...s, status: 'ACTIVE' })) },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? 'Gagal', 'error'),
  })

  const completeMutation = useMutation({
    mutationFn: (id: string) => api.post(`/contracts/${id}/complete`, { completed_by: user?.username }),
    onSuccess: () => { showToast('Kontrak selesai'); invalidate() },
  })

  const f = (k: keyof typeof BLANK) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
    setForm(p => ({ ...p, [k]: e.target.value }))

  const MAIN_TABS: { key: MainTab; label: string; icon: React.ReactNode }[] = [
    { key: 'list', label: 'Daftar Kontrak', icon: <FileCheck className="h-4 w-4" /> },
    { key: 'ar-aging', label: 'AR Aging', icon: <BarChart2 className="h-4 w-4" /> },
    { key: 'expiry', label: 'Alert Expiry', icon: <AlertTriangle className="h-4 w-4" /> },
  ]

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Contract Tracker</h1>
          <p className="text-sm text-gray-500 mt-0.5">Kelola kontrak, milestone billing, invoice klien & AR aging</p>
        </div>
        <button onClick={() => setShowCreate(true)} className="btn-primary"><Plus className="h-4 w-4" /> Buat Kontrak</button>
      </div>

      {/* Main tab bar */}
      <div className="flex gap-1 border-b border-gray-200">
        {MAIN_TABS.map(t => (
          <button key={t.key} onClick={() => { setMainTab(t.key); setSelected(null) }}
            className={`flex items-center gap-2 px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${mainTab === t.key ? 'border-primary-500 text-primary-600' : 'border-transparent text-gray-500 hover:text-gray-700'}`}>
            {t.icon} {t.label}
          </button>
        ))}
      </div>

      {/* List tab */}
      {mainTab === 'list' && (
        <div className="grid grid-cols-5 gap-4 h-[calc(100vh-260px)]">
          {/* Contract list */}
          <div className="col-span-2 overflow-y-auto space-y-2">
            {isLoading ? <div className="py-8 flex justify-center"><Spinner /></div>
              : contracts.length === 0
                ? <EmptyState title="Belum ada kontrak" description="Klik 'Buat Kontrak' untuk mulai." />
                : contracts.map((c: any) => (
                  <div key={c.id} onClick={() => setSelected(c)}
                    className={`border rounded-xl p-3 cursor-pointer transition-colors ${selected?.id === c.id ? 'border-primary-500 bg-primary-50' : 'border-gray-200 bg-white hover:border-gray-300'}`}>
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <p className="font-medium text-sm truncate">{c.contract_title}</p>
                        <p className="text-xs text-gray-500 mt-0.5">{c.contract_number}</p>
                      </div>
                      <Badge status={c.status} />
                    </div>
                    <div className="mt-2 flex gap-3 text-xs text-gray-500">
                      <span className="font-medium text-gray-700">{formatRupiah(c.total_value)}</span>
                      <span>{formatDate(c.start_date)} → {formatDate(c.end_date)}</span>
                    </div>
                  </div>
                ))}
          </div>

          {/* Contract detail */}
          <div className="col-span-3 overflow-y-auto">
            {!selected ? (
              <Card className="h-full flex items-center justify-center">
                <div className="text-center text-gray-400">
                  <FileCheck className="h-10 w-10 mx-auto mb-2 opacity-30" />
                  <p className="text-sm">Pilih kontrak untuk melihat detail</p>
                </div>
              </Card>
            ) : (
              <Card className="space-y-4">
                <div className="flex items-start justify-between">
                  <div>
                    <h2 className="text-lg font-bold">{selected.contract_title}</h2>
                    <p className="text-sm text-gray-500">{selected.contract_number}</p>
                  </div>
                  <div className="flex gap-2 items-center">
                    <Badge status={selected.status} />
                    {selected.status === 'IN_REVIEW' && (
                      <button onClick={() => activateMutation.mutate(selected.id)} className="btn-primary text-xs">Aktifkan</button>
                    )}
                    {selected.status === 'ACTIVE' && (
                      <button onClick={() => completeMutation.mutate(selected.id)} className="btn-secondary text-xs">Selesaikan</button>
                    )}
                  </div>
                </div>

                <div className="grid grid-cols-3 gap-3 text-sm">
                  <div><p className="form-label">Nilai Kontrak</p><p className="font-bold text-primary-600">{formatRupiah(selected.total_value)}</p></div>
                  <div><p className="form-label">Mata Uang</p><p className="font-medium">{selected.currency}</p></div>
                  <div><p className="form-label">Retensi</p><p className="font-medium">{selected.retention_pct}%</p></div>
                  <div><p className="form-label">Mulai</p><p>{formatDate(selected.start_date)}</p></div>
                  <div><p className="form-label">Selesai</p><p>{formatDate(selected.end_date)}</p></div>
                  <div><p className="form-label">Termin</p><p>{selected.term_of_payment_days} hari</p></div>
                </div>
                {selected.scope_summary && <p className="text-sm text-gray-600 bg-gray-50 rounded-lg p-2">{selected.scope_summary}</p>}

                <ContractDetailTabs contract={selected} />
              </Card>
            )}
          </div>
        </div>
      )}

      {/* AR Aging tab */}
      {mainTab === 'ar-aging' && (
        <div className="space-y-4">
          <div className="grid grid-cols-5 gap-3">
            {[
              { label: 'Current', key: 'current', color: 'bg-green-50' },
              { label: '1-30 Hari', key: 'days_1_30', color: 'bg-yellow-50' },
              { label: '31-60 Hari', key: 'days_31_60', color: 'bg-orange-50' },
              { label: '61-90 Hari', key: 'days_61_90', color: 'bg-red-50' },
              { label: '>90 Hari', key: 'days_over_90', color: 'bg-red-100' },
            ].map(b => (
              <Card key={b.key} className={b.color}>
                <p className="text-xs text-gray-500">{b.label}</p>
                <p className="text-lg font-bold mt-1">{formatRupiah(agingData[b.key] ?? 0)}</p>
              </Card>
            ))}
          </div>
          <Card>
            <div className="flex items-center gap-2 mb-3">
              <DollarSign className="h-4 w-4 text-gray-400" />
              <p className="text-sm font-semibold">Total Outstanding: <span className="text-primary-600">{formatRupiah(agingData.total_outstanding ?? 0)}</span></p>
            </div>
            <p className="text-xs text-gray-500">Data diperbarui berdasarkan invoice yang belum lunas per hari ini.</p>
          </Card>
        </div>
      )}

      {/* Expiry alerts tab */}
      {mainTab === 'expiry' && (
        <Card>
          {expiryList.length === 0
            ? <EmptyState title="Tidak ada kontrak yang akan kadaluarsa" description="Kontrak yang kadaluarsa dalam 60 hari akan muncul di sini." />
            : (
              <table className="min-w-full text-sm">
                <thead><tr className="border-b border-gray-200">
                  {['Kontrak', 'Status', 'Nilai', 'Berakhir', 'Sisa Hari'].map(h => (
                    <th key={h} className="text-left py-2 pr-4 text-xs font-medium text-gray-600">{h}</th>
                  ))}
                </tr></thead>
                <tbody>
                  {expiryList.map((c: any) => (
                    <tr key={c.id} className="border-b border-gray-50 hover:bg-yellow-50">
                      <td className="py-2 pr-4">
                        <p className="font-medium">{c.contract_title}</p>
                        <p className="text-xs text-gray-500">{c.contract_number}</p>
                      </td>
                      <td className="py-2 pr-4"><Badge status={c.status} /></td>
                      <td className="py-2 pr-4 font-medium">{formatRupiah(c.total_value)}</td>
                      <td className="py-2 pr-4">{formatDate(c.end_date)}</td>
                      <td className="py-2 pr-4">
                        <span className={`font-bold ${(c.days_remaining ?? 60) <= 14 ? 'text-red-600' : 'text-yellow-600'}`}>
                          {c.days_remaining ?? '—'} hari
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
        </Card>
      )}

      {/* Create contract modal */}
      {showCreate && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 overflow-auto">
          <div className="bg-white rounded-xl p-6 w-[40rem] shadow-xl my-8 space-y-4">
            <h3 className="text-lg font-bold">Buat Kontrak Baru</h3>
            <div className="grid grid-cols-2 gap-3">
              <div className="col-span-2"><label className="form-label">Judul Kontrak *</label>
                <input value={form.contract_title} onChange={f('contract_title')} className="form-input w-full" /></div>
              <div><label className="form-label">No. Kontrak *</label>
                <input value={form.contract_number} onChange={f('contract_number')} className="form-input w-full" /></div>
              <div><label className="form-label">No. PO Klien</label>
                <input value={form.po_number} onChange={f('po_number')} className="form-input w-full" /></div>
              <div><label className="form-label">Project ID</label>
                <input value={form.project_id} onChange={f('project_id')} className="form-input w-full" placeholder="UUID project" /></div>
              <div><label className="form-label">Client ID</label>
                <input value={form.client_id} onChange={f('client_id')} className="form-input w-full" placeholder="UUID klien AR" /></div>
              <div><label className="form-label">Nilai Kontrak (IDR) *</label>
                <input type="number" value={form.total_value} onChange={f('total_value')} className="form-input w-full" min={0} /></div>
              <div><label className="form-label">Retensi (%)</label>
                <input type="number" value={form.retention_pct} onChange={f('retention_pct')} className="form-input w-full" min={0} max={20} /></div>
              <div><label className="form-label">Tgl Mulai *</label>
                <input type="date" value={form.start_date} onChange={f('start_date')} className="form-input w-full" /></div>
              <div><label className="form-label">Tgl Selesai *</label>
                <input type="date" value={form.end_date} onChange={f('end_date')} className="form-input w-full" /></div>
              <div><label className="form-label">Termin Bayar (hari)</label>
                <input type="number" value={form.term_of_payment_days} onChange={f('term_of_payment_days')} className="form-input w-full" /></div>
              <div className="col-span-2"><label className="form-label">Ringkasan Scope</label>
                <textarea value={form.scope_summary} onChange={f('scope_summary')} className="form-input w-full" rows={2} /></div>
            </div>
            <div className="flex gap-3 justify-end">
              <button onClick={() => { setShowCreate(false); setForm(BLANK) }} className="btn-secondary">Batal</button>
              <button onClick={() => createMutation.mutate()}
                disabled={createMutation.isPending || !form.contract_title || !form.contract_number || !form.total_value || !form.start_date || !form.end_date}
                className="btn-primary">{createMutation.isPending ? 'Menyimpan...' : 'Buat Kontrak'}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
