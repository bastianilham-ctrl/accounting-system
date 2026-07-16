import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Send, Mail, BarChart2, Users, Trash2, Eye } from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext'
import api from '../../lib/api'
import { Card } from '../../components/ui/Card'
import Spinner from '../../components/ui/Spinner'
import EmptyState from '../../components/ui/EmptyState'
import Badge from '../../components/ui/Badge'
import { formatDate } from '../../lib/utils'
import { showToast } from '../../components/ui/Toast'

const BLANK = { name: '', subject: '', body_html: '' }

export default function EmailMarketingPage() {
  const { entityId, user } = useAuth()
  const qc = useQueryClient()
  const [selected, setSelected] = useState<any>(null)
  const [view, setView] = useState<'recipients' | 'stats'>('recipients')
  const [showCreate, setShowCreate] = useState(false)
  const [showAddPersons, setShowAddPersons] = useState(false)
  const [form, setForm] = useState(BLANK)
  const [personSearch, setPersonSearch] = useState('')
  const [checkedPersons, setCheckedPersons] = useState<Set<string>>(new Set())
  const [confirmSend, setConfirmSend] = useState(false)

  const { data, isLoading } = useQuery({
    queryKey: ['em-campaigns', entityId],
    queryFn: () => api.get('/email-marketing/campaigns', { params: { entity_id: entityId } }).then(r => r.data),
    enabled: !!entityId,
    refetchInterval: selected?.status === 'sending' ? 5000 : false,
  })

  const { data: recipients } = useQuery({
    queryKey: ['em-recipients', selected?.id],
    queryFn: () => api.get(`/email-marketing/campaigns/${selected.id}/recipients`).then(r => r.data),
    enabled: !!selected?.id && view === 'recipients',
  })

  const { data: stats } = useQuery({
    queryKey: ['em-stats', selected?.id],
    queryFn: () => api.get(`/email-marketing/campaigns/${selected.id}/stats`).then(r => r.data),
    enabled: !!selected?.id && view === 'stats',
  })

  const { data: contacts } = useQuery({
    queryKey: ['contacts-for-em', entityId],
    queryFn: () => api.get('/contacts', { params: { entity_id: entityId } }).then(r => r.data),
    enabled: showAddPersons && !!entityId,
  })

  const { data: allPersons } = useQuery({
    queryKey: ['all-persons-for-em', selected?.id],
    queryFn: async () => {
      const cos: any[] = Array.isArray(contacts) ? contacts : []
      const results: any[] = []
      for (const co of cos) {
        const ps = await api.get(`/contacts/${co.id}/persons`).then(r => r.data)
        results.push(...(ps as any[]).map((p: any) => ({ ...p, company_name: co.company_name })))
      }
      return results
    },
    enabled: showAddPersons && Array.isArray(contacts) && (contacts as any[]).length > 0,
  })

  const campaigns: any[] = Array.isArray(data) ? data : []
  const recipientList: any[] = Array.isArray(recipients) ? recipients : []
  const personList: any[] = (Array.isArray(allPersons) ? allPersons : [])
    .filter((p: any) => p.email && (!personSearch ||
      p.full_name?.toLowerCase().includes(personSearch.toLowerCase()) ||
      p.company_name?.toLowerCase().includes(personSearch.toLowerCase())))

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['em-campaigns'] })
    qc.invalidateQueries({ queryKey: ['em-recipients'] })
    qc.invalidateQueries({ queryKey: ['em-stats'] })
  }

  const createMutation = useMutation({
    mutationFn: () => api.post('/email-marketing/campaigns', {
      entity_id: entityId, ...form, created_by: user?.username,
    }),
    onSuccess: () => { showToast('Campaign dibuat'); setShowCreate(false); setForm(BLANK); invalidate() },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? 'Gagal', 'error'),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.delete(`/email-marketing/campaigns/${id}`),
    onSuccess: () => { showToast('Campaign dihapus'); setSelected(null); invalidate() },
  })

  const addPersonsMutation = useMutation({
    mutationFn: () => api.post(`/email-marketing/campaigns/${selected.id}/recipients`, {
      person_ids: Array.from(checkedPersons),
    }),
    onSuccess: (res) => {
      showToast(`${res.data.added} penerima ditambahkan`)
      setShowAddPersons(false); setCheckedPersons(new Set()); invalidate()
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? 'Gagal', 'error'),
  })

  const removeRecipient = useMutation({
    mutationFn: (id: string) => api.delete(`/email-marketing/recipients/${id}`),
    onSuccess: () => invalidate(),
  })

  const sendMutation = useMutation({
    mutationFn: () => api.post(`/email-marketing/campaigns/${selected.id}/send`),
    onSuccess: () => {
      showToast('Pengiriman dimulai di latar belakang')
      setConfirmSend(false); invalidate()
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? 'Gagal memulai pengiriman', 'error'),
  })

  const togglePerson = (id: string) => {
    setCheckedPersons(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  const f = (k: keyof typeof BLANK) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
    setForm(prev => ({ ...prev, [k]: e.target.value }))

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Email Marketing</h1>
          <p className="text-sm text-gray-500 mt-0.5">Kirim email massal ke kontak & pantau open rate</p>
        </div>
        <button onClick={() => setShowCreate(true)} className="btn-primary"><Plus className="h-4 w-4" /> Campaign Baru</button>
      </div>

      <div className="grid grid-cols-5 gap-4 h-[calc(100vh-240px)]">
        {/* Campaign list */}
        <div className="col-span-2 overflow-y-auto space-y-2">
          {isLoading ? <div className="py-8 flex justify-center"><Spinner /></div>
            : campaigns.length === 0
              ? <EmptyState title="Belum ada campaign" description="Buat campaign email pertama Anda." />
              : campaigns.map((c: any) => (
                <div key={c.id} onClick={() => { setSelected(c); setView('recipients') }}
                  className={`border rounded-xl p-3 cursor-pointer transition-colors ${selected?.id === c.id ? 'border-primary-500 bg-primary-50' : 'border-gray-200 bg-white hover:border-gray-300'}`}>
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <Mail className="h-4 w-4 text-gray-400 flex-shrink-0" />
                        <p className="font-medium text-sm truncate">{c.name}</p>
                      </div>
                      <p className="text-xs text-gray-500 mt-0.5 ml-6 truncate">{c.subject}</p>
                    </div>
                    <Badge status={c.status} />
                  </div>
                  <div className="mt-2 ml-6 flex gap-3 text-xs text-gray-500">
                    <span><Users className="inline h-3 w-3 mr-0.5" />{c.total_recipients}</span>
                    <span className="text-green-600"><Send className="inline h-3 w-3 mr-0.5" />{c.sent_count}</span>
                    {c.opened_count > 0 && <span className="text-blue-600"><Eye className="inline h-3 w-3 mr-0.5" />{c.opened_count}</span>}
                  </div>
                </div>
              ))}
        </div>

        {/* Campaign detail */}
        <div className="col-span-3 overflow-y-auto">
          {!selected ? (
            <Card className="h-full flex items-center justify-center">
              <div className="text-center text-gray-400">
                <Mail className="h-10 w-10 mx-auto mb-2 opacity-30" />
                <p className="text-sm">Pilih campaign untuk melihat detail</p>
              </div>
            </Card>
          ) : (
            <Card className="space-y-4">
              {/* Header */}
              <div className="flex items-start justify-between">
                <div>
                  <h2 className="text-lg font-bold">{selected.name}</h2>
                  <p className="text-sm text-gray-500">{selected.subject}</p>
                </div>
                <div className="flex gap-2">
                  {['draft', 'scheduled'].includes(selected.status) && (
                    <>
                      <button onClick={() => setShowAddPersons(true)} className="btn-secondary text-xs"><Plus className="h-3.5 w-3.5" /> Tambah Penerima</button>
                      <button onClick={() => setConfirmSend(true)} className="btn-primary text-xs"><Send className="h-3.5 w-3.5" /> Kirim</button>
                    </>
                  )}
                  <button onClick={() => deleteMutation.mutate(selected.id)} className="text-xs text-red-600 border border-red-300 rounded-lg px-3 py-1.5 hover:bg-red-50">
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>

              {/* Tabs */}
              <div className="flex gap-1 border-b">
                {(['recipients', 'stats'] as const).map(t => (
                  <button key={t} onClick={() => setView(t)}
                    className={`px-3 py-1.5 text-sm font-medium border-b-2 -mb-px transition-colors ${view === t ? 'border-primary-500 text-primary-600' : 'border-transparent text-gray-500 hover:text-gray-700'}`}>
                    {t === 'recipients' ? <><Users className="inline h-3.5 w-3.5 mr-1" />Penerima</> : <><BarChart2 className="inline h-3.5 w-3.5 mr-1" />Statistik</>}
                  </button>
                ))}
              </div>

              {/* Recipients tab */}
              {view === 'recipients' && (
                recipientList.length === 0
                  ? <EmptyState title="Belum ada penerima" description="Tambah penerima dari database kontak." />
                  : (
                    <table className="min-w-full text-sm">
                      <thead><tr className="border-b border-gray-200">
                        {['Nama', 'Email', 'Perusahaan', 'Status', 'Dibuka', ''].map(h => (
                          <th key={h} className="text-left py-2 pr-3 text-xs font-medium text-gray-600">{h}</th>
                        ))}
                      </tr></thead>
                      <tbody>
                        {recipientList.map((r: any) => (
                          <tr key={r.id} className="border-b border-gray-50 hover:bg-gray-50">
                            <td className="py-2 pr-3 font-medium">{r.recipient_name ?? '—'}</td>
                            <td className="py-2 pr-3 text-gray-500 text-xs">{r.recipient_email}</td>
                            <td className="py-2 pr-3 text-gray-500 text-xs">{r.company_name ?? '—'}</td>
                            <td className="py-2 pr-3"><Badge status={r.status} /></td>
                            <td className="py-2 pr-3 text-xs">
                              {r.opened_at
                                ? <span className="text-blue-600"><Eye className="inline h-3 w-3 mr-0.5" />{r.open_count}× {formatDate(r.opened_at)}</span>
                                : <span className="text-gray-400">—</span>}
                            </td>
                            <td className="py-2">
                              {r.status === 'pending' && (
                                <button onClick={() => removeRecipient.mutate(r.id)} className="text-red-400 hover:text-red-600">
                                  <Trash2 className="h-3.5 w-3.5" />
                                </button>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )
              )}

              {/* Stats tab */}
              {view === 'stats' && stats && (
                <div className="grid grid-cols-3 gap-3">
                  {[
                    { label: 'Total Penerima', value: stats.total, color: 'text-gray-700' },
                    { label: 'Terkirim', value: stats.sent, color: 'text-green-600' },
                    { label: 'Gagal', value: stats.failed, color: 'text-red-600' },
                    { label: 'Dibuka', value: stats.opened, color: 'text-blue-600' },
                    { label: 'Open Rate', value: `${stats.open_rate_pct ?? 0}%`, color: 'text-purple-600' },
                    { label: 'Pending', value: stats.pending, color: 'text-yellow-600' },
                  ].map(s => (
                    <div key={s.label} className="bg-gray-50 rounded-xl p-3 text-center">
                      <p className={`text-2xl font-bold ${s.color}`}>{s.value}</p>
                      <p className="text-xs text-gray-500 mt-0.5">{s.label}</p>
                    </div>
                  ))}
                </div>
              )}
            </Card>
          )}
        </div>
      </div>

      {/* Create campaign modal */}
      {showCreate && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 overflow-auto">
          <div className="bg-white rounded-xl p-6 w-[48rem] shadow-xl my-8 space-y-4">
            <h3 className="text-lg font-bold">Campaign Email Baru</h3>
            <div>
              <label className="form-label">Nama Campaign *</label>
              <input value={form.name} onChange={f('name')} className="form-input w-full" placeholder="misal: Promo Q3 2026" />
            </div>
            <div>
              <label className="form-label">Subject Email *</label>
              <input value={form.subject} onChange={f('subject')} className="form-input w-full" placeholder="Subject yang akan diterima penerima" />
            </div>
            <div>
              <label className="form-label">Isi Email (HTML) *</label>
              <textarea value={form.body_html} onChange={f('body_html')} rows={10} className="form-input w-full font-mono text-xs"
                placeholder="<h2>Halo [nama],</h2><p>Kami ingin memperkenalkan...</p>" />
              <p className="text-xs text-gray-400 mt-1">Tips: gunakan HTML standar. Tracking pixel akan ditambahkan otomatis saat pengiriman.</p>
            </div>
            <div className="flex gap-3 justify-end">
              <button onClick={() => { setShowCreate(false); setForm(BLANK) }} className="btn-secondary">Batal</button>
              <button onClick={() => createMutation.mutate()}
                disabled={createMutation.isPending || !form.name || !form.subject || !form.body_html}
                className="btn-primary">{createMutation.isPending ? 'Menyimpan...' : 'Buat Campaign'}</button>
            </div>
          </div>
        </div>
      )}

      {/* Add persons from contacts modal */}
      {showAddPersons && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 overflow-auto">
          <div className="bg-white rounded-xl p-6 w-[36rem] shadow-xl my-8 space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="text-lg font-bold">Pilih Penerima dari Kontak</h3>
              <button onClick={() => setShowAddPersons(false)} className="text-gray-400 text-xl">×</button>
            </div>
            <input value={personSearch} onChange={e => setPersonSearch(e.target.value)}
              placeholder="Cari nama / perusahaan..." className="form-input w-full" />
            <div className="max-h-80 overflow-y-auto space-y-1">
              {personList.length === 0
                ? <p className="text-sm text-gray-400 text-center py-4">Tidak ada contact person dengan email</p>
                : personList.map((p: any) => (
                  <label key={p.id} className="flex items-start gap-3 p-2 rounded-lg hover:bg-gray-50 cursor-pointer">
                    <input type="checkbox" checked={checkedPersons.has(p.id)}
                      onChange={() => togglePerson(p.id)} className="mt-0.5 h-4 w-4" />
                    <div>
                      <p className="text-sm font-medium">{p.full_name}
                        {p.title && <span className="text-gray-400 font-normal"> — {p.title}</span>}
                      </p>
                      <p className="text-xs text-gray-500">{p.company_name} · {p.email}</p>
                    </div>
                  </label>
                ))}
            </div>
            <div className="flex items-center justify-between">
              <p className="text-sm text-gray-500">{checkedPersons.size} dipilih</p>
              <div className="flex gap-3">
                <button onClick={() => setShowAddPersons(false)} className="btn-secondary">Batal</button>
                <button onClick={() => addPersonsMutation.mutate()}
                  disabled={addPersonsMutation.isPending || checkedPersons.size === 0}
                  className="btn-primary">{addPersonsMutation.isPending ? 'Menambahkan...' : 'Tambahkan'}</button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Confirm send modal */}
      {confirmSend && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl p-6 w-80 shadow-xl text-center space-y-4">
            <Send className="h-10 w-10 text-primary-600 mx-auto" />
            <h3 className="font-bold text-gray-900">Kirim Campaign?</h3>
            <p className="text-sm text-gray-600">
              Email akan dikirim ke semua penerima pending menggunakan SMTP yang dikonfigurasi.
              Aksi ini tidak bisa dibatalkan.
            </p>
            <div className="flex gap-3 justify-center">
              <button onClick={() => setConfirmSend(false)} className="btn-secondary">Batal</button>
              <button onClick={() => sendMutation.mutate()}
                disabled={sendMutation.isPending}
                className="btn-primary">{sendMutation.isPending ? 'Memulai...' : 'Ya, Kirim Sekarang'}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
