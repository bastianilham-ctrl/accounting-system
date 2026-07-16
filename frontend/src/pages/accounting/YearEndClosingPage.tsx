import { useState } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import { CheckCircle, XCircle, Lock, Unlock, ChevronRight } from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext'
import api from '../../lib/api'
import { Card } from '../../components/ui/Card'
import Spinner from '../../components/ui/Spinner'
import { formatDate, currentYear, MONTHS } from '../../lib/utils'
import { showToast } from '../../components/ui/Toast'

const STEP_LABELS = [
  'Pre-closing Checks',
  'Income Summary (Jurnal Penutup)',
  'Transfer ke Laba Ditahan',
  'Lock Semua Periode',
]

export default function YearEndClosingPage() {
  const { entityId, user } = useAuth()
  const [year, setYear] = useState(currentYear() - 1)
  const [closingDate, setClosingDate] = useState(`${currentYear() - 1}-12-31`)
  const [showSetup, setShowSetup] = useState(false)
  const [setupYear, setSetupYear] = useState(currentYear())
  const [setupStartMonth, setSetupStartMonth] = useState(1)
  const [preCheckResult, setPreCheckResult] = useState<any>(null)
  const [showCloseAll, setShowCloseAll] = useState(false)

  const { data: fiscalYears, isLoading: fyLoading, refetch: refetchFY } = useQuery({
    queryKey: ['fiscal-years', entityId],
    queryFn: () => api.get('/year-end/fiscal-years', { params: { entity_id: entityId } }).then(r => r.data),
    enabled: !!entityId,
  })

  const { data: periods, isLoading: pLoading, refetch: refetchPeriods } = useQuery({
    queryKey: ['fiscal-periods', entityId, year],
    queryFn: () => api.get('/year-end/periods', { params: { entity_id: entityId, fiscal_year: year } }).then(r => r.data),
    enabled: !!entityId,
  })

  const rows: any[] = Array.isArray(fiscalYears) ? fiscalYears : []
  const periodRows: any[] = Array.isArray(periods) ? periods : []

  const setupMutation = useMutation({
    mutationFn: () => api.post('/year-end/fiscal-years/setup', {
      entity_id: entityId, fiscal_year: setupYear, start_month: setupStartMonth,
    }),
    onSuccess: () => { showToast(`Fiscal year ${setupYear} berhasil dibuat`); setShowSetup(false); refetchFY() },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? 'Gagal setup fiscal year', 'error'),
  })

  const preCheckMutation = useMutation({
    mutationFn: () => api.post('/year-end/pre-closing-checks', { entity_id: entityId, fiscal_year: year }),
    onSuccess: (r) => { setPreCheckResult(r.data); refetchFY() },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? 'Pre-check gagal', 'error'),
  })

  const incomeSummaryMutation = useMutation({
    mutationFn: () => api.post('/year-end/income-summary', {
      entity_id: entityId, fiscal_year: year, closing_date: closingDate, closed_by: user?.username ?? '',
    }),
    onSuccess: (r) => { showToast(`Income summary posted: ${r.data.journal_no}`); refetchFY() },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? 'Income summary gagal', 'error'),
  })

  const reTransferMutation = useMutation({
    mutationFn: () => api.post('/year-end/re-transfer', {
      entity_id: entityId, fiscal_year: year, closing_date: closingDate, closed_by: user?.username ?? '',
    }),
    onSuccess: (r) => { showToast(`RE Transfer posted: ${r.data.journal_no}`); refetchFY() },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? 'RE Transfer gagal', 'error'),
  })

  const lockMutation = useMutation({
    mutationFn: () => api.post('/year-end/lock-periods', {
      entity_id: entityId, fiscal_year: year, locked_by: user?.username ?? '',
    }),
    onSuccess: () => { showToast(`Semua periode FY${year} dikunci`); refetchFY(); refetchPeriods() },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? 'Gagal lock periode', 'error'),
  })

  const closeAllMutation = useMutation({
    mutationFn: () => api.post('/year-end/close', {
      entity_id: entityId, fiscal_year: year, closing_date: closingDate, closed_by: user?.username ?? '',
    }),
    onSuccess: (r) => {
      showToast(`Tutup buku FY${year} selesai`)
      setShowCloseAll(false)
      refetchFY(); refetchPeriods()
    },
    onError: (e: any) => { showToast(e?.response?.data?.detail ?? 'Gagal tutup buku', 'error'); setShowCloseAll(false) },
  })

  const lockSingleMutation = useMutation({
    mutationFn: ({ m }: { m: number }) => api.post('/year-end/periods/lock-single', {
      entity_id: entityId, period_year: year, period_month: m, locked_by: user?.username ?? '',
    }),
    onSuccess: () => { showToast('Periode dikunci'); refetchPeriods() },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? 'Gagal', 'error'),
  })

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Tutup Buku Akhir Tahun</h1>
          <p className="text-sm text-gray-500 mt-0.5">Setup fiscal year, jurnal penutup, dan lock periode</p>
        </div>
        <button onClick={() => setShowSetup(true)} className="btn-secondary text-sm">+ Setup Fiscal Year</button>
      </div>

      {/* Setup fiscal year modal */}
      {showSetup && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl p-6 w-80 shadow-xl space-y-4">
            <h3 className="text-lg font-bold text-gray-900">Setup Fiscal Year</h3>
            <div>
              <label className="form-label">Tahun Fiskal</label>
              <input type="number" value={setupYear} onChange={e => setSetupYear(+e.target.value)}
                className="form-input w-full" min={2020} max={2099} />
            </div>
            <div>
              <label className="form-label">Bulan Mulai</label>
              <select value={setupStartMonth} onChange={e => setSetupStartMonth(+e.target.value)} className="form-select w-full">
                {MONTHS.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
              </select>
            </div>
            <div className="flex gap-2 justify-end">
              <button onClick={() => setShowSetup(false)} className="btn-secondary">Batal</button>
              <button onClick={() => setupMutation.mutate()} disabled={setupMutation.isPending} className="btn-primary">
                {setupMutation.isPending ? 'Membuat...' : 'Buat'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Fiscal years list */}
      <Card>
        <h2 className="text-base font-semibold text-gray-900 mb-3">Fiscal Years</h2>
        {fyLoading ? <Spinner /> : rows.length === 0 ? (
          <p className="text-sm text-gray-400 py-4 text-center">Belum ada fiscal year. Klik "+ Setup Fiscal Year".</p>
        ) : (
          <table className="min-w-full text-sm">
            <thead><tr className="border-b border-gray-200">
              {['FY', 'Mulai', 'Selesai', 'Status', 'Periode Terkunci', 'Aksi'].map(h => (
                <th key={h} className="text-left py-2 pr-4 font-medium text-gray-600">{h}</th>
              ))}
            </tr></thead>
            <tbody>
              {rows.map((r: any) => (
                <tr key={r.fiscal_year} className={`border-b border-gray-50 ${year === r.fiscal_year ? 'bg-blue-50' : ''}`}>
                  <td className="py-2 pr-4 font-semibold">{r.fiscal_year}</td>
                  <td className="py-2 pr-4 text-gray-500">{r.start_date ? formatDate(r.start_date) : ''}</td>
                  <td className="py-2 pr-4 text-gray-500">{r.end_date ? formatDate(r.end_date) : ''}</td>
                  <td className="py-2 pr-4">
                    <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${r.is_closed ? 'bg-green-100 text-green-700' : 'bg-amber-100 text-amber-700'}`}>
                      {r.is_closed ? 'CLOSED' : 'OPEN'}
                    </span>
                  </td>
                  <td className="py-2 pr-4 text-gray-600">{r.locked_periods ?? 0} / {r.total_periods ?? 12}</td>
                  <td className="py-2">
                    <button onClick={() => setYear(r.fiscal_year)}
                      className="text-xs text-blue-600 hover:underline flex items-center gap-1">
                      Kelola <ChevronRight className="h-3 w-3" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      {/* Closing workflow for selected year */}
      <Card>
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-4">
            <h2 className="text-base font-semibold text-gray-900">Tutup Buku FY {year}</h2>
            <input type="number" value={year} onChange={e => setYear(+e.target.value)}
              className="form-input w-20 text-sm" min={2020} max={2099} />
          </div>
          <div className="flex items-center gap-3">
            <div>
              <label className="form-label text-xs">Tgl Penutupan</label>
              <input type="date" value={closingDate} onChange={e => setClosingDate(e.target.value)}
                className="form-input text-sm w-36" />
            </div>
            <button onClick={() => setShowCloseAll(true)}
              className="btn-primary text-sm bg-red-600 hover:bg-red-700">
              Tutup Buku (Semua Sekaligus)
            </button>
          </div>
        </div>

        {/* Step-by-step */}
        <div className="space-y-3 mb-4">
          {[
            { label: STEP_LABELS[0], action: () => preCheckMutation.mutate(), loading: preCheckMutation.isPending, result: preCheckResult },
            { label: STEP_LABELS[1], action: () => incomeSummaryMutation.mutate(), loading: incomeSummaryMutation.isPending, result: incomeSummaryMutation.data?.data },
            { label: STEP_LABELS[2], action: () => reTransferMutation.mutate(), loading: reTransferMutation.isPending, result: reTransferMutation.data?.data },
            { label: STEP_LABELS[3], action: () => lockMutation.mutate(), loading: lockMutation.isPending, result: null },
          ].map((step, i) => (
            <div key={i} className="flex items-center gap-4 p-3 rounded-lg border border-gray-200">
              <div className="w-7 h-7 rounded-full bg-blue-100 text-blue-700 text-xs font-bold flex items-center justify-center flex-shrink-0">
                {i + 1}
              </div>
              <div className="flex-1">
                <p className="text-sm font-medium text-gray-900">{step.label}</p>
                {step.result && (
                  <p className="text-xs text-gray-500 mt-0.5">
                    {step.result.is_ready != null
                      ? (step.result.is_ready ? '✓ Siap untuk closing' : `✗ ${step.result.issues?.join(', ')}`)
                      : (step.result.journal_no ? `Journal: ${step.result.journal_no}` : JSON.stringify(step.result).slice(0, 80))}
                  </p>
                )}
              </div>
              <button onClick={step.action} disabled={step.loading} className="btn-secondary text-xs">
                {step.loading ? 'Proses...' : 'Jalankan'}
              </button>
            </div>
          ))}
        </div>
      </Card>

      {/* Period grid */}
      <Card>
        <h2 className="text-base font-semibold text-gray-900 mb-3">Periode FY {year}</h2>
        {pLoading ? <Spinner /> : (
          <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-6 gap-2">
            {periodRows.sort((a: any, b: any) => a.period_month - b.period_month).map((p: any) => (
              <div key={p.period_month}
                className={`p-3 rounded-lg border text-center ${p.status === 'locked' ? 'bg-gray-100 border-gray-200' : 'bg-white border-blue-200'}`}>
                <p className="text-xs font-semibold text-gray-700">{MONTHS.find(m => m.value === p.period_month)?.label}</p>
                <div className="flex items-center justify-center gap-1 mt-1">
                  {p.status === 'locked'
                    ? <Lock className="h-3.5 w-3.5 text-gray-400" />
                    : <Unlock className="h-3.5 w-3.5 text-blue-500" />}
                  <span className={`text-xs ${p.status === 'locked' ? 'text-gray-400' : 'text-blue-600'}`}>
                    {p.status === 'locked' ? 'Locked' : 'Open'}
                  </span>
                </div>
                {p.status !== 'locked' && (
                  <button onClick={() => lockSingleMutation.mutate({ m: p.period_month })}
                    disabled={lockSingleMutation.isPending}
                    className="mt-1.5 text-xs text-gray-500 hover:text-red-600 underline">
                    Lock
                  </button>
                )}
              </div>
            ))}
            {periodRows.length === 0 && (
              <p className="col-span-6 text-center text-sm text-gray-400 py-4">Belum ada periode. Setup fiscal year dulu.</p>
            )}
          </div>
        )}
      </Card>

      {/* Close all confirmation */}
      {showCloseAll && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl p-6 w-96 shadow-xl">
            <h3 className="text-lg font-bold text-gray-900 mb-2">Tutup Buku FY {year}</h3>
            <p className="text-sm text-gray-600 mb-4">
              Akan menjalankan semua langkah penutupan sekaligus (pre-check → income summary → RE transfer → lock periode).
              <strong className="text-red-600"> Tidak bisa dibatalkan setelah lock.</strong>
            </p>
            <div className="flex gap-3 justify-end">
              <button onClick={() => setShowCloseAll(false)} className="btn-secondary">Batal</button>
              <button onClick={() => closeAllMutation.mutate()}
                disabled={closeAllMutation.isPending}
                className="btn-primary bg-red-600 hover:bg-red-700">
                {closeAllMutation.isPending ? 'Memproses...' : 'Ya, Tutup Buku'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
