import { useState } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import api from '../../lib/api'
import { Card } from '../../components/ui/Card'
import Spinner from '../../components/ui/Spinner'
import { formatRupiah, formatDate } from '../../lib/utils'
import { showToast } from '../../components/ui/Toast'

// ── GL Tab ────────────────────────────────────────────────────────────────────
export function GLTab({ sessionId, onSaved }: { sessionId: string; onSaved: () => void }) {
  const { data, isLoading, refetch } = useQuery({
    queryKey: ['ob-gl', sessionId],
    queryFn: () => api.get(`/opening-balance/${sessionId}/gl`).then(r => r.data),
  })
  const [rows, setRows] = useState<any[]>([])
  const [draft, setDraft] = useState({ account_code: '', account_name: '', debit_balance: '', credit_balance: '' })
  const [editing, setEditing] = useState(false)

  const items: any[] = data?.items ?? []
  const display = rows.length ? rows : items
  const totalDr = display.reduce((s: number, r: any) => s + (+r.debit_balance || 0), 0)
  const totalCr = display.reduce((s: number, r: any) => s + (+r.credit_balance || 0), 0)
  const balanced = Math.abs(totalDr - totalCr) <= 1

  const saveMutation = useMutation({
    mutationFn: () => api.put(`/opening-balance/${sessionId}/gl`, { balances: display, replace_all: true }),
    onSuccess: () => { showToast('GL disimpan'); refetch(); onSaved() },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? 'Gagal simpan GL', 'error'),
  })

  const addRow = () => {
    if (!draft.account_code) return
    setRows([...display, { ...draft, debit_balance: +draft.debit_balance || 0, credit_balance: +draft.credit_balance || 0 }])
    setDraft({ account_code: '', account_name: '', debit_balance: '', credit_balance: '' })
    setEditing(false)
  }

  if (isLoading) return <div className="py-8 flex justify-center"><Spinner /></div>

  return (
    <Card>
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-4 text-sm text-gray-600">
          <span>Debit: <strong>{formatRupiah(totalDr)}</strong></span>
          <span>Kredit: <strong>{formatRupiah(totalCr)}</strong></span>
          {display.length > 0 && (
            <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${balanced ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'}`}>
              {balanced ? 'BALANCE' : 'TIDAK BALANCE'}
            </span>
          )}
        </div>
        <div className="flex gap-2">
          <button onClick={() => setEditing(true)} className="btn-secondary text-sm">+ Akun</button>
          {display.length > 0 && (
            <button onClick={() => saveMutation.mutate()} disabled={saveMutation.isPending} className="btn-primary text-sm">
              {saveMutation.isPending ? 'Menyimpan...' : 'Simpan GL'}
            </button>
          )}
        </div>
      </div>
      {editing && (
        <div className="flex flex-wrap gap-2 mb-4 p-3 bg-gray-50 rounded-lg items-end">
          <input placeholder="Kode Akun *" value={draft.account_code} onChange={e => setDraft(d => ({ ...d, account_code: e.target.value }))} className="form-input w-28" />
          <input placeholder="Nama Akun" value={draft.account_name} onChange={e => setDraft(d => ({ ...d, account_name: e.target.value }))} className="form-input flex-1 min-w-40" />
          <input type="number" placeholder="Debit" value={draft.debit_balance} onChange={e => setDraft(d => ({ ...d, debit_balance: e.target.value }))} className="form-input w-36" min={0} />
          <input type="number" placeholder="Kredit" value={draft.credit_balance} onChange={e => setDraft(d => ({ ...d, credit_balance: e.target.value }))} className="form-input w-36" min={0} />
          <button onClick={addRow} className="btn-primary text-sm">Tambah</button>
          <button onClick={() => setEditing(false)} className="btn-secondary text-sm">Batal</button>
        </div>
      )}
      <table className="min-w-full text-sm">
        <thead><tr className="border-b border-gray-200">
          {['Kode', 'Nama Akun', 'Debit', 'Kredit'].map(h => (
            <th key={h} className={`py-2 pr-3 font-medium text-gray-600 ${h.includes('Debit') || h.includes('Kredit') ? 'text-right' : 'text-left'}`}>{h}</th>
          ))}
        </tr></thead>
        <tbody>
          {display.map((r: any, i: number) => (
            <tr key={i} className="border-b border-gray-50">
              <td className="py-1.5 pr-3 font-mono text-xs text-gray-700">{r.account_code}</td>
              <td className="py-1.5 pr-3 text-gray-700">{r.account_name}</td>
              <td className="py-1.5 pr-3 text-right text-gray-700">{r.debit_balance > 0 ? formatRupiah(r.debit_balance) : ''}</td>
              <td className="py-1.5 text-right text-gray-700">{r.credit_balance > 0 ? formatRupiah(r.credit_balance) : ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {display.length === 0 && !editing && <p className="text-center text-sm text-gray-400 py-6">Belum ada data. Klik "+ Akun" untuk mulai.</p>}
    </Card>
  )
}

// ── AR / AP Tab ───────────────────────────────────────────────────────────────
export function ARAPTab({ sessionId, type, onSaved }: { sessionId: string; type: 'ar' | 'ap'; onSaved: () => void }) {
  const nameKey = type === 'ar' ? 'customer_name' : 'vendor_name'
  const nameLabel = type === 'ar' ? 'Customer' : 'Vendor'
  const { data, isLoading, refetch } = useQuery({
    queryKey: [`ob-${type}`, sessionId],
    queryFn: () => api.get(`/opening-balance/${sessionId}/${type}`).then(r => r.data),
  })
  const [rows, setRows] = useState<any[]>([])
  const [draft, setDraft] = useState({ [nameKey]: '', invoice_number: '', invoice_date: '', original_amount: '', amount_remaining: '' })
  const [editing, setEditing] = useState(false)

  const items: any[] = data?.items ?? []
  const display = rows.length ? rows : items
  const total = display.reduce((s: number, r: any) => s + (+r.amount_remaining || +r.original_amount || 0), 0)

  const saveMutation = useMutation({
    mutationFn: () => api.put(`/opening-balance/${sessionId}/${type}`, { items: display, replace_all: true }),
    onSuccess: () => { showToast(`${type.toUpperCase()} disimpan`); refetch(); onSaved() },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? 'Gagal simpan', 'error'),
  })

  const addRow = () => {
    if (!draft[nameKey] || !draft.invoice_number) return
    const amt = +draft.original_amount || 0
    setRows([...display, { ...draft, original_amount: amt, amount_remaining: +draft.amount_remaining || amt }])
    setDraft({ [nameKey]: '', invoice_number: '', invoice_date: '', original_amount: '', amount_remaining: '' })
    setEditing(false)
  }

  if (isLoading) return <div className="py-8 flex justify-center"><Spinner /></div>

  return (
    <Card>
      <div className="flex items-center justify-between mb-4">
        <span className="text-sm text-gray-600">Total Sisa: <strong>{formatRupiah(total)}</strong></span>
        <div className="flex gap-2">
          <button onClick={() => setEditing(true)} className="btn-secondary text-sm">+ Invoice</button>
          {display.length > 0 && (
            <button onClick={() => saveMutation.mutate()} disabled={saveMutation.isPending} className="btn-primary text-sm">
              {saveMutation.isPending ? 'Menyimpan...' : `Simpan ${type.toUpperCase()}`}
            </button>
          )}
        </div>
      </div>
      {editing && (
        <div className="flex flex-wrap gap-2 mb-4 p-3 bg-gray-50 rounded-lg items-end">
          <input placeholder={`${nameLabel} *`} value={draft[nameKey] as string} onChange={e => setDraft(d => ({ ...d, [nameKey]: e.target.value }))} className="form-input w-44" />
          <input placeholder="No. Invoice *" value={draft.invoice_number} onChange={e => setDraft(d => ({ ...d, invoice_number: e.target.value }))} className="form-input w-36" />
          <input type="date" value={draft.invoice_date} onChange={e => setDraft(d => ({ ...d, invoice_date: e.target.value }))} className="form-input w-36" />
          <input type="number" placeholder="Jumlah Asal" value={draft.original_amount} onChange={e => setDraft(d => ({ ...d, original_amount: e.target.value }))} className="form-input w-36" min={0} />
          <input type="number" placeholder="Sisa (kosong=sama)" value={draft.amount_remaining} onChange={e => setDraft(d => ({ ...d, amount_remaining: e.target.value }))} className="form-input w-36" min={0} />
          <button onClick={addRow} className="btn-primary text-sm">Tambah</button>
          <button onClick={() => setEditing(false)} className="btn-secondary text-sm">Batal</button>
        </div>
      )}
      <table className="min-w-full text-sm">
        <thead><tr className="border-b border-gray-200">
          {[nameLabel, 'No. Invoice', 'Tgl Invoice', 'Jumlah Asal', 'Sisa'].map(h => (
            <th key={h} className={`py-2 pr-3 font-medium text-gray-600 ${h.includes('Jumlah') || h.includes('Sisa') ? 'text-right' : 'text-left'}`}>{h}</th>
          ))}
        </tr></thead>
        <tbody>
          {display.map((r: any, i: number) => (
            <tr key={i} className="border-b border-gray-50">
              <td className="py-1.5 pr-3">{r[nameKey]}</td>
              <td className="py-1.5 pr-3 font-mono text-xs">{r.invoice_number}</td>
              <td className="py-1.5 pr-3 text-gray-500">{r.invoice_date ? formatDate(r.invoice_date) : ''}</td>
              <td className="py-1.5 pr-3 text-right">{formatRupiah(r.original_amount)}</td>
              <td className="py-1.5 text-right font-medium">{formatRupiah(r.amount_remaining ?? r.original_amount)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {display.length === 0 && !editing && <p className="text-center text-sm text-gray-400 py-6">Belum ada data.</p>}
    </Card>
  )
}

// ── Assets Tab ────────────────────────────────────────────────────────────────
export function AssetsTab({ sessionId, onSaved }: { sessionId: string; onSaved: () => void }) {
  const { data, isLoading, refetch } = useQuery({
    queryKey: ['ob-assets', sessionId],
    queryFn: () => api.get(`/opening-balance/${sessionId}/assets`).then(r => r.data),
  })
  const [rows, setRows] = useState<any[]>([])
  const [draft, setDraft] = useState({ asset_name: '', asset_code: '', category: '', acquisition_date: '', acquisition_cost: '', accumulated_depreciation: '0', useful_life_months: '60' })
  const [editing, setEditing] = useState(false)

  const items: any[] = data?.items ?? []
  const display = rows.length ? rows : items
  const totalNbv = display.reduce((s: number, r: any) => s + (+r.net_book_value || (+r.acquisition_cost - +r.accumulated_depreciation) || 0), 0)

  const saveMutation = useMutation({
    mutationFn: () => api.put(`/opening-balance/${sessionId}/assets`, {
      assets: display.map(r => ({ ...r, acquisition_cost: +r.acquisition_cost || 0, accumulated_depreciation: +r.accumulated_depreciation || 0, useful_life_months: +r.useful_life_months || 60 })),
      replace_all: true,
    }),
    onSuccess: () => { showToast('Aset disimpan'); refetch(); onSaved() },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? 'Gagal simpan', 'error'),
  })

  const addRow = () => {
    if (!draft.asset_name || !draft.acquisition_date) return
    setRows([...display, { ...draft }])
    setDraft({ asset_name: '', asset_code: '', category: '', acquisition_date: '', acquisition_cost: '', accumulated_depreciation: '0', useful_life_months: '60' })
    setEditing(false)
  }

  if (isLoading) return <div className="py-8 flex justify-center"><Spinner /></div>

  return (
    <Card>
      <div className="flex items-center justify-between mb-4">
        <span className="text-sm text-gray-600">Total NBV: <strong>{formatRupiah(totalNbv)}</strong></span>
        <div className="flex gap-2">
          <button onClick={() => setEditing(true)} className="btn-secondary text-sm">+ Aset</button>
          {display.length > 0 && (
            <button onClick={() => saveMutation.mutate()} disabled={saveMutation.isPending} className="btn-primary text-sm">
              {saveMutation.isPending ? 'Menyimpan...' : 'Simpan Aset'}
            </button>
          )}
        </div>
      </div>
      {editing && (
        <div className="flex flex-wrap gap-2 mb-4 p-3 bg-gray-50 rounded-lg items-end">
          <input placeholder="Nama Aset *" value={draft.asset_name} onChange={e => setDraft(d => ({ ...d, asset_name: e.target.value }))} className="form-input w-44" />
          <input placeholder="Kode" value={draft.asset_code} onChange={e => setDraft(d => ({ ...d, asset_code: e.target.value }))} className="form-input w-24" />
          <input placeholder="Kategori" value={draft.category} onChange={e => setDraft(d => ({ ...d, category: e.target.value }))} className="form-input w-32" />
          <input type="date" value={draft.acquisition_date} onChange={e => setDraft(d => ({ ...d, acquisition_date: e.target.value }))} className="form-input w-36" />
          <input type="number" placeholder="Harga Perolehan" value={draft.acquisition_cost} onChange={e => setDraft(d => ({ ...d, acquisition_cost: e.target.value }))} className="form-input w-36" min={0} />
          <input type="number" placeholder="Akum. Depr." value={draft.accumulated_depreciation} onChange={e => setDraft(d => ({ ...d, accumulated_depreciation: e.target.value }))} className="form-input w-32" min={0} />
          <input type="number" placeholder="Umur (bln)" value={draft.useful_life_months} onChange={e => setDraft(d => ({ ...d, useful_life_months: e.target.value }))} className="form-input w-24" min={1} />
          <button onClick={addRow} className="btn-primary text-sm">Tambah</button>
          <button onClick={() => setEditing(false)} className="btn-secondary text-sm">Batal</button>
        </div>
      )}
      <table className="min-w-full text-sm">
        <thead><tr className="border-b border-gray-200">
          {['Nama Aset', 'Kode', 'Kategori', 'Tgl Perolehan', 'Harga Perolehan', 'NBV'].map(h => (
            <th key={h} className={`py-2 pr-3 font-medium text-gray-600 ${h.includes('Harga') || h === 'NBV' ? 'text-right' : 'text-left'}`}>{h}</th>
          ))}
        </tr></thead>
        <tbody>
          {display.map((r: any, i: number) => (
            <tr key={i} className="border-b border-gray-50">
              <td className="py-1.5 pr-3">{r.asset_name}</td>
              <td className="py-1.5 pr-3 font-mono text-xs text-gray-500">{r.asset_code}</td>
              <td className="py-1.5 pr-3 text-gray-500">{r.category}</td>
              <td className="py-1.5 pr-3 text-gray-500">{r.acquisition_date ? formatDate(r.acquisition_date) : ''}</td>
              <td className="py-1.5 pr-3 text-right">{formatRupiah(+r.acquisition_cost)}</td>
              <td className="py-1.5 text-right font-medium">{formatRupiah(+r.net_book_value || +r.acquisition_cost - +r.accumulated_depreciation)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {display.length === 0 && !editing && <p className="text-center text-sm text-gray-400 py-6">Belum ada data.</p>}
    </Card>
  )
}

// ── Banks Tab ─────────────────────────────────────────────────────────────────
export function BanksTab({ sessionId, onSaved }: { sessionId: string; onSaved: () => void }) {
  const { data, isLoading, refetch } = useQuery({
    queryKey: ['ob-banks', sessionId],
    queryFn: () => api.get(`/opening-balance/${sessionId}/banks`).then(r => r.data),
  })
  const [rows, setRows] = useState<any[]>([])
  const [draft, setDraft] = useState({ bank_name: '', account_number: '', account_holder: '', opening_balance: '', gl_account_code: '' })
  const [editing, setEditing] = useState(false)

  const items: any[] = data?.items ?? []
  const display = rows.length ? rows : items
  const total = display.reduce((s: number, r: any) => s + (+r.opening_balance || 0), 0)

  const saveMutation = useMutation({
    mutationFn: () => api.put(`/opening-balance/${sessionId}/banks`, {
      banks: display.map(r => ({ ...r, opening_balance: +r.opening_balance || 0 })), replace_all: true,
    }),
    onSuccess: () => { showToast('Bank disimpan'); refetch(); onSaved() },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? 'Gagal simpan', 'error'),
  })

  const addRow = () => {
    if (!draft.bank_name) return
    setRows([...display, { ...draft, opening_balance: +draft.opening_balance || 0 }])
    setDraft({ bank_name: '', account_number: '', account_holder: '', opening_balance: '', gl_account_code: '' })
    setEditing(false)
  }

  if (isLoading) return <div className="py-8 flex justify-center"><Spinner /></div>

  return (
    <Card>
      <div className="flex items-center justify-between mb-4">
        <span className="text-sm text-gray-600">Total Saldo: <strong>{formatRupiah(total)}</strong></span>
        <div className="flex gap-2">
          <button onClick={() => setEditing(true)} className="btn-secondary text-sm">+ Rekening</button>
          {display.length > 0 && (
            <button onClick={() => saveMutation.mutate()} disabled={saveMutation.isPending} className="btn-primary text-sm">
              {saveMutation.isPending ? 'Menyimpan...' : 'Simpan Bank'}
            </button>
          )}
        </div>
      </div>
      {editing && (
        <div className="flex flex-wrap gap-2 mb-4 p-3 bg-gray-50 rounded-lg items-end">
          <input placeholder="Nama Bank *" value={draft.bank_name} onChange={e => setDraft(d => ({ ...d, bank_name: e.target.value }))} className="form-input w-36" />
          <input placeholder="No. Rekening" value={draft.account_number} onChange={e => setDraft(d => ({ ...d, account_number: e.target.value }))} className="form-input w-40" />
          <input placeholder="Pemegang Rekening" value={draft.account_holder} onChange={e => setDraft(d => ({ ...d, account_holder: e.target.value }))} className="form-input w-44" />
          <input type="number" placeholder="Saldo Awal" value={draft.opening_balance} onChange={e => setDraft(d => ({ ...d, opening_balance: e.target.value }))} className="form-input w-36" min={0} />
          <input placeholder="Kode Akun GL" value={draft.gl_account_code} onChange={e => setDraft(d => ({ ...d, gl_account_code: e.target.value }))} className="form-input w-28" />
          <button onClick={addRow} className="btn-primary text-sm">Tambah</button>
          <button onClick={() => setEditing(false)} className="btn-secondary text-sm">Batal</button>
        </div>
      )}
      <table className="min-w-full text-sm">
        <thead><tr className="border-b border-gray-200">
          {['Bank', 'No. Rekening', 'Pemegang', 'Kode GL', 'Saldo Awal'].map(h => (
            <th key={h} className={`py-2 pr-3 font-medium text-gray-600 ${h === 'Saldo Awal' ? 'text-right' : 'text-left'}`}>{h}</th>
          ))}
        </tr></thead>
        <tbody>
          {display.map((r: any, i: number) => (
            <tr key={i} className="border-b border-gray-50">
              <td className="py-1.5 pr-3">{r.bank_name}</td>
              <td className="py-1.5 pr-3 font-mono text-xs">{r.account_number}</td>
              <td className="py-1.5 pr-3 text-gray-500">{r.account_holder}</td>
              <td className="py-1.5 pr-3 font-mono text-xs text-gray-500">{r.gl_account_code}</td>
              <td className="py-1.5 text-right font-medium">{formatRupiah(r.opening_balance)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {display.length === 0 && !editing && <p className="text-center text-sm text-gray-400 py-6">Belum ada data.</p>}
    </Card>
  )
}

// ── Inventory / Leave read-only tab ───────────────────────────────────────────
export function SimpleListTab({ sessionId, type, onSaved: _ }: { sessionId: string; type: 'inventory' | 'leave'; onSaved: () => void }) {
  const { data, isLoading } = useQuery({
    queryKey: [`ob-${type}`, sessionId],
    queryFn: () => api.get(`/opening-balance/${sessionId}/${type}`).then(r => r.data),
  })
  const items: any[] = data?.items ?? []
  const cols = type === 'inventory'
    ? ['product_code', 'product_name', 'warehouse_code', 'quantity', 'unit_cost', 'total_value']
    : ['employee_code', 'employee_name', 'leave_type_code', 'entitled_days', 'used_days', 'balance_days']
  const headers = type === 'inventory'
    ? ['Kode Produk', 'Nama', 'Gudang', 'Qty', 'Unit Cost', 'Total Nilai']
    : ['Kode Karyawan', 'Nama', 'Jenis Cuti', 'Hak', 'Terpakai', 'Sisa']

  if (isLoading) return <div className="py-8 flex justify-center"><Spinner /></div>

  return (
    <Card>
      <p className="text-sm text-gray-500 mb-4">
        Data {type === 'inventory' ? 'inventori' : 'saldo cuti'} dapat dikirim via API langsung menggunakan <code className="bg-gray-100 px-1 rounded">PUT /opening-balance/{'{session_id}'}/${type}</code>.
      </p>
      {items.length > 0 ? (
        <table className="min-w-full text-sm">
          <thead><tr className="border-b border-gray-200">
            {headers.map(h => <th key={h} className="text-left py-2 pr-3 font-medium text-gray-600">{h}</th>)}
          </tr></thead>
          <tbody>
            {items.map((r: any, i: number) => (
              <tr key={i} className="border-b border-gray-50">
                {cols.map(c => (
                  <td key={c} className="py-1.5 pr-3 text-gray-700">
                    {typeof r[c] === 'number' && (c.includes('cost') || c.includes('value')) ? formatRupiah(r[c]) : (r[c] ?? '—')}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="text-center text-sm text-gray-400 py-6">Belum ada data {type === 'inventory' ? 'inventori' : 'cuti'}.</p>
      )}
    </Card>
  )
}
