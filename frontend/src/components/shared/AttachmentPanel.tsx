import { useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { X, Paperclip, Download, Upload } from 'lucide-react'
import api, { downloadFile } from '../../lib/api'
import Spinner from '../ui/Spinner'
import EmptyState from '../ui/EmptyState'
import { showToast } from '../ui/Toast'

interface AttachmentPanelProps {
  refType: string
  refId: string
  entityId: string
  title?: string
  onClose: () => void
}

function formatSize(bytes?: number) {
  if (!bytes) return '-'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

export default function AttachmentPanel({ refType, refId, entityId, title, onClose }: AttachmentPanelProps) {
  const { t } = useTranslation(['ap', 'common'])
  const qc = useQueryClient()
  const fileInputRef = useRef<HTMLInputElement>(null)

  const { data, isLoading } = useQuery({
    queryKey: ['attachments', refType, refId],
    queryFn: () => api.get(`/attachments/by-entity/${refType}/${refId}`).then((r) => r.data),
    enabled: !!refId,
  })
  const attachments: any[] = Array.isArray(data) ? data : []

  const uploadMutation = useMutation({
    mutationFn: (file: File) => {
      const form = new FormData()
      form.append('entity_id', entityId)
      form.append('file', file)
      form.append('ref_type', refType)
      form.append('ref_id', refId)
      return api.post('/attachments/upload', form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
    },
    onSuccess: () => {
      showToast(t('ap:attachmentUploadSuccess'))
      qc.invalidateQueries({ queryKey: ['attachments', refType, refId] })
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('ap:attachmentUploadFailed'), 'error'),
  })

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) uploadMutation.mutate(file)
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-lg">
        <div className="flex items-center justify-between p-4 border-b">
          <p className="font-semibold text-gray-900">{title ?? t('ap:attachmentsModalTitle')}</p>
          <button onClick={onClose} className="p-1.5 hover:bg-gray-100 rounded-lg">
            <X className="h-5 w-5 text-gray-400" />
          </button>
        </div>

        <div className="p-4 space-y-3 max-h-[60vh] overflow-y-auto">
          {isLoading ? (
            <div className="flex justify-center py-8"><Spinner /></div>
          ) : attachments.length === 0 ? (
            <EmptyState title={t('ap:noAttachments')} description="" icon={<Paperclip className="h-10 w-10" />} />
          ) : (
            <ul className="divide-y divide-gray-100">
              {attachments.map((a) => (
                <li key={a.attachment_id} className="flex items-center justify-between py-2.5">
                  <div className="min-w-0 pr-3">
                    <p className="text-sm font-medium text-gray-800 truncate">{a.original_name}</p>
                    <p className="text-xs text-gray-400">{formatSize(a.file_size)}</p>
                  </div>
                  <button
                    onClick={() => downloadFile(`/attachments/${a.attachment_id}/download`, a.original_name)}
                    className="shrink-0 inline-flex items-center gap-1 text-xs px-2 py-1 rounded-md bg-primary-50 text-primary-700 hover:bg-primary-100"
                  >
                    <Download className="h-3 w-3" /> {t('common:download')}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="p-4 border-t">
          <input ref={fileInputRef} type="file" className="hidden" onChange={handleFileChange}
            accept=".pdf,.jpg,.jpeg,.png,.doc,.docx,.xls,.xlsx,.csv" />
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={uploadMutation.isPending}
            className="btn-secondary w-full justify-center"
          >
            {uploadMutation.isPending ? <Spinner size="sm" /> : <Upload className="h-4 w-4" />}
            {t('ap:uploadAttachmentBtn')}
          </button>
        </div>
      </div>
    </div>
  )
}
