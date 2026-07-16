import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Building2, Users, ChevronRight, Search, Star, Crown } from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext'
import api from '../../lib/api'
import { Card } from '../../components/ui/Card'
import Spinner from '../../components/ui/Spinner'
import EmptyState from '../../components/ui/EmptyState'
import Badge from '../../components/ui/Badge'
import { formatDate } from '../../lib/utils'
import { showToast } from '../../components/ui/Toast'

const SOURCE_OPTS = ['cold_call', 'referral', 'event', 'website', 'social_media', 'other']
const STATUS_OPTS = ['prospect', 'active', 'inactive', 'lost']
const BLANK_CO = {
  company_name: '', industry: '', website: '', city: '', province: '',
  phone: '', email: '', source: 'cold_call', status: 'prospect',
  assigned_to: '', notes: '', address: '',
}
const BLANK_P = {
  full_name: '', title: '', department: '', email: '', phone: '',
  whatsapp: '', linkedin: '', is_primary: false, is_decision_maker: false, notes: '',
}

export default function ContactPage() {
  const { entityId, user } = useAuth()
  const qc = useQueryClient()
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [showForm, setShowForm] = useState(false)
  const [editingCo, setEditingCo] = useState<any>(null)
  const [selectedCo, setSelectedCo] = useState<any>(null)
  const [coForm, setCoForm] = useState(BLANK_CO)
  const [showPersonForm, setShowPersonForm] = useState(false)
  const [editingP, setEditingP] = useState<any>(null)
  const [pForm, setPForm] = useState(BLANK_P)

  const { data, isLoading } = useQuery({
    queryKey: ['contacts', entityId, search, statusFilter],
    queryFn: () => api.get('/contacts', {
      params: { entity_id: entityId, search: search || undefined, status: statusFilter || undefined }
    }).then(r => r.data),
    enabled: !!entityId,
  })

  const { data: persons, isLoading: personsLoading } = useQuery({
    queryKey: ['contact-persons', selectedCo?.id],
    queryFn: () => api.get(`/contacts/${selectedCo.id}/persons`).then(r => r.data),
    enabled: !!selectedCo?.id,
  })

  const rows: any[] = Array.isArray(data) ? data : []
  const personList: any[] = Array.isArray(persons) ? persons : []
  const invalidate = () => qc.invalidateQueries({ queryKey: ['contacts'] })
  const invalidatePersons = () => qc.invalidateQueries({ queryKey: ['contact-persons'] })

  const coCreate = useMutation({
    mutationFn: () => api.post('/contacts', { entity_id: entityId, ...coForm }),
    onSuccess: () => { showToast('Contact dibuat'); setShowForm(false); setCoForm(BLANK_CO); invalidate() },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? 'Gagal', 'error'),
  })
  const coUpdate = useMutation({
    mutationFn: () => api.put(`/contacts/${editingCo?.id}`, coForm),
    onSuccess: () => { showToast('Contact diupdate'); setEditingCo(null); invalidate() },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? 'Gagal', 'error'),
  })
  const coDelete = useMutation({
    mutationFn: (id: string) => api.delete(`/contacts/${id}`),
    onSuccess: () => { showToast('Contact dihapus'); setSelectedCo(null); invalidate() },
  })

  const pCreate = useMutation({
    mutationFn: () => api.post('/contact-persons', { contact_id: selectedCo?.id, ...pForm }),
    onSuccess: () => { showToast('Contact person ditambahkan'); setShowPersonForm(false); setPForm(BLANK_P); invalidatePersons() },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? 'Gagal', 'error'),
  })
  const pUpdate = useMutation({
    mutationFn: () => api.put(`/contact-persons/${editingP?.id}`, pForm),
    onSuccess: () => { showToast('Contact person diupdate'); setEditingP(null); invalidatePersons() },
  })
  const pDelete = useMutation({
    mutationFn: (id: string) => api.delete(`/contact-persons/${id}`),
    onSuccess: () => { showToast('Dihapus'); invalidatePersons() },
  })

  const openEditCo = (co: any) => {
    setEditingCo(co)
    setCoForm({ company_name: co.company_name, industry: co.industry ?? '', website: co.website ?? '',
      city: co.city ?? '', province: co.province ?? '', phone: co.phone ?? '', email: co.email ?? '',
      source: co.source ?? 'cold_call', status: co.status ?? 'prospect',
      assigned_to: co.assigned_to ?? '', notes: co.notes ?? '', address: co.address ?? '' })
  }
  const openEditP = (p: any) => {
    setEditingP(p)
    setPForm({ full_name: p.full_name, title: p.title ?? '', department: p.department ?? '',
      email: p.email ?? '', phone: p.phone ?? '', whatsapp: p.whatsapp ?? '',
      linkedin: p.linkedin ?? '', is_primary: p.is_primary, is_decision_maker: p.is_decision_maker,
      notes: p.notes ?? '' })
  }

  const cf = (k: keyof typeof BLANK_CO) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
    setCoForm(f => ({ ...f, [k]: e.target.value }))
  const pf = (k: keyof typeof BLANK_P) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
    setPForm(f => ({ ...f, [k]: k === 'is_primary' || k === 'is_decision_maker' ? (e.target as HTMLInputElement).checked : e.target.value }))

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Database Kontak</h1>
          <p className="text-sm text-gray-500 mt-0.5">Kelola kontak perusahaan & contact person sales</p>
        </div>
        <button onClick={() => { setCoForm({ ...BLANK_CO, assigned_to: user?.username ?? '' }); setShowForm(true) }} className="btn-primary"><Plus className="h-4 w-4" /> Tambah Kontak</button>
      </div>

      {/* Filters */}
      <div className="flex gap-3 items-center">
        <div className="relative flex-1 max-w-xs">
          <Search className="absolute left-3 top-2.5 h-4 w-4 text-gray-400" />
          <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Cari nama, kota, industri..."
            className="form-input pl-9 w-full" />
        </div>
        <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)} className="form-select w-36">
          <option value="">Semua Status</option>
          {STATUS_OPTS.map(s => <option key={s} value={s}>{s}</option>)}
        </select>
      </div>

      <div className="grid grid-cols-5 gap-4 h-[calc(100vh-260px)]">
        {/* Left — company list */}
        <div className="col-span-2 overflow-y-auto space-y-2">
          {isLoading ? <div className="py-8 flex justify-center"><Spinner /></div>
            : rows.length === 0
              ? <EmptyState title="Belum ada kontak" description="Tambah kontak pertama untuk memulai." />
              : rows.map((co: any) => (
                <div key={co.id}
                  onClick={() => setSelectedCo(co)}
                  className={`border rounded-xl p-3 cursor-pointer transition-colors ${selectedCo?.id === co.id ? 'border-primary-500 bg-primary-50' : 'border-gray-200 bg-white hover:border-gray-300'}`}>
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <Building2 className="h-4 w-4 text-gray-400 flex-shrink-0" />
                        <p className="font-medium text-gray-900 text-sm truncate">{co.company_name}</p>
                      </div>
                      <p className="text-xs text-gray-500 mt-0.5 ml-6">{[co.industry, co.city].filter(Boolean).join(' · ')}</p>
                    </div>
                    <div className="flex items-center gap-1 flex-shrink-0">
                      <Badge status={co.status} />
                      <ChevronRight className="h-3.5 w-3.5 text-gray-400" />
                    </div>
                  </div>
                  <div className="flex items-center gap-3 mt-2 ml-6 text-xs text-gray-500">
                    <span><Users className="inline h-3 w-3 mr-0.5" />{co.person_count}</span>
                    {co.assigned_to && <span>→ {co.assigned_to}</span>}
                  </div>
                </div>
              ))}
        </div>

        {/* Right — contact person detail */}
        <div className="col-span-3 overflow-y-auto">
          {!selectedCo ? (
            <Card className="h-full flex items-center justify-center">
              <div className="text-center text-gray-400">
                <Users className="h-10 w-10 mx-auto mb-2 opacity-30" />
                <p className="text-sm">Pilih perusahaan untuk melihat contact person</p>
              </div>
            </Card>
          ) : (
            <Card className="space-y-4">
              <div className="flex items-start justify-between">
                <div>
                  <h2 className="text-lg font-bold text-gray-900">{selectedCo.company_name}</h2>
                  <div className="text-sm text-gray-500 space-x-3 mt-0.5">
                    {selectedCo.industry && <span>{selectedCo.industry}</span>}
                    {selectedCo.city && <span>📍 {selectedCo.city}</span>}
                    {selectedCo.phone && <span>📞 {selectedCo.phone}</span>}
                    {selectedCo.email && <span>✉ {selectedCo.email}</span>}
                  </div>
                  {selectedCo.website && <a href={selectedCo.website} target="_blank" rel="noreferrer" className="text-xs text-blue-600 hover:underline">{selectedCo.website}</a>}
                </div>
                <div className="flex gap-2">
                  <button onClick={() => openEditCo(selectedCo)} className="btn-secondary text-xs">Edit</button>
                  <button onClick={() => coDelete.mutate(selectedCo.id)} className="text-xs text-red-600 border border-red-300 rounded-lg px-3 py-1.5 hover:bg-red-50">Hapus</button>
                </div>
              </div>

              <div className="text-xs text-gray-500 flex gap-4">
                <span>Source: <strong>{selectedCo.source}</strong></span>
                <span>PIC Sales: <strong>{selectedCo.assigned_to ?? '—'}</strong></span>
                <span>Ditambahkan: {formatDate(selectedCo.created_at)}</span>
              </div>
              {selectedCo.notes && <p className="text-sm text-gray-600 bg-gray-50 rounded-lg p-2">{selectedCo.notes}</p>}

              <div className="border-t pt-3">
                <div className="flex items-center justify-between mb-3">
                  <h3 className="text-sm font-semibold text-gray-700">Contact Person</h3>
                  <button onClick={() => setShowPersonForm(true)} className="btn-secondary text-xs"><Plus className="h-3.5 w-3.5" /> Tambah Person</button>
                </div>

                {personsLoading ? <Spinner /> : personList.length === 0
                  ? <p className="text-sm text-gray-400 text-center py-4">Belum ada contact person</p>
                  : (
                    <div className="space-y-2">
                      {personList.map((p: any) => (
                        <div key={p.id} className="border border-gray-100 rounded-lg p-3 hover:bg-gray-50">
                          <div className="flex items-start justify-between">
                            <div>
                              <div className="flex items-center gap-2">
                                <p className="font-medium text-sm">{p.full_name}</p>
                                {p.is_primary && <span title="Kontak Utama"><Star className="h-3.5 w-3.5 text-yellow-500" /></span>}
                                {p.is_decision_maker && <span title="Decision Maker"><Crown className="h-3.5 w-3.5 text-purple-500" /></span>}
                              </div>
                              <p className="text-xs text-gray-500">{[p.title, p.department].filter(Boolean).join(' · ')}</p>
                            </div>
                            <div className="flex gap-2">
                              <button onClick={() => openEditP(p)} className="text-xs text-blue-600 hover:underline">Edit</button>
                              <button onClick={() => pDelete.mutate(p.id)} className="text-xs text-red-500 hover:underline">Hapus</button>
                            </div>
                          </div>
                          <div className="mt-1 text-xs text-gray-500 space-x-3">
                            {p.email && <span>✉ {p.email}</span>}
                            {p.phone && <span>📞 {p.phone}</span>}
                            {p.whatsapp && <span>WA: {p.whatsapp}</span>}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
              </div>
            </Card>
          )}
        </div>
      </div>

      {/* Company form modal */}
      {(showForm || editingCo) && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 overflow-auto">
          <div className="bg-white rounded-xl p-6 w-[38rem] shadow-xl my-8">
            <h3 className="text-lg font-bold mb-4">{editingCo ? 'Edit Kontak' : 'Tambah Kontak Baru'}</h3>
            <div className="grid grid-cols-2 gap-3">
              <div className="col-span-2"><label className="form-label">Nama Perusahaan *</label>
                <input value={coForm.company_name} onChange={cf('company_name')} className="form-input w-full" /></div>
              <div><label className="form-label">Industri</label>
                <input value={coForm.industry} onChange={cf('industry')} className="form-input w-full" /></div>
              <div><label className="form-label">Website</label>
                <input value={coForm.website} onChange={cf('website')} className="form-input w-full" /></div>
              <div><label className="form-label">Telepon</label>
                <input value={coForm.phone} onChange={cf('phone')} className="form-input w-full" /></div>
              <div><label className="form-label">Email</label>
                <input type="email" value={coForm.email} onChange={cf('email')} className="form-input w-full" /></div>
              <div><label className="form-label">Kota</label>
                <input value={coForm.city} onChange={cf('city')} className="form-input w-full" /></div>
              <div><label className="form-label">Provinsi</label>
                <input value={coForm.province} onChange={cf('province')} className="form-input w-full" /></div>
              <div><label className="form-label">Source</label>
                <select value={coForm.source} onChange={cf('source')} className="form-select w-full">
                  {SOURCE_OPTS.map(s => <option key={s} value={s}>{s}</option>)}
                </select></div>
              <div><label className="form-label">Status</label>
                <select value={coForm.status} onChange={cf('status')} className="form-select w-full">
                  {STATUS_OPTS.map(s => <option key={s} value={s}>{s}</option>)}
                </select></div>
              <div className="col-span-2"><label className="form-label">Catatan</label>
                <textarea value={coForm.notes} onChange={cf('notes')} className="form-input w-full" rows={2} /></div>
            </div>
            <div className="flex gap-3 justify-end mt-4">
              <button onClick={() => { setShowForm(false); setEditingCo(null) }} className="btn-secondary">Batal</button>
              <button onClick={() => editingCo ? coUpdate.mutate() : coCreate.mutate()}
                disabled={coCreate.isPending || coUpdate.isPending || !coForm.company_name}
                className="btn-primary">{editingCo ? 'Simpan' : 'Tambahkan'}</button>
            </div>
          </div>
        </div>
      )}

      {/* Person form modal */}
      {(showPersonForm || editingP) && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl p-6 w-[32rem] shadow-xl">
            <h3 className="text-lg font-bold mb-4">{editingP ? 'Edit Contact Person' : 'Tambah Contact Person'}</h3>
            <div className="grid grid-cols-2 gap-3">
              <div className="col-span-2"><label className="form-label">Nama Lengkap *</label>
                <input value={pForm.full_name} onChange={pf('full_name')} className="form-input w-full" /></div>
              <div><label className="form-label">Jabatan</label>
                <input value={pForm.title} onChange={pf('title')} className="form-input w-full" /></div>
              <div><label className="form-label">Departemen</label>
                <input value={pForm.department} onChange={pf('department')} className="form-input w-full" /></div>
              <div><label className="form-label">Email</label>
                <input type="email" value={pForm.email} onChange={pf('email')} className="form-input w-full" /></div>
              <div><label className="form-label">No. Telepon</label>
                <input value={pForm.phone} onChange={pf('phone')} className="form-input w-full" /></div>
              <div><label className="form-label">WhatsApp</label>
                <input value={pForm.whatsapp} onChange={pf('whatsapp')} className="form-input w-full" /></div>
              <div><label className="form-label">LinkedIn</label>
                <input value={pForm.linkedin} onChange={pf('linkedin')} className="form-input w-full" /></div>
              <div className="flex items-center gap-2">
                <input type="checkbox" id="is_primary" checked={pForm.is_primary as boolean}
                  onChange={pf('is_primary')} className="h-4 w-4" />
                <label htmlFor="is_primary" className="text-sm text-gray-700">Kontak Utama</label>
              </div>
              <div className="flex items-center gap-2">
                <input type="checkbox" id="is_dm" checked={pForm.is_decision_maker as boolean}
                  onChange={pf('is_decision_maker')} className="h-4 w-4" />
                <label htmlFor="is_dm" className="text-sm text-gray-700">Decision Maker</label>
              </div>
            </div>
            <div className="flex gap-3 justify-end mt-4">
              <button onClick={() => { setShowPersonForm(false); setEditingP(null) }} className="btn-secondary">Batal</button>
              <button onClick={() => editingP ? pUpdate.mutate() : pCreate.mutate()}
                disabled={pCreate.isPending || pUpdate.isPending || !pForm.full_name}
                className="btn-primary">{editingP ? 'Simpan' : 'Tambahkan'}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
