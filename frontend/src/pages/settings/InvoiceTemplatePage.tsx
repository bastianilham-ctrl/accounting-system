import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Eye } from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext'
import api from '../../lib/api'
import { Card } from '../../components/ui/Card'
import Spinner from '../../components/ui/Spinner'
import { showToast } from '../../components/ui/Toast'

export default function InvoiceTemplatePage() {
  const { entityId } = useAuth()
  const qc = useQueryClient()
  const [tab, setTab] = useState<'html' | 'email'>('html')
  const [preview, setPreview] = useState(false)
  const [showCreate, setShowCreate] = useState(false)
  const [createForm, setCreateForm] = useState({ name: '', is_default: false })

  const { data: templates, isLoading } = useQuery({
    queryKey: ['invoice-templates', entityId],
    queryFn: () => api.get('/invoice-templates', { params: { entity_id: entityId } }).then(r => r.data),
    enabled: !!entityId,
  })

  const { data: defaultHtml } = useQuery({
    queryKey: ['invoice-template-default-html'],
    queryFn: () => api.get('/invoice-templates/default-html').then(r => r.data),
    enabled: tab === 'html',
  })

  const { data: emailTemplates } = useQuery({
    queryKey: ['email-templates', entityId],
    queryFn: () => api.get('/invoice-templates/email-templates', { params: { entity_id: entityId } }).then(r => r.data),
    enabled: tab === 'email' && !!entityId,
  })

  const rows: any[] = Array.isArray(templates) ? templates : (templates?.items ?? [])
  const emailRows: any[] = Array.isArray(emailTemplates) ? emailTemplates : (emailTemplates?.items ?? [])

  const createMutation = useMutation({
    mutationFn: () => api.post('/invoice-templates', { entity_id: entityId, ...createForm }),
    onSuccess: () => {
      showToast('Template dibuat')
      setShowCreate(false)
      setCreateForm({ name: '', is_default: false })
      qc.invalidateQueries({ queryKey: ['invoice-templates'] })
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? 'Gagal', 'error'),
  })

  const htmlContent = typeof defaultHtml === 'string' ? defaultHtml : (defaultHtml?.html ?? '')

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Template Invoice</h1>
          <p className="text-sm text-gray-500 mt-0.5">Kustomisasi tampilan invoice dan template email</p>
        </div>
        <button onClick={() => setShowCreate(true)} className="btn-primary"><Plus className="h-4 w-4" /> Template Baru</button>
      </div>

      {/* Tabs */}
      <div className="border-b border-gray-200">
        <nav className="-mb-px flex space-x-6">
          {(['html', 'email'] as const).map(t => (
            <button key={t} onClick={() => setTab(t)}
              className={`pb-3 text-sm font-medium border-b-2 transition-colors ${tab === t ? 'border-blue-600 text-blue-600' : 'border-transparent text-gray-500 hover:text-gray-700'}`}>
              {t === 'html' ? 'Template HTML' : 'Template Email'}
            </button>
          ))}
        </nav>
      </div>

      {tab === 'html' && (
        <>
          {/* Template list */}
          <Card>
            <h2 className="text-base font-semibold text-gray-900 mb-3">Template Tersimpan</h2>
            {isLoading ? <Spinner /> : rows.length === 0 ? (
              <p className="text-sm text-gray-400 text-center py-4">Belum ada template. Klik "+ Template Baru" untuk membuat.</p>
            ) : (
              <table className="min-w-full text-sm">
                <thead><tr className="border-b border-gray-200">
                  {['Nama', 'Default', 'Terakhir Update'].map(h => <th key={h} className="text-left py-2 pr-4 font-medium text-gray-600">{h}</th>)}
                </tr></thead>
                <tbody>
                  {rows.map((r: any) => (
                    <tr key={r.id} className="border-b border-gray-50">
                      <td className="py-2 pr-4 font-medium">{r.name}</td>
                      <td className="py-2 pr-4">
                        {r.is_default && <span className="text-xs bg-green-100 text-green-700 px-2 py-0.5 rounded-full font-semibold">Default</span>}
                      </td>
                      <td className="py-2 pr-4 text-gray-500 text-xs">{r.updated_at ? new Date(r.updated_at).toLocaleDateString('id-ID') : ''}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>

          {/* Default HTML preview */}
          <Card>
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-base font-semibold text-gray-900">Default HTML Template</h2>
              <button onClick={() => setPreview(!preview)} className="btn-secondary text-sm flex items-center gap-2">
                <Eye className="h-4 w-4" /> {preview ? 'Kode' : 'Preview'}
              </button>
            </div>
            {preview ? (
              <div className="border border-gray-200 rounded-lg overflow-hidden h-96">
                <iframe srcDoc={htmlContent} title="Invoice Preview" className="w-full h-full" />
              </div>
            ) : (
              <pre className="bg-gray-50 rounded-lg p-4 text-xs overflow-auto max-h-96 text-gray-700">
                {htmlContent || 'Loading...'}
              </pre>
            )}
          </Card>
        </>
      )}

      {tab === 'email' && (
        <Card>
          <h2 className="text-base font-semibold text-gray-900 mb-3">Template Email</h2>
          {emailRows.length === 0 ? (
            <p className="text-sm text-gray-400 text-center py-4">Belum ada template email.</p>
          ) : (
            <div className="space-y-4">
              {emailRows.map((r: any) => (
                <div key={r.id} className="border border-gray-200 rounded-lg p-4">
                  <div className="flex items-center justify-between mb-2">
                    <p className="font-semibold text-gray-900">{r.name}</p>
                    {r.is_default && <span className="text-xs bg-green-100 text-green-700 px-2 py-0.5 rounded-full font-semibold">Default</span>}
                  </div>
                  <p className="text-xs text-gray-500">Subject: {r.subject}</p>
                  <pre className="mt-2 text-xs bg-gray-50 rounded p-3 overflow-auto max-h-32 text-gray-700">{r.body_html ?? r.body}</pre>
                </div>
              ))}
            </div>
          )}
        </Card>
      )}

      {/* Create template modal */}
      {showCreate && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl p-6 w-80 shadow-xl space-y-4">
            <h3 className="text-lg font-bold text-gray-900">Template Baru</h3>
            <div>
              <label className="form-label">Nama Template *</label>
              <input value={createForm.name} onChange={e => setCreateForm(f => ({ ...f, name: e.target.value }))} className="form-input w-full" />
            </div>
            <label className="flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
              <input type="checkbox" checked={createForm.is_default} onChange={e => setCreateForm(f => ({ ...f, is_default: e.target.checked }))} className="rounded" />
              Set sebagai default
            </label>
            <div className="flex gap-3 justify-end">
              <button onClick={() => setShowCreate(false)} className="btn-secondary">Batal</button>
              <button onClick={() => createMutation.mutate()} disabled={createMutation.isPending || !createForm.name} className="btn-primary">
                {createMutation.isPending ? 'Membuat...' : 'Buat'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
