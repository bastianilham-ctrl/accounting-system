import { useState } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { CheckCircle, AlertTriangle, Lock } from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext'
import api from '../../lib/api'
import { Card } from '../../components/ui/Card'
import Spinner from '../../components/ui/Spinner'
import { formatDate, currentYear } from '../../lib/utils'
import { showToast } from '../../components/ui/Toast'
import { GLTab, ARAPTab, AssetsTab, BanksTab, SimpleListTab } from './OpeningBalanceTabs'

const TABS = ['gl', 'ar', 'ap', 'assets', 'inventory', 'banks', 'leave'] as const
type Tab = typeof TABS[number]

const TAB_LABELS: Record<Tab, string> = {
  gl: 'Trial Balance (GL)',
  ar: 'AR Outstanding',
  ap: 'AP Outstanding',
  assets: 'Aset Tetap',
  inventory: 'Inventori',
  banks: 'Bank',
  leave: 'Saldo Cuti',
}

export default function OpeningBalancePage() {
  const { entityId, user } = useAuth()
  useTranslation(['common'])
  const [tab, setTab] = useState<Tab>('gl')
  const [sessionForm, setSessionForm] = useState({
    opening_date: new Date().toISOString().slice(0, 10),
    fiscal_year: currentYear(),
    is_mid_year: false,
    notes: '',
  })
  const [showFinalize, setShowFinalize] = useState(false)

  const { data: status, isLoading: statusLoading, refetch: refetchStatus } = useQuery({
    queryKey: ['ob-status', entityId],
    queryFn: () => api.get('/opening-balance/status', { params: { entity_id: entityId } }).then(r => r.data),
    enabled: !!entityId,
  })

  const session = status?.session
  const sid = session?.session_id
  const isFinalized = session?.status === 'finalized'

  const createSessionMutation = useMutation({
    mutationFn: () => api.post('/opening-balance/session', {
      entity_id: entityId, ...sessionForm,
      fiscal_year: +sessionForm.fiscal_year,
      created_by: user?.username ?? '',
    }),
    onSuccess: () => { showToast('Session opening balance dibuat'); refetchStatus() },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? 'Gagal membuat session', 'error'),
  })

  const validateMutation = useMutation({
    mutationFn: () => api.post(`/opening-balance/${sid}/validate`),
    onSuccess: (r) => {
      const d = r.data
      if (d.is_valid) showToast('Validasi berhasil — siap untuk finalisasi')
      else showToast(`Validasi gagal: ${(d.errors ?? []).join(', ')}`, 'error')
      refetchStatus()
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? 'Gagal validasi', 'error'),
  })

  const finalizeMutation = useMutation({
    mutationFn: () => api.post(`/opening-balance/${sid}/finalize`, { finalized_by: user?.username ?? '' }),
    onSuccess: () => { showToast('Opening balance difinalisasi!'); setShowFinalize(false); refetchStatus() },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? 'Gagal finalisasi', 'error'),
  })

  if (statusLoading) return <div className="flex justify-center py-16"><Spinner size="lg" /></div>

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Saldo Awal (Opening Balance)</h1>
        <p className="text-sm text-gray-500 mt-0.5">Input saldo pembuka sebelum entitas mulai beroperasi di sistem</p>
      </div>

      {/* Session header */}
      {!session ? (
        <Card>
          <p className="text-sm font-semibold text-gray-700 mb-4">Buat Session Opening Balance</p>
          <div className="flex flex-wrap gap-4 items-end">
            <div>
              <label className="form-label">Tanggal Pembuka</label>
              <input type="date" value={sessionForm.opening_date}
                onChange={e => setSessionForm(s => ({ ...s, opening_date: e.target.value }))}
                className="form-input w-40" />
            </div>
            <div>
              <label className="form-label">Tahun Fiskal</label>
              <input type="number" value={sessionForm.fiscal_year}
                onChange={e => setSessionForm(s => ({ ...s, fiscal_year: +e.target.value }))}
                className="form-input w-24" min={2020} max={2099} />
            </div>
            <label className="flex items-center gap-2 text-sm text-gray-700 pb-1 cursor-pointer">
              <input type="checkbox" checked={sessionForm.is_mid_year}
                onChange={e => setSessionForm(s => ({ ...s, is_mid_year: e.target.checked }))}
                className="rounded" />
              Mid-year entry
            </label>
            <div className="flex-1 min-w-48">
              <label className="form-label">Catatan</label>
              <input type="text" value={sessionForm.notes}
                onChange={e => setSessionForm(s => ({ ...s, notes: e.target.value }))}
                className="form-input" placeholder="Opsional" />
            </div>
            <button onClick={() => createSessionMutation.mutate()}
              disabled={createSessionMutation.isPending} className="btn-primary">
              {createSessionMutation.isPending ? 'Membuat...' : 'Buat Session'}
            </button>
          </div>
        </Card>
      ) : (
        <Card>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              {isFinalized
                ? <Lock className="h-5 w-5 text-green-600" />
                : <AlertTriangle className="h-5 w-5 text-amber-500" />}
              <div>
                <p className="font-semibold text-gray-900">
                  Opening Balance — {formatDate(session.opening_date)} (FY {session.fiscal_year})
                </p>
                <p className="text-xs text-gray-500 mt-0.5">
                  Status:{' '}
                  <span className={`font-medium ${isFinalized ? 'text-green-600' : 'text-amber-600'}`}>
                    {isFinalized ? 'FINALIZED' : (session.status ?? 'draft').toUpperCase()}
                  </span>
                </p>
              </div>
            </div>
            {!isFinalized && (
              <div className="flex gap-2">
                <button onClick={() => validateMutation.mutate()}
                  disabled={validateMutation.isPending} className="btn-secondary text-sm">
                  {validateMutation.isPending ? 'Validasi...' : 'Validasi'}
                </button>
                <button onClick={() => setShowFinalize(true)}
                  className="btn-primary text-sm bg-red-600 hover:bg-red-700">
                  Finalisasi
                </button>
              </div>
            )}
          </div>
          {status?.last_validation && (
            <div className={`mt-3 p-3 rounded text-sm ${status.last_validation.is_valid ? 'bg-green-50 text-green-800' : 'bg-red-50 text-red-800'}`}>
              {status.last_validation.is_valid
                ? '✓ Validasi terakhir: OK — siap difinalisasi'
                : `✗ ${(status.last_validation.errors ?? []).join(' | ')}`}
            </div>
          )}
        </Card>
      )}

      {/* Tabs */}
      {sid && !isFinalized && (
        <>
          <div className="border-b border-gray-200">
            <nav className="-mb-px flex space-x-6 overflow-x-auto">
              {TABS.map(t => (
                <button key={t} onClick={() => setTab(t)}
                  className={`pb-3 text-sm font-medium whitespace-nowrap border-b-2 transition-colors ${
                    tab === t ? 'border-blue-600 text-blue-600' : 'border-transparent text-gray-500 hover:text-gray-700'
                  }`}>
                  {TAB_LABELS[t]}
                </button>
              ))}
            </nav>
          </div>

          {tab === 'gl'        && <GLTab sessionId={sid} onSaved={refetchStatus} />}
          {tab === 'ar'        && <ARAPTab sessionId={sid} type="ar" onSaved={refetchStatus} />}
          {tab === 'ap'        && <ARAPTab sessionId={sid} type="ap" onSaved={refetchStatus} />}
          {tab === 'assets'    && <AssetsTab sessionId={sid} onSaved={refetchStatus} />}
          {tab === 'inventory' && <SimpleListTab sessionId={sid} type="inventory" onSaved={refetchStatus} />}
          {tab === 'banks'     && <BanksTab sessionId={sid} onSaved={refetchStatus} />}
          {tab === 'leave'     && <SimpleListTab sessionId={sid} type="leave" onSaved={refetchStatus} />}
        </>
      )}

      {sid && isFinalized && (
        <Card>
          <div className="flex items-center gap-3 text-green-700">
            <CheckCircle className="h-6 w-6" />
            <p className="font-medium">Opening balance sudah difinalisasi dan dikunci. Data tidak bisa diubah lagi.</p>
          </div>
        </Card>
      )}

      {/* Finalize confirmation */}
      {showFinalize && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl p-6 w-96 shadow-xl">
            <h3 className="text-lg font-bold text-gray-900 mb-2">Konfirmasi Finalisasi</h3>
            <p className="text-sm text-gray-600 mb-4">
              Finalisasi akan memposting semua jurnal pembuka, membuat invoice AR/AP, dan mengunci session ini secara permanen.{' '}
              <strong className="text-red-600">Tindakan ini tidak bisa dibatalkan.</strong>
            </p>
            <div className="flex gap-3 justify-end">
              <button onClick={() => setShowFinalize(false)} className="btn-secondary">Batal</button>
              <button onClick={() => finalizeMutation.mutate()}
                disabled={finalizeMutation.isPending}
                className="btn-primary bg-red-600 hover:bg-red-700">
                {finalizeMutation.isPending ? 'Memproses...' : 'Ya, Finalisasi'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
