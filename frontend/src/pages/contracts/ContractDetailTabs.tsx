import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, CheckCircle, FileText, CreditCard } from 'lucide-react'
import api from '../../lib/api'
import { useAuth } from '../../contexts/AuthContext'
import Badge from '../../components/ui/Badge'
import Spinner from '../../components/ui/Spinner'
import EmptyState from '../../components/ui/EmptyState'
import { formatRupiah, formatDate } from '../../lib/utils'
import { showToast } from '../../components/ui/Toast'

type SubTab = 'milestones' | 'invoices' | 'amendments'

const BLANK_MS = { name: '', description: '', percentage: '', planned_date: '' }

export function ContractDetailTabs({ contract }: { contract: any }) {
  const { user } = useAuth()
  const qc = useQueryClient()
  const [subTab, setSubTab] = useState<SubTab>('milestones')
  const [showMsForm, setShowMsForm] = useState(false)
  const [msForm, setMsForm] = useState(BLANK_MS)

  const inval = (key: string) => qc.invalidateQueries({ queryKey: [key, contract.id] })

  const { data: milestones, isLoading: msLoading } = useQuery({
    queryKey: ['contract-milestones', contract.id],
    queryFn: () => api.get(`/contracts/${contract.id}/milestones`).then(r => r.data),
  })

  const { data: detail } = useQuery({
    queryKey: ['contract-detail', contract.id],
    queryFn: () => api.get(`/contracts/${contract.id}`).then(r => r.data),
  })

  const msList: any[] = Array.isArray(milestones) ? milestones : (milestones?.milestones ?? [])
  const invoices: any[] = detail?.invoices ?? []
  const amendments: any[] = detail?.amendments ?? []

  const addMsMutation = useMutation({
    mutationFn: () => api.post(`/contracts/${contract.id}/milestones`, {
      ...msForm, percentage: +msForm.percentage,
    }),
    onSuccess: () => { showToast('Milestone ditambahkan'); setShowMsForm(false); setMsForm(BLANK_MS); inval('contract-milestones'); inval('contract-detail') },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? 'Gagal', 'error'),
  })

  const bastMutation = useMutation({
    mutationFn: ({ msId }: { msId: string }) =>
      api.post(`/contracts/${contract.id}/milestones/${msId}/complete-bast`, {
        bast_date: new Date().toISOString().slice(0, 10),
        completed_by: user?.username,
      }),
    onSuccess: () => { showToast('BAST dikonfirmasi'); inval('contract-milestones') },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? 'Gagal', 'error'),
  })

  const createInvMutation = useMutation({
    mutationFn: ({ msId }: { msId: string }) =>
      api.post(`/contracts/${contract.id}/milestones/${msId}/create-invoice`, {
        invoice_date: new Date().toISOString().slice(0, 10),
        created_by: user?.username,
      }),
    onSuccess: () => { showToast('Invoice dibuat dari milestone'); inval('contract-detail') },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? 'Gagal', 'error'),
  })

  const sendInvMutation = useMutation({
    mutationFn: (invId: string) => api.put(`/contracts/invoices/${invId}/send`),
    onSuccess: () => { showToast('Invoice ditandai SENT'); inval('contract-detail') },
  })

  const payInvMutation = useMutation({
    mutationFn: (invId: string) => api.post(`/contracts/invoices/${invId}/record-payment`, {
      payment_date: new Date().toISOString().slice(0, 10),
      amount: 0, notes: 'Recorded from UI',
    }),
    onSuccess: () => { showToast('Pembayaran dicatat'); inval('contract-detail') },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? 'Gagal', 'error'),
  })

  const mf = (k: keyof typeof BLANK_MS) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
    setMsForm(p => ({ ...p, [k]: e.target.value }))

  const SUBTABS: { key: SubTab; label: string }[] = [
    { key: 'milestones', label: `Milestone (${msList.length})` },
    { key: 'invoices', label: `Invoice (${invoices.length})` },
    { key: 'amendments', label: `Amendment (${amendments.length})` },
  ]

  return (
    <div className="space-y-3 mt-2">
      {/* Sub-tabs */}
      <div className="flex gap-1 border-b border-gray-200">
        {SUBTABS.map(t => (
          <button key={t.key} onClick={() => setSubTab(t.key)}
            className={`px-3 py-1.5 text-xs font-medium border-b-2 -mb-px transition-colors ${subTab === t.key ? 'border-primary-500 text-primary-600' : 'border-transparent text-gray-500 hover:text-gray-700'}`}>
            {t.label}
          </button>
        ))}
      </div>

      {/* Milestones */}
      {subTab === 'milestones' && (
        <div className="space-y-2">
          {contract.status === 'ACTIVE' && (
            <div className="flex justify-end">
              <button onClick={() => setShowMsForm(true)} className="btn-secondary text-xs"><Plus className="h-3.5 w-3.5" /> Tambah Milestone</button>
            </div>
          )}
          {msLoading ? <Spinner /> : msList.length === 0
            ? <EmptyState title="Belum ada milestone" description="Tambah milestone untuk mulai billing." />
            : (
              <table className="min-w-full text-xs">
                <thead><tr className="border-b border-gray-200">
                  {['Milestone', '%', 'Tgl Rencana', 'BAST', 'Status', 'Aksi'].map(h => (
                    <th key={h} className="text-left py-1.5 pr-3 font-medium text-gray-600">{h}</th>
                  ))}
                </tr></thead>
                <tbody>
                  {msList.map((ms: any) => (
                    <tr key={ms.id} className="border-b border-gray-50 hover:bg-gray-50">
                      <td className="py-1.5 pr-3 font-medium">{ms.name}</td>
                      <td className="py-1.5 pr-3 text-center">{ms.percentage}%</td>
                      <td className="py-1.5 pr-3">{ms.planned_date ? formatDate(ms.planned_date) : '—'}</td>
                      <td className="py-1.5 pr-3">{ms.bast_date ? formatDate(ms.bast_date) : '—'}</td>
                      <td className="py-1.5 pr-3"><Badge status={ms.status ?? 'pending'} /></td>
                      <td className="py-1.5 flex gap-2">
                        {ms.status === 'pending' && (
                          <button onClick={() => bastMutation.mutate({ msId: ms.id })}
                            className="text-xs text-blue-600 hover:underline flex items-center gap-0.5">
                            <CheckCircle className="h-3 w-3" /> BAST
                          </button>
                        )}
                        {ms.status === 'bast_completed' && !ms.invoice_id && (
                          <button onClick={() => createInvMutation.mutate({ msId: ms.id })}
                            className="text-xs text-green-600 hover:underline flex items-center gap-0.5">
                            <FileText className="h-3 w-3" /> Invoice
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}

          {/* Add milestone form */}
          {showMsForm && (
            <div className="border rounded-xl p-3 bg-gray-50 space-y-2 mt-2">
              <p className="text-xs font-semibold text-gray-700">Tambah Milestone</p>
              <div className="grid grid-cols-2 gap-2">
                <div><label className="form-label">Nama *</label>
                  <input value={msForm.name} onChange={mf('name')} className="form-input w-full text-xs" /></div>
                <div><label className="form-label">Persentase (%) *</label>
                  <input type="number" value={msForm.percentage} onChange={mf('percentage')} min={1} max={100} className="form-input w-full text-xs" /></div>
                <div><label className="form-label">Tgl Rencana</label>
                  <input type="date" value={msForm.planned_date} onChange={mf('planned_date')} className="form-input w-full text-xs" /></div>
                <div><label className="form-label">Deskripsi</label>
                  <input value={msForm.description} onChange={mf('description')} className="form-input w-full text-xs" /></div>
              </div>
              <div className="flex gap-2 justify-end">
                <button onClick={() => { setShowMsForm(false); setMsForm(BLANK_MS) }} className="btn-secondary text-xs">Batal</button>
                <button onClick={() => addMsMutation.mutate()}
                  disabled={addMsMutation.isPending || !msForm.name || !msForm.percentage}
                  className="btn-primary text-xs">{addMsMutation.isPending ? '...' : 'Simpan'}</button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Invoices */}
      {subTab === 'invoices' && (
        invoices.length === 0
          ? <EmptyState title="Belum ada invoice" description="Invoice dibuat dari milestone yang sudah BAST." />
          : (
            <table className="min-w-full text-xs">
              <thead><tr className="border-b border-gray-200">
                {['No. Invoice', 'Jumlah', 'Tgl Invoice', 'Jatuh Tempo', 'Status', 'Aksi'].map(h => (
                  <th key={h} className="text-left py-1.5 pr-3 font-medium text-gray-600">{h}</th>
                ))}
              </tr></thead>
              <tbody>
                {invoices.map((inv: any) => (
                  <tr key={inv.id} className="border-b border-gray-50 hover:bg-gray-50">
                    <td className="py-1.5 pr-3 font-mono">{inv.invoice_no}</td>
                    <td className="py-1.5 pr-3 font-medium">{formatRupiah(inv.amount)}</td>
                    <td className="py-1.5 pr-3">{formatDate(inv.invoice_date)}</td>
                    <td className="py-1.5 pr-3">{inv.due_date ? formatDate(inv.due_date) : '—'}</td>
                    <td className="py-1.5 pr-3"><Badge status={inv.status} /></td>
                    <td className="py-1.5 flex gap-2">
                      {inv.status === 'DRAFT' && <button onClick={() => sendInvMutation.mutate(inv.id)} className="text-xs text-blue-600 hover:underline">Kirim</button>}
                      {inv.status === 'SENT' && <button onClick={() => payInvMutation.mutate(inv.id)} className="text-xs text-green-600 hover:underline flex items-center gap-0.5"><CreditCard className="h-3 w-3" /> Bayar</button>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )
      )}

      {/* Amendments */}
      {subTab === 'amendments' && (
        amendments.length === 0
          ? <EmptyState title="Belum ada amendment" description="Amendment akan muncul jika ada perubahan kontrak." />
          : (
            <table className="min-w-full text-xs">
              <thead><tr className="border-b border-gray-200">
                {['No. Amandemen', 'Tipe', 'Perubahan Nilai', 'Tgl Efektif', 'Status'].map(h => (
                  <th key={h} className="text-left py-1.5 pr-3 font-medium text-gray-600">{h}</th>
                ))}
              </tr></thead>
              <tbody>
                {amendments.map((am: any) => (
                  <tr key={am.id} className="border-b border-gray-50">
                    <td className="py-1.5 pr-3 font-mono">{am.amendment_number}</td>
                    <td className="py-1.5 pr-3">{am.amendment_type}</td>
                    <td className="py-1.5 pr-3">{am.value_change ? formatRupiah(am.value_change) : '—'}</td>
                    <td className="py-1.5 pr-3">{am.effective_date ? formatDate(am.effective_date) : '—'}</td>
                    <td className="py-1.5 pr-3"><Badge status={am.status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )
      )}
    </div>
  )
}
