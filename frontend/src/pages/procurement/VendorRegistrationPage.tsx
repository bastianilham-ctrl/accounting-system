import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Building2 } from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext'
import api from '../../lib/api'
import { Card } from '../../components/ui/Card'
import Spinner from '../../components/ui/Spinner'
import EmptyState from '../../components/ui/EmptyState'
import Badge from '../../components/ui/Badge'
import { formatDate } from '../../lib/utils'
import { showToast } from '../../components/ui/Toast'


const BLANK_FORM = {
  vendor_name: '', legal_name: '', tax_id: '', business_type: 'supplier',
  address: '', city: '', province: '', phone: '', email: '', website: '',
  bank_name: '', bank_account_no: '', bank_account_name: '',
  pic_name: '', pic_phone: '', pic_email: '',
}

export default function VendorRegistrationPage() {
  const { entityId, user } = useAuth()
  const qc = useQueryClient()
  const [showForm, setShowForm] = useState(false)
  const [selected, setSelected] = useState<any>(null)
  const [reviewNote, setReviewNote] = useState('')
  const [form, setForm] = useState(BLANK_FORM)

  const { data, isLoading } = useQuery({
    queryKey: ['vendor-regs', entityId],
    queryFn: () => api.get('/vendor-registration/', { params: { entity_id: entityId } }).then(r => r.data),
    enabled: !!entityId,
  })

  const rows: any[] = Array.isArray(data) ? data : (data?.items ?? [])
  const invalidate = () => qc.invalidateQueries({ queryKey: ['vendor-regs'] })

  const createMutation = useMutation({
    mutationFn: () => api.post('/vendor-registration/', { entity_id: entityId, ...form, submitted_by: user?.username }),
    onSuccess: () => { showToast('Registrasi vendor dibuat'); setShowForm(false); setForm(BLANK_FORM); invalidate() },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? 'Gagal buat registrasi', 'error'),
  })

  const doAction = (path: string, body: any = {}) =>
    api.post(`/vendor-registration/${selected?.id}${path}`, body)
      .then(() => { showToast('Berhasil'); setSelected(null); setReviewNote(''); invalidate() })
      .catch((e: any) => showToast(e?.response?.data?.detail ?? 'Gagal', 'error'))

  const submitMutation = useMutation({ mutationFn: () => doAction('/submit', { submitted_by: user?.username }) })
  const internalMutation = useMutation({ mutationFn: () => doAction('/review/internal', { reviewer: user?.username, decision: 'approved', notes: reviewNote }) })
  const bankingMutation = useMutation({ mutationFn: () => doAction('/review/banking', { reviewer: user?.username, decision: 'approved', notes: reviewNote }) })
  const approveL1Mutation = useMutation({ mutationFn: () => doAction('/review/approve-l1', { approved_by: user?.username, notes: reviewNote }) })
  const approveL2Mutation = useMutation({ mutationFn: () => doAction('/review/approve-l2', { approved_by: user?.username, notes: reviewNote }) })

  const f = (k: keyof typeof BLANK_FORM) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm(f => ({ ...f, [k]: e.target.value }))

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Registrasi Vendor</h1>
          <p className="text-sm text-gray-500 mt-0.5">Onboarding & approval vendor baru</p>
        </div>
        <button onClick={() => setShowForm(true)} className="btn-primary"><Plus className="h-4 w-4" /> Daftarkan Vendor</button>
      </div>

      <Card>
        {isLoading ? <div className="py-8 flex justify-center"><Spinner /></div>
          : rows.length === 0
            ? <EmptyState title="Belum ada registrasi vendor" description="Klik 'Daftarkan Vendor' untuk memulai proses onboarding." />
            : (
              <table className="min-w-full text-sm">
                <thead><tr className="border-b border-gray-200">
                  {['Nama Vendor', 'Jenis', 'Email', 'Kota', 'Status', 'Tgl Daftar', 'Aksi'].map(h => (
                    <th key={h} className="text-left py-2 pr-4 font-medium text-gray-600">{h}</th>
                  ))}
                </tr></thead>
                <tbody>
                  {rows.map((r: any) => (
                    <tr key={r.id} className="border-b border-gray-50 hover:bg-gray-50">
                      <td className="py-2 pr-4 font-medium flex items-center gap-2">
                        <Building2 className="h-4 w-4 text-gray-400 flex-shrink-0" /> {r.vendor_name}
                      </td>
                      <td className="py-2 pr-4 text-gray-500">{r.business_type}</td>
                      <td className="py-2 pr-4 text-gray-500">{r.email}</td>
                      <td className="py-2 pr-4 text-gray-500">{r.city}</td>
                      <td className="py-2 pr-4"><Badge status={r.status} /></td>
                      <td className="py-2 pr-4 text-gray-500 text-xs">{r.created_at ? formatDate(r.created_at) : ''}</td>
                      <td className="py-2"><button onClick={() => setSelected(r)} className="text-xs text-blue-600 hover:underline">Kelola</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
      </Card>

      {/* Action modal for selected registration */}
      {selected && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl p-6 w-96 shadow-xl space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="text-lg font-bold text-gray-900">{selected.vendor_name}</h3>
              <button onClick={() => setSelected(null)} className="text-gray-400 hover:text-gray-600 text-xl">×</button>
            </div>
            <p className="text-sm text-gray-500">Status: <Badge status={selected.status} /></p>
            <div>
              <label className="form-label">Catatan Review</label>
              <input type="text" value={reviewNote} onChange={e => setReviewNote(e.target.value)} className="form-input w-full" placeholder="Opsional" />
            </div>
            <div className="space-y-2">
              {selected.status === 'draft' && (
                <button onClick={() => submitMutation.mutate()} disabled={submitMutation.isPending} className="btn-primary w-full text-sm">
                  {submitMutation.isPending ? 'Proses...' : 'Submit untuk Review'}
                </button>
              )}
              {selected.status === 'submitted' && (
                <button onClick={() => internalMutation.mutate()} disabled={internalMutation.isPending} className="btn-primary w-full text-sm">
                  {internalMutation.isPending ? 'Proses...' : 'Review Internal → Approve'}
                </button>
              )}
              {selected.status === 'internal_review' && (
                <button onClick={() => bankingMutation.mutate()} disabled={bankingMutation.isPending} className="btn-primary w-full text-sm">
                  {bankingMutation.isPending ? 'Proses...' : 'Review Banking → Approve'}
                </button>
              )}
              {selected.status === 'banking_review' && (
                <button onClick={() => approveL1Mutation.mutate()} disabled={approveL1Mutation.isPending} className="btn-primary w-full text-sm">
                  {approveL1Mutation.isPending ? 'Proses...' : 'Approve Level 1'}
                </button>
              )}
              {selected.status === 'approved_l1' && (
                <button onClick={() => approveL2Mutation.mutate()} disabled={approveL2Mutation.isPending} className="btn-primary w-full text-sm">
                  {approveL2Mutation.isPending ? 'Proses...' : 'Approve Level 2 (Final)'}
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Create form modal */}
      {showForm && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 overflow-auto">
          <div className="bg-white rounded-xl p-6 w-[36rem] shadow-xl my-8">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-bold text-gray-900">Daftarkan Vendor Baru</h3>
              <button onClick={() => setShowForm(false)} className="text-gray-400 hover:text-gray-600 text-xl">×</button>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="col-span-2">
                <label className="form-label">Nama Vendor *</label>
                <input value={form.vendor_name} onChange={f('vendor_name')} className="form-input w-full" placeholder="PT / CV / UD ..." />
              </div>
              <div>
                <label className="form-label">Nama Legal</label>
                <input value={form.legal_name} onChange={f('legal_name')} className="form-input w-full" />
              </div>
              <div>
                <label className="form-label">NPWP</label>
                <input value={form.tax_id} onChange={f('tax_id')} className="form-input w-full" />
              </div>
              <div>
                <label className="form-label">Jenis Bisnis</label>
                <select value={form.business_type} onChange={f('business_type')} className="form-select w-full">
                  {['supplier', 'contractor', 'consultant', 'service_provider'].map(t => <option key={t} value={t}>{t}</option>)}
                </select>
              </div>
              <div>
                <label className="form-label">Kota</label>
                <input value={form.city} onChange={f('city')} className="form-input w-full" />
              </div>
              <div>
                <label className="form-label">Telepon</label>
                <input value={form.phone} onChange={f('phone')} className="form-input w-full" />
              </div>
              <div>
                <label className="form-label">Email</label>
                <input type="email" value={form.email} onChange={f('email')} className="form-input w-full" />
              </div>
              <div>
                <label className="form-label">Nama Bank</label>
                <input value={form.bank_name} onChange={f('bank_name')} className="form-input w-full" />
              </div>
              <div>
                <label className="form-label">No. Rekening</label>
                <input value={form.bank_account_no} onChange={f('bank_account_no')} className="form-input w-full" />
              </div>
              <div>
                <label className="form-label">Nama Pemegang</label>
                <input value={form.bank_account_name} onChange={f('bank_account_name')} className="form-input w-full" />
              </div>
              <div>
                <label className="form-label">Nama PIC</label>
                <input value={form.pic_name} onChange={f('pic_name')} className="form-input w-full" />
              </div>
              <div>
                <label className="form-label">Email PIC</label>
                <input type="email" value={form.pic_email} onChange={f('pic_email')} className="form-input w-full" />
              </div>
            </div>
            <div className="flex gap-3 justify-end mt-4">
              <button onClick={() => setShowForm(false)} className="btn-secondary">Batal</button>
              <button onClick={() => createMutation.mutate()} disabled={createMutation.isPending || !form.vendor_name} className="btn-primary">
                {createMutation.isPending ? 'Menyimpan...' : 'Daftarkan'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
