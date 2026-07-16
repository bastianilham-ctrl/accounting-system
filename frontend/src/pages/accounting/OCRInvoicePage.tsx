import { useRef, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Upload, FileText, ScanLine, CheckCircle } from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext'
import api from '../../lib/api'
import { Card } from '../../components/ui/Card'
import Spinner from '../../components/ui/Spinner'
import EmptyState from '../../components/ui/EmptyState'
import Badge from '../../components/ui/Badge'
import { formatRupiah, formatDate } from '../../lib/utils'
import { showToast } from '../../components/ui/Toast'

interface ExtractResult {
  success: boolean
  vendor_id?: string
  vendor_name?: string
  invoice_no?: string
  invoice_date?: string
  subtotal?: number
  ppn_amount?: number
  pph_amount?: number
  total_amount?: number
  confidence?: number
}

export default function OCRInvoicePage() {
  const { entityId, user } = useAuth()
  const qc = useQueryClient()
  const fileRef = useRef<HTMLInputElement>(null)
  const extractRef = useRef<HTMLInputElement>(null)
  const [uploading, setUploading] = useState(false)
  const [extracting, setExtracting] = useState(false)
  const [extractResult, setExtractResult] = useState<ExtractResult | null>(null)

  const { data, isLoading } = useQuery({
    queryKey: ['ocr-invoices', entityId],
    queryFn: () => api.get(`/ocr/invoices/${entityId}`).then(r => r.data),
    enabled: !!entityId,
    refetchInterval: 15000,
  })

  const rows: any[] = Array.isArray(data) ? data : []
  const invalidate = () => qc.invalidateQueries({ queryKey: ['ocr-invoices'] })

  const handleUpload = async () => {
    const file = fileRef.current?.files?.[0]
    if (!file) return showToast('Pilih file PDF terlebih dahulu', 'error')
    const fd = new FormData()
    fd.append('file', file)
    if (entityId) fd.append('entity_id', entityId)
    fd.append('auto_post', 'false')
    setUploading(true)
    try {
      await api.post('/ocr/upload', fd, { headers: { 'Content-Type': 'multipart/form-data' } })
      showToast('Invoice diunggah, AI sedang mengekstrak data di latar belakang')
      if (fileRef.current) fileRef.current.value = ''
      setTimeout(invalidate, 5000)
    } catch (e: any) {
      showToast(e?.response?.data?.detail ?? 'Upload gagal', 'error')
    } finally {
      setUploading(false)
    }
  }

  const handleExtract = async () => {
    const file = extractRef.current?.files?.[0]
    if (!file) return showToast('Pilih file untuk diekstrak', 'error')
    const fd = new FormData()
    fd.append('file', file)
    if (entityId) fd.append('entity_id', entityId)
    setExtracting(true)
    try {
      const res = await api.post('/ocr/extract', fd, { headers: { 'Content-Type': 'multipart/form-data' } })
      setExtractResult(res.data)
    } catch (e: any) {
      showToast(e?.response?.data?.detail ?? 'Ekstrak gagal', 'error')
    } finally {
      setExtracting(false)
    }
  }

  const postMutation = useMutation({
    mutationFn: (invoiceId: string) =>
      api.post(`/ocr/invoices/${invoiceId}/post-journal`, null, {
        params: { created_by: user?.username ?? 'system' },
      }),
    onSuccess: () => { showToast('Jurnal AP berhasil diposting'); invalidate() },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? 'Gagal posting', 'error'),
  })

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">OCR Invoice</h1>
        <p className="text-sm text-gray-500 mt-0.5">Upload PDF invoice untuk ekstrak data otomatis dan posting jurnal AP</p>
      </div>

      <div className="grid grid-cols-2 gap-6">
        {/* Upload & auto-process */}
        <Card>
          <div className="space-y-3">
            <div className="flex items-center gap-2 text-sm font-semibold text-gray-700">
              <Upload className="h-4 w-4 text-primary-600" /> Upload & Proses Otomatis
            </div>
            <p className="text-xs text-gray-500">
              Upload PDF invoice. AI akan mengekstrak data dan menyimpan sebagai AP Invoice draft secara otomatis di latar belakang.
            </p>
            <input ref={fileRef} type="file" accept=".pdf" className="block w-full text-sm text-gray-500
              file:mr-3 file:py-1.5 file:px-3 file:rounded-md file:border-0
              file:text-xs file:font-medium file:bg-primary-50 file:text-primary-700
              hover:file:bg-primary-100 cursor-pointer" />
            <button
              onClick={handleUpload}
              disabled={uploading}
              className="btn-primary w-full text-sm"
            >
              {uploading ? <><Spinner size="sm" /> Mengunggah...</> : <><Upload className="h-4 w-4" /> Upload Invoice PDF</>}
            </button>
          </div>
        </Card>

        {/* Extract preview */}
        <Card>
          <div className="space-y-3">
            <div className="flex items-center gap-2 text-sm font-semibold text-gray-700">
              <ScanLine className="h-4 w-4 text-primary-600" /> Ekstrak & Preview
            </div>
            <p className="text-xs text-gray-500">
              Scan PDF/JPG/PNG untuk melihat hasil ekstrak tanpa menyimpan. Berguna untuk verifikasi sebelum input manual.
            </p>
            <input ref={extractRef} type="file" accept=".pdf,.jpg,.jpeg,.png" className="block w-full text-sm text-gray-500
              file:mr-3 file:py-1.5 file:px-3 file:rounded-md file:border-0
              file:text-xs file:font-medium file:bg-blue-50 file:text-blue-700
              hover:file:bg-blue-100 cursor-pointer" />
            <button
              onClick={handleExtract}
              disabled={extracting}
              className="btn-secondary w-full text-sm"
            >
              {extracting ? <><Spinner size="sm" /> Memindai...</> : <><ScanLine className="h-4 w-4" /> Scan & Ekstrak</>}
            </button>
          </div>
        </Card>
      </div>

      {/* Extract result preview */}
      {extractResult && (
        <Card>
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold text-gray-800 flex items-center gap-2">
              <FileText className="h-4 w-4 text-blue-500" /> Hasil Ekstrak OCR
            </h3>
            <button onClick={() => setExtractResult(null)} className="text-gray-400 text-lg hover:text-gray-600">×</button>
          </div>
          {extractResult.success ? (
            <div className="grid grid-cols-3 gap-3 text-sm">
              <div><p className="form-label">Vendor</p><p className="font-medium">{extractResult.vendor_name ?? '—'}</p></div>
              <div><p className="form-label">No. Invoice</p><p className="font-medium">{extractResult.invoice_no ?? '—'}</p></div>
              <div><p className="form-label">Tanggal</p><p className="font-medium">{extractResult.invoice_date ?? '—'}</p></div>
              <div><p className="form-label">Subtotal</p><p className="font-medium">{formatRupiah(extractResult.subtotal ?? 0)}</p></div>
              <div><p className="form-label">PPN</p><p className="font-medium">{formatRupiah(extractResult.ppn_amount ?? 0)}</p></div>
              <div><p className="form-label">PPh</p><p className="font-medium">{formatRupiah(extractResult.pph_amount ?? 0)}</p></div>
              <div className="col-span-2"><p className="form-label">Total</p><p className="text-lg font-bold text-primary-600">{formatRupiah(extractResult.total_amount ?? 0)}</p></div>
              <div><p className="form-label">Confidence</p><p className="font-medium">{((extractResult.confidence ?? 0) * 100).toFixed(0)}%</p></div>
            </div>
          ) : (
            <p className="text-sm text-red-600">Ekstrak gagal. Coba file yang lebih jelas.</p>
          )}
        </Card>
      )}

      {/* Invoice list */}
      <Card>
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-gray-800">AP Invoice dari OCR</h3>
          <button onClick={invalidate} className="text-xs text-blue-600 hover:underline">Refresh</button>
        </div>
        {isLoading ? (
          <div className="py-8 flex justify-center"><Spinner /></div>
        ) : rows.length === 0 ? (
          <EmptyState title="Belum ada invoice" description="Upload PDF invoice untuk memulai." />
        ) : (
          <table className="min-w-full text-sm">
            <thead>
              <tr className="border-b border-gray-200">
                {['No. Invoice', 'Vendor', 'Tgl Invoice', 'Total', 'Klasifikasi', 'Status', 'Aksi'].map(h => (
                  <th key={h} className="text-left py-2 pr-4 font-medium text-gray-600">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r: any) => (
                <tr key={r.id} className="border-b border-gray-50 hover:bg-gray-50">
                  <td className="py-2 pr-4 font-mono text-xs font-medium">{r.invoice_no}</td>
                  <td className="py-2 pr-4 text-gray-700">{r.vendor_name}</td>
                  <td className="py-2 pr-4 text-gray-500 text-xs">{r.invoice_date ? formatDate(r.invoice_date) : '—'}</td>
                  <td className="py-2 pr-4 font-medium">{formatRupiah(r.total_amount)}</td>
                  <td className="py-2 pr-4 text-xs text-gray-500">{r.classification ?? '—'}</td>
                  <td className="py-2 pr-4"><Badge status={r.status} /></td>
                  <td className="py-2">
                    {r.status === 'draft' && (
                      <button
                        onClick={() => postMutation.mutate(r.id)}
                        disabled={postMutation.isPending}
                        className="text-xs text-primary-600 hover:underline flex items-center gap-1"
                      >
                        <CheckCircle className="h-3 w-3" /> Post GL
                      </button>
                    )}
                    {r.status === 'approved' && (
                      <span className="text-xs text-green-600 flex items-center gap-1">
                        <CheckCircle className="h-3 w-3" /> Sudah diposting
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  )
}
