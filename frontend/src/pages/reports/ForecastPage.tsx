import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Play, TrendingUp } from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext'
import api from '../../lib/api'
import { Card } from '../../components/ui/Card'
import Spinner from '../../components/ui/Spinner'
import EmptyState from '../../components/ui/EmptyState'
import { formatRupiah, formatDate, currentYear } from '../../lib/utils'
import { showToast } from '../../components/ui/Toast'

type ForecastTab = 'pl' | 'cashflow' | 'bs' | 'summary'

export default function ForecastPage() {
  const { entityId } = useAuth()
  const qc = useQueryClient()
  const [selected, setSelected] = useState<any>(null)
  const [tab, setTab] = useState<ForecastTab>('summary')
  const [showCreate, setShowCreate] = useState(false)
  const [form, setForm] = useState({ name: '', base_year: currentYear(), scenario_type: 'base_case', notes: '' })

  const { data: scenarios, isLoading } = useQuery({
    queryKey: ['forecast-scenarios', entityId],
    queryFn: () => api.get('/forecast/scenarios', { params: { entity_id: entityId } }).then(r => r.data),
    enabled: !!entityId,
  })

  const { data: tabData, isLoading: tabLoading } = useQuery({
    queryKey: ['forecast-tab', selected?.id, tab],
    queryFn: () => api.get(`/forecast/scenarios/${selected.id}/${tab}`).then(r => r.data),
    enabled: !!selected?.id,
  })

  const rows: any[] = Array.isArray(scenarios) ? scenarios : (scenarios?.items ?? [])

  const createMutation = useMutation({
    mutationFn: () => api.post('/forecast/scenarios/create-and-run', {
      entity_id: entityId, ...form, base_year: +form.base_year,
    }),
    onSuccess: (r) => {
      showToast(`Scenario "${form.name}" dibuat & dijalankan`)
      setShowCreate(false)
      setForm({ name: '', base_year: currentYear(), scenario_type: 'base_case', notes: '' })
      qc.invalidateQueries({ queryKey: ['forecast-scenarios'] })
      setSelected(r.data)
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? 'Gagal buat scenario', 'error'),
  })

  const runMutation = useMutation({
    mutationFn: (id: string) => api.post(`/forecast/scenarios/${id}/run`),
    onSuccess: () => { showToast('Forecast dijalankan ulang'); qc.invalidateQueries({ queryKey: ['forecast-tab'] }) },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? 'Gagal', 'error'),
  })

  const TAB_LABELS: Record<ForecastTab, string> = { summary: 'Ringkasan', pl: 'P&L', cashflow: 'Cash Flow', bs: 'Balance Sheet' }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Financial Forecast</h1>
          <p className="text-sm text-gray-500 mt-0.5">Proyeksi P&L, Cash Flow, dan Balance Sheet 3-way</p>
        </div>
        <button onClick={() => setShowCreate(true)} className="btn-primary"><Plus className="h-4 w-4" /> Scenario Baru</button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Scenario list */}
        <div className="space-y-3">
          <p className="text-sm font-semibold text-gray-700">Scenario</p>
          {isLoading ? <Spinner /> : rows.length === 0
            ? <p className="text-sm text-gray-400">Belum ada scenario.</p>
            : rows.map((r: any) => (
              <div key={r.id}
                onClick={() => { setSelected(r); setTab('summary') }}
                className={`p-3 rounded-lg border cursor-pointer transition-colors ${selected?.id === r.id ? 'border-blue-500 bg-blue-50' : 'border-gray-200 hover:border-gray-300'}`}>
                <p className="text-sm font-medium text-gray-900">{r.name ?? r.scenario_name}</p>
                <p className="text-xs text-gray-500 mt-0.5">{r.scenario_type} · FY{r.base_year}</p>
                <p className="text-xs text-gray-400">{r.created_at ? formatDate(r.created_at) : ''}</p>
                <button onClick={(e) => { e.stopPropagation(); runMutation.mutate(r.id) }}
                  disabled={runMutation.isPending}
                  className="mt-2 text-xs text-blue-600 hover:underline flex items-center gap-1">
                  <Play className="h-3 w-3" /> Jalankan Ulang
                </button>
              </div>
            ))}
        </div>

        {/* Forecast output */}
        <div className="lg:col-span-2">
          {!selected
            ? <Card><EmptyState title="Pilih scenario" description="Klik scenario di kiri untuk melihat hasil forecast." /></Card>
            : (
              <>
                <div className="flex items-center justify-between mb-3">
                  <h2 className="text-base font-semibold text-gray-900 flex items-center gap-2">
                    <TrendingUp className="h-4 w-4 text-blue-500" /> {selected.name ?? selected.scenario_name}
                  </h2>
                  <div className="flex border border-gray-200 rounded-lg overflow-hidden text-xs">
                    {(Object.keys(TAB_LABELS) as ForecastTab[]).map(t => (
                      <button key={t} onClick={() => setTab(t)}
                        className={`px-3 py-1.5 font-medium ${tab === t ? 'bg-blue-600 text-white' : 'text-gray-600 hover:bg-gray-50'}`}>
                        {TAB_LABELS[t]}
                      </button>
                    ))}
                  </div>
                </div>
                <Card>
                  {tabLoading ? <div className="py-8 flex justify-center"><Spinner /></div>
                    : !tabData
                      ? <p className="text-sm text-gray-400 text-center py-6">Data belum tersedia. Jalankan forecast dulu.</p>
                      : <ForecastTable data={tabData} tab={tab} />}
                </Card>
              </>
            )}
        </div>
      </div>

      {/* Create modal */}
      {showCreate && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl p-6 w-96 shadow-xl space-y-4">
            <h3 className="text-lg font-bold text-gray-900">Buat Forecast Scenario</h3>
            <div>
              <label className="form-label">Nama Scenario *</label>
              <input value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} className="form-input w-full" placeholder="Base Case 2026, dll." />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="form-label">Tahun Dasar</label>
                <input type="number" value={form.base_year} onChange={e => setForm(f => ({ ...f, base_year: +e.target.value }))} className="form-input w-full" min={2020} max={2099} />
              </div>
              <div>
                <label className="form-label">Jenis</label>
                <select value={form.scenario_type} onChange={e => setForm(f => ({ ...f, scenario_type: e.target.value }))} className="form-select w-full">
                  {['base_case', 'optimistic', 'pessimistic', 'stress_test'].map(t => <option key={t} value={t}>{t}</option>)}
                </select>
              </div>
            </div>
            <div className="flex gap-3 justify-end">
              <button onClick={() => setShowCreate(false)} className="btn-secondary">Batal</button>
              <button onClick={() => createMutation.mutate()} disabled={createMutation.isPending || !form.name} className="btn-primary">
                {createMutation.isPending ? 'Membuat...' : 'Buat & Jalankan'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function ForecastTable({ data, tab }: { data: any; tab: ForecastTab }) {
  const rows: any[] = Array.isArray(data) ? data : (data?.periods ?? data?.rows ?? data?.lines ?? [])
  if (rows.length === 0) return <p className="text-sm text-gray-400 text-center py-6">Tidak ada data.</p>

  const keys = Object.keys(rows[0]).filter(k => k !== 'period' && k !== 'month' && k !== 'year')
  const isNumeric = (v: any) => typeof v === 'number'

  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-xs">
        <thead><tr className="border-b border-gray-200">
          <th className="text-left py-2 pr-3 font-medium text-gray-600">Periode</th>
          {keys.map(k => <th key={k} className="text-right py-2 px-2 font-medium text-gray-600 whitespace-nowrap">{k.replace(/_/g, ' ')}</th>)}
        </tr></thead>
        <tbody>
          {rows.map((r: any, i: number) => (
            <tr key={i} className="border-b border-gray-50">
              <td className="py-1.5 pr-3 font-medium text-gray-700">{r.period ?? `${r.year ?? ''}-${String(r.month ?? '').padStart(2, '0')}`}</td>
              {keys.map(k => (
                <td key={k} className={`py-1.5 px-2 text-right ${isNumeric(r[k]) && r[k] < 0 ? 'text-red-600' : 'text-gray-700'}`}>
                  {isNumeric(r[k]) ? formatRupiah(r[k]) : (r[k] ?? '—')}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
