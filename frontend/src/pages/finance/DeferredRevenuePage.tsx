import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { TrendingDown, CheckCircle, DollarSign, Search } from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext'
import api from '../../lib/api'
import { Card } from '../../components/ui/Card'
import Spinner from '../../components/ui/Spinner'
import EmptyState from '../../components/ui/EmptyState'
import Badge from '../../components/ui/Badge'
import { formatRupiah, formatDate } from '../../lib/utils'
import { showToast } from '../../components/ui/Toast'

const BLANK_PAY = { amount: '', payment_date: '', bank_account_code: '1-1-001', notes: '' }

export default function DeferredRevenuePage() {
  const { entityId, user } = useAuth()
  const qc = useQueryClient()
  const [search, setSearch] = useState('')
  const [selectedProject, setSelectedProject] = useState<any>(null)
  const [payTarget, setPayTarget] = useState<any>(null)
  const [payForm, setPayForm] = useState(BLANK_PAY)

  const { data: projects, isLoading: projLoading } = useQuery({
    queryKey: ['projects-for-dr', entityId],
    queryFn: () => api.get('/projects', { params: { entity_id: entityId } }).then(r => r.data),
    enabled: !!entityId,
  })

  const { data: summary, isLoading: sumLoading, refetch: refetchSum } = useQuery({
    queryKey: ['deferred-revenue-summary', selectedProject?.id],
    queryFn: () => api.get(`/deferred-revenue/projects/${selectedProject.id}/summary`).then(r => r.data),
    enabled: !!selectedProject?.id,
  })

  const projectList: any[] = (Array.isArray(projects) ? projects : (projects?.items ?? []))
    .filter((p: any) => !search || p.project_name?.toLowerCase().includes(search.toLowerCase()) || p.project_code?.toLowerCase().includes(search.toLowerCase()))

  const milestones: any[] = Array.isArray(summary?.milestones) ? summary.milestones : []
  const totals = summary?.totals ?? {}

  const inval = () => {
    qc.invalidateQueries({ queryKey: ['deferred-revenue-summary'] })
  }

  const payMutation = useMutation({
    mutationFn: (msId: string) => api.post(`/deferred-revenue/milestones/${msId}/payment`, {
      amount: +payForm.amount,
      payment_date: payForm.payment_date,
      bank_account_code: payForm.bank_account_code,
      notes: payForm.notes || null,
    }),
    onSuccess: () => {
      showToast('Penerimaan termin dicatat — Dr Bank / Cr Deferred Revenue')
      setPayTarget(null); setPayForm(BLANK_PAY); inval()
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? 'Gagal mencatat pembayaran', 'error'),
  })

  const recognizeMutation = useMutation({
    mutationFn: (msId: string) => api.post(`/deferred-revenue/milestones/${msId}/recognize`, {
      recognition_date: new Date().toISOString().slice(0, 10),
    }),
    onSuccess: () => {
      showToast('Revenue direkognisi — Dr Deferred Revenue / Cr Revenue')
      inval()
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? 'Gagal rekognisi', 'error'),
  })

  const pf = (k: keyof typeof BLANK_PAY) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setPayForm(p => ({ ...p, [k]: e.target.value }))

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Deferred Revenue</h1>
        <p className="text-sm text-gray-500 mt-0.5">Kelola penerimaan termin & rekognisi revenue per milestone proyek</p>
      </div>

      {/* Summary cards when project selected */}
      {selectedProject && summary && (
        <div className="grid grid-cols-4 gap-3">
          {[
            { label: 'Total Billing', value: totals.total_billing ?? 0, color: 'text-gray-700', bg: 'bg-gray-50' },
            { label: 'Sudah Diterima', value: totals.total_paid ?? 0, color: 'text-green-700', bg: 'bg-green-50' },
            { label: 'Deferred (Belum Direkognisi)', value: totals.total_deferred ?? 0, color: 'text-blue-700', bg: 'bg-blue-50' },
            { label: 'Sudah Direkognisi', value: totals.total_recognized ?? 0, color: 'text-purple-700', bg: 'bg-purple-50' },
          ].map(c => (
            <Card key={c.label} className={c.bg}>
              <p className="text-xs text-gray-500">{c.label}</p>
              <p className={`text-lg font-bold mt-0.5 ${c.color}`}>{formatRupiah(c.value)}</p>
            </Card>
          ))}
        </div>
      )}

      <div className="grid grid-cols-5 gap-4 h-[calc(100vh-280px)]">
        {/* Project list */}
        <div className="col-span-2 flex flex-col gap-2">
          <div className="relative">
            <Search className="absolute left-3 top-2.5 h-4 w-4 text-gray-400" />
            <input value={search} onChange={e => setSearch(e.target.value)}
              placeholder="Cari proyek..." className="form-input pl-9 w-full" />
          </div>
          <div className="overflow-y-auto space-y-2 flex-1">
            {projLoading ? <div className="py-8 flex justify-center"><Spinner /></div>
              : projectList.length === 0
                ? <EmptyState title="Tidak ada proyek" description="Proyek ditemukan dari modul Project Management." />
                : projectList.map((p: any) => (
                  <div key={p.id} onClick={() => setSelectedProject(p)}
                    className={`border rounded-xl p-3 cursor-pointer transition-colors ${selectedProject?.id === p.id ? 'border-primary-500 bg-primary-50' : 'border-gray-200 bg-white hover:border-gray-300'}`}>
                    <p className="font-medium text-sm">{p.project_name}</p>
                    <div className="flex items-center gap-2 mt-0.5">
                      <p className="text-xs text-gray-500">{p.project_code}</p>
                      <Badge status={p.status ?? 'active'} />
                    </div>
                    {p.contract_value && (
                      <p className="text-xs text-primary-600 mt-1 font-medium">{formatRupiah(p.contract_value)}</p>
                    )}
                  </div>
                ))}
          </div>
        </div>

        {/* Milestone detail */}
        <div className="col-span-3 overflow-y-auto">
          {!selectedProject ? (
            <Card className="h-full flex items-center justify-center">
              <div className="text-center text-gray-400">
                <TrendingDown className="h-10 w-10 mx-auto mb-2 opacity-30" />
                <p className="text-sm">Pilih proyek untuk melihat deferred revenue</p>
              </div>
            </Card>
          ) : sumLoading ? (
            <Card className="py-12 flex justify-center"><Spinner /></Card>
          ) : milestones.length === 0 ? (
            <Card>
              <EmptyState title="Belum ada data milestone" description="Milestone dikelola di Contract Tracker." />
            </Card>
          ) : (
            <Card className="space-y-2">
              <div className="flex items-center gap-2 mb-3">
                <TrendingDown className="h-4 w-4 text-blue-500" />
                <h2 className="font-semibold text-gray-800">{selectedProject.project_name} — Detail per Milestone</h2>
              </div>
              <table className="min-w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-200">
                    {['Milestone', '%', 'Billing', 'Diterima', 'Deferred', 'Direkognisi', 'Status', 'Aksi'].map(h => (
                      <th key={h} className="text-left py-2 pr-3 text-xs font-medium text-gray-600">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {milestones.map((ms: any) => {
                    const deferred = (ms.paid_amount ?? 0) - (ms.recognized_amount ?? 0)
                    const canRecognize = (ms.bast_completed || ms.status === 'bast_completed') && deferred > 0
                    const canPay = (ms.billing_amount ?? 0) > (ms.paid_amount ?? 0)

                    return (
                      <tr key={ms.id} className="border-b border-gray-50 hover:bg-gray-50">
                        <td className="py-2 pr-3 font-medium text-xs max-w-[100px] truncate">{ms.name}</td>
                        <td className="py-2 pr-3 text-center text-xs">{ms.percentage}%</td>
                        <td className="py-2 pr-3 text-xs">{formatRupiah(ms.billing_amount ?? 0)}</td>
                        <td className="py-2 pr-3 text-xs text-green-700">{formatRupiah(ms.paid_amount ?? 0)}</td>
                        <td className="py-2 pr-3 text-xs text-blue-700 font-medium">{formatRupiah(deferred > 0 ? deferred : 0)}</td>
                        <td className="py-2 pr-3 text-xs text-purple-700">{formatRupiah(ms.recognized_amount ?? 0)}</td>
                        <td className="py-2 pr-3"><Badge status={ms.dr_status ?? ms.status ?? 'pending'} /></td>
                        <td className="py-2 pr-3">
                          <div className="flex flex-col gap-1">
                            {canPay && (
                              <button onClick={() => setPayTarget(ms)}
                                className="text-[10px] text-green-600 hover:underline flex items-center gap-0.5">
                                <DollarSign className="h-3 w-3" /> Terima
                              </button>
                            )}
                            {canRecognize && (
                              <button onClick={() => recognizeMutation.mutate(ms.id)}
                                disabled={recognizeMutation.isPending}
                                className="text-[10px] text-purple-600 hover:underline flex items-center gap-0.5">
                                <CheckCircle className="h-3 w-3" /> Rekognisi
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>

              {/* GL Legend */}
              <div className="mt-3 pt-3 border-t border-gray-100 text-xs text-gray-400 space-y-0.5">
                <p>Terima pembayaran termin: <strong>Dr Bank → Cr Deferred Revenue</strong></p>
                <p>Rekognisi revenue: <strong>Dr Deferred Revenue → Cr Revenue</strong> (proporsional % milestone)</p>
              </div>
            </Card>
          )}
        </div>
      </div>

      {/* Record Payment modal */}
      {payTarget && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl p-6 w-[26rem] shadow-xl space-y-4">
            <div className="flex items-center gap-2 text-green-700">
              <DollarSign className="h-5 w-5" />
              <h3 className="font-bold">Catat Penerimaan Termin</h3>
            </div>
            <p className="text-xs text-gray-500">
              Milestone: <strong>{payTarget.name}</strong> —
              Billing: <strong>{formatRupiah(payTarget.billing_amount ?? 0)}</strong> —
              Sudah diterima: <strong>{formatRupiah(payTarget.paid_amount ?? 0)}</strong>
            </p>
            <div>
              <label className="form-label">Jumlah Diterima *</label>
              <input type="number" value={payForm.amount} onChange={pf('amount')} className="form-input w-full" min={0} />
            </div>
            <div>
              <label className="form-label">Tanggal Penerimaan *</label>
              <input type="date" value={payForm.payment_date} onChange={pf('payment_date')} className="form-input w-full" />
            </div>
            <div>
              <label className="form-label">Kode Akun Bank</label>
              <input value={payForm.bank_account_code} onChange={pf('bank_account_code')} className="form-input w-full" placeholder="1-1-001" />
            </div>
            <div>
              <label className="form-label">Catatan</label>
              <input value={payForm.notes} onChange={pf('notes')} className="form-input w-full" />
            </div>
            <p className="text-xs text-gray-400">Jurnal: Dr {payForm.bank_account_code || '1-1-001'} → Cr Deferred Revenue</p>
            <div className="flex gap-3 justify-end">
              <button onClick={() => { setPayTarget(null); setPayForm(BLANK_PAY) }} className="btn-secondary">Batal</button>
              <button onClick={() => payMutation.mutate(payTarget.id)}
                disabled={payMutation.isPending || !payForm.amount || !payForm.payment_date}
                className="btn-primary">{payMutation.isPending ? 'Menyimpan...' : 'Catat Penerimaan'}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
