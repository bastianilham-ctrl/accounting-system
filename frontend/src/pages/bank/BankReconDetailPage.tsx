import { useState, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import {
  ArrowLeft, Zap, CheckCircle, Upload, XCircle, RefreshCw, Link, Unlink, AlertTriangle,
} from 'lucide-react'
import api from '../../lib/api'
import { Card, CardHeader } from '../../components/ui/Card'
import Spinner from '../../components/ui/Spinner'
import EmptyState from '../../components/ui/EmptyState'
import Badge from '../../components/ui/Badge'
import { formatRupiah, formatDate, MONTHS } from '../../lib/utils'
import { showToast } from '../../components/ui/Toast'

const MATCH_COLORS: Record<string, string> = {
  unmatched:    'bg-red-50   text-red-600   border-red-200',
  auto_matched: 'bg-blue-50  text-blue-600  border-blue-200',
  manual_matched:'bg-purple-50 text-purple-600 border-purple-200',
  confirmed:    'bg-green-50 text-green-600  border-green-200',
  ignored:      'bg-gray-50  text-gray-400   border-gray-200',
}

export default function BankReconDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { t } = useTranslation(['bank', 'common'])
  const qc = useQueryClient()
  const fileRef = useRef<HTMLInputElement>(null)

  const MATCH_LABELS: Record<string, string> = {
    unmatched: t('bank:reconDetail_status_unmatched'),
    auto_matched: t('bank:reconDetail_status_auto_matched'),
    manual_matched: t('bank:reconDetail_status_manual_matched'),
    confirmed: t('bank:reconDetail_status_confirmed'),
    ignored: t('bank:reconDetail_status_ignored'),
  }

  const [activeFilter, setActiveFilter] = useState<string>('')
  const [selectedBankLine, setSelectedBankLine] = useState<any>(null)
  const [showSuggestions, setShowSuggestions] = useState(false)
  const [importPreview, setImportPreview] = useState<any[]>([])
  const [showImportModal, setShowImportModal] = useState(false)

  // Statement detail (summary)
  const { data: stmt, isLoading: stmtLoading } = useQuery({
    queryKey: ['bank-recon-stmt', id],
    queryFn: () => api.get(`/bank-recon/statements/${id}`).then(r => r.data),
    enabled: !!id,
  })

  // Lines
  const { data: linesData, isLoading: linesLoading, refetch: refetchLines } = useQuery({
    queryKey: ['bank-recon-lines', id, activeFilter],
    queryFn: () =>
      api.get(`/bank-recon/statements/${id}/lines`, {
        params: activeFilter ? { match_status: activeFilter } : {},
      }).then(r => r.data),
    enabled: !!id,
  })
  const lines: any[] = Array.isArray(linesData) ? linesData : (linesData?.items ?? linesData?.lines ?? [])

  // GL suggestions for selected line
  const { data: suggestionsData, isLoading: sugsLoading } = useQuery({
    queryKey: ['bank-recon-suggestions', id, selectedBankLine?.id],
    queryFn: () =>
      api.get(`/bank-recon/statements/${id}/suggestions/${selectedBankLine.id}`).then(r => r.data),
    enabled: !!id && !!selectedBankLine && showSuggestions,
  })
  const suggestions: any[] = Array.isArray(suggestionsData) ? suggestionsData : []

  // Auto-match
  const autoMatchMutation = useMutation({
    mutationFn: () => api.post(`/bank-recon/statements/${id}/auto-match`),
    onSuccess: (res) => {
      const matched = res.data?.matched_count ?? 0
      showToast(t('bank:reconDetail_autoMatchSuccess', { count: matched }))
      qc.invalidateQueries({ queryKey: ['bank-recon-lines', id] })
      qc.invalidateQueries({ queryKey: ['bank-recon-stmt', id] })
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('bank:reconDetail_autoMatchFailed'), 'error'),
  })

  // Confirm suggestion
  const confirmMutation = useMutation({
    mutationFn: (payload: { bank_line_id: string; gl_line_id: string }) =>
      api.post(`/bank-recon/statements/${id}/confirm-suggestion`, payload),
    onSuccess: () => {
      showToast(t('bank:reconDetail_confirmSuccess'))
      setSelectedBankLine(null)
      setShowSuggestions(false)
      qc.invalidateQueries({ queryKey: ['bank-recon-lines', id] })
      qc.invalidateQueries({ queryKey: ['bank-recon-stmt', id] })
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('bank:reconDetail_confirmFailed'), 'error'),
  })

  // Manual match
  const [manualGlId, setManualGlId] = useState('')
  const manualMatchMutation = useMutation({
    mutationFn: (payload: { bank_line_id: string; gl_line_id: string }) =>
      api.post(`/bank-recon/statements/${id}/manual-match`, payload),
    onSuccess: () => {
      showToast(t('bank:reconDetail_manualMatchSuccess'))
      setSelectedBankLine(null)
      setManualGlId('')
      qc.invalidateQueries({ queryKey: ['bank-recon-lines', id] })
      qc.invalidateQueries({ queryKey: ['bank-recon-stmt', id] })
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('bank:reconDetail_manualMatchFailed'), 'error'),
  })

  // Finalize
  const finalizeMutation = useMutation({
    mutationFn: () => api.post(`/bank-recon/statements/${id}/finalize`),
    onSuccess: () => {
      showToast(t('bank:reconDetail_finalizeSuccess'))
      qc.invalidateQueries({ queryKey: ['bank-recon-stmt', id] })
      qc.invalidateQueries({ queryKey: ['bank-recon-statements'] })
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('bank:reconDetail_finalizeFailed'), 'error'),
  })

  // Import lines from CSV
  const importMutation = useMutation({
    mutationFn: (payload: any[]) =>
      api.post(`/bank-recon/statements/${id}/lines`, payload),
    onSuccess: (res) => {
      const cnt = res.data?.imported_count ?? res.data?.count ?? importPreview.length
      showToast(t('bank:reconDetail_importSuccess', { count: cnt }))
      setShowImportModal(false)
      setImportPreview([])
      qc.invalidateQueries({ queryKey: ['bank-recon-lines', id] })
      qc.invalidateQueries({ queryKey: ['bank-recon-stmt', id] })
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('bank:reconDetail_importFailed'), 'error'),
  })

  // CSV parser
  function parseCsvFile(file: File) {
    const reader = new FileReader()
    reader.onload = (e) => {
      const text = e.target?.result as string
      const rows = text.split('\n').map(r => r.split(',').map(c => c.trim().replace(/^"|"$/g, '')))
      if (rows.length < 2) { showToast(t('bank:reconDetail_csvEmpty'), 'error'); return }

      const header = rows[0].map(h => h.toLowerCase())
      const getCol = (...keys: string[]) => {
        for (const k of keys) {
          const i = header.findIndex(h => h.includes(k))
          if (i >= 0) return i
        }
        return -1
      }

      const dateIdx   = getCol('date', 'tanggal', 'tgl')
      const descIdx   = getCol('desc', 'keterangan', 'narasi', 'uraian')
      const refIdx    = getCol('ref', 'no', 'referensi')
      const debitIdx  = getCol('debit', 'db', 'kredit_out')
      const creditIdx = getCol('credit', 'kredit', 'cr', 'debit_in')
      const balIdx    = getCol('balance', 'saldo', 'running')

      const parsed = rows.slice(1).filter(r => r.length > 1 && r.some(c => c)).map(r => ({
        transaction_date: dateIdx >= 0 ? r[dateIdx] : '',
        description:      descIdx >= 0 ? r[descIdx] : r[1] ?? '',
        reference_no:     refIdx  >= 0 ? r[refIdx]  : undefined,
        debit_amount:     debitIdx  >= 0 ? parseFloat(r[debitIdx]?.replace(/[^0-9.-]/g, '')) || 0 : 0,
        credit_amount:    creditIdx >= 0 ? parseFloat(r[creditIdx]?.replace(/[^0-9.-]/g, '')) || 0 : 0,
        running_balance:  balIdx >= 0 ? parseFloat(r[balIdx]?.replace(/[^0-9.-]/g, '')) || undefined : undefined,
      })).filter(r => r.transaction_date)

      setImportPreview(parsed)
      setShowImportModal(true)
    }
    reader.readAsText(file)
  }

  if (stmtLoading) {
    return <div className="flex justify-center py-24"><Spinner size="lg" /></div>
  }

  const isFinalized = stmt?.status === 'finalized'
  const unmatchedCount = stmt?.unmatched_count ?? 0
  const matchedCount   = stmt?.matched_count ?? 0
  const totalLines     = stmt?.total_lines ?? 0
  const diff           = (stmt?.closing_balance ?? 0) - (stmt?.gl_balance ?? stmt?.closing_balance ?? 0)
  const isBalanced     = Math.abs(diff) < 1

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div className="flex items-start gap-3">
          <button onClick={() => navigate('/bank-recon')} className="mt-1 btn-secondary p-2">
            <ArrowLeft className="h-4 w-4" />
          </button>
          <div>
            <h1 className="text-xl font-bold text-gray-900">
              {t('bank:reconDetail_title', { account: stmt?.account_name ?? '...' })}
            </h1>
            <p className="text-sm text-gray-500 mt-0.5">
              {MONTHS.find(m => m.value === stmt?.statement_period_month)?.label} {stmt?.statement_period_year}
              &nbsp;&middot;&nbsp;{stmt?.account_number}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {!isFinalized && (
            <>
              <button onClick={() => fileRef.current?.click()} className="btn-secondary">
                <Upload className="h-4 w-4" /> {t('bank:reconDetail_importCsv')}
              </button>
              <input ref={fileRef} type="file" accept=".csv" className="hidden"
                onChange={e => { if (e.target.files?.[0]) parseCsvFile(e.target.files[0]); e.target.value = '' }} />
              <button onClick={() => autoMatchMutation.mutate()}
                disabled={autoMatchMutation.isPending || !totalLines}
                className="btn-secondary">
                <Zap className="h-4 w-4 text-yellow-500" />
                {autoMatchMutation.isPending ? t('bank:reconDetail_autoMatching') : t('bank:reconDetail_autoMatch')}
              </button>
              <button onClick={() => finalizeMutation.mutate()}
                disabled={finalizeMutation.isPending || unmatchedCount > 0}
                title={unmatchedCount > 0 ? t('bank:reconDetail_finalizeTooltip') : ''}
                className="btn-primary">
                <CheckCircle className="h-4 w-4" />
                {finalizeMutation.isPending ? t('bank:reconDetail_finalizing') : t('bank:reconDetail_finalize')}
              </button>
            </>
          )}
          {isFinalized && (
            <span className="inline-flex items-center gap-1.5 text-sm font-medium text-green-700 bg-green-50 px-3 py-1.5 rounded-lg">
              <CheckCircle className="h-4 w-4" /> {t('bank:reconDetail_finalized')}
            </span>
          )}
        </div>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="card p-4">
          <p className="text-xs text-gray-500 uppercase tracking-wide">{t('bank:reconDetail_openingBalance')}</p>
          <p className="text-lg font-bold text-gray-900 mt-1">Rp {formatRupiah(stmt?.opening_balance)}</p>
        </div>
        <div className="card p-4">
          <p className="text-xs text-gray-500 uppercase tracking-wide">{t('bank:reconDetail_closingBalanceStatement')}</p>
          <p className="text-lg font-bold text-gray-900 mt-1">Rp {formatRupiah(stmt?.closing_balance)}</p>
        </div>
        <div className="card p-4">
          <p className="text-xs text-gray-500 uppercase tracking-wide">{t('bank:reconDetail_glBalance')}</p>
          <p className="text-lg font-bold text-gray-900 mt-1">Rp {formatRupiah(stmt?.gl_balance ?? stmt?.closing_balance)}</p>
        </div>
        <div className={`card p-4 ${isBalanced ? 'border-l-4 border-green-400' : 'border-l-4 border-red-400'}`}>
          <p className="text-xs text-gray-500 uppercase tracking-wide">{t('bank:reconDetail_difference')}</p>
          <p className={`text-lg font-bold mt-1 ${isBalanced ? 'text-green-700' : 'text-red-700'}`}>
            Rp {formatRupiah(Math.abs(diff))}
          </p>
          <p className="text-xs mt-0.5">{isBalanced ? t('bank:reconDetail_balanced') : t('bank:reconDetail_notBalanced')}</p>
        </div>
      </div>

      {/* Progress bar */}
      {totalLines > 0 && (
        <div className="card p-4 flex items-center gap-4">
          <div className="flex-1">
            <div className="flex justify-between text-xs text-gray-500 mb-1">
              <span>{t('bank:reconDetail_matchedOfTotal', { matched: matchedCount, total: totalLines })}</span>
              <span>{t('bank:reconDetail_unmatchedCount', { count: unmatchedCount })}</span>
            </div>
            <div className="h-2 bg-gray-100 rounded-full overflow-hidden">
              <div className="h-full bg-green-500 rounded-full transition-all"
                style={{ width: `${totalLines ? (matchedCount / totalLines) * 100 : 0}%` }} />
            </div>
          </div>
          {unmatchedCount > 0 && (
            <div className="flex items-center gap-1 text-sm text-amber-600">
              <AlertTriangle className="h-4 w-4" />
              {t('bank:reconDetail_needsAttention', { count: unmatchedCount })}
            </div>
          )}
        </div>
      )}

      {/* Lines table */}
      <Card noPad>
        <CardHeader
          title={t('bank:reconDetail_linesTitle')}
          subtitle={t('bank:reconDetail_linesSubtitle', { count: lines.length })}
          actions={
            <div className="flex items-center gap-2">
              <select value={activeFilter} onChange={e => setActiveFilter(e.target.value)} className="form-select w-40">
                <option value="">{t('bank:reconDetail_allStatus')}</option>
                <option value="unmatched">{t('bank:reconDetail_status_unmatched')}</option>
                <option value="auto_matched">{t('bank:reconDetail_status_auto_matched_filter')}</option>
                <option value="manual_matched">{t('bank:reconDetail_status_manual_matched_filter')}</option>
                <option value="confirmed">{t('bank:reconDetail_status_confirmed')}</option>
                <option value="ignored">{t('bank:reconDetail_status_ignored')}</option>
              </select>
              <button onClick={() => refetchLines()} className="btn-secondary"><RefreshCw className="h-4 w-4" /></button>
            </div>
          }
        />

        {linesLoading ? (
          <div className="flex justify-center py-16"><Spinner size="lg" /></div>
        ) : lines.length === 0 ? (
          <EmptyState
            title={t('bank:reconDetail_emptyLinesTitle')}
            description={totalLines === 0 ? t('bank:reconDetail_emptyLinesDescriptionNoData') : t('bank:reconDetail_emptyLinesDescriptionFiltered')}
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>{t('bank:reconDetail_colDate')}</th>
                  <th>{t('bank:reconDetail_colDescription')}</th>
                  <th>{t('bank:reconDetail_colReference')}</th>
                  <th className="right">{t('bank:reconDetail_colDebit')}</th>
                  <th className="right">{t('bank:reconDetail_colCredit')}</th>
                  <th className="right">{t('bank:reconDetail_colBalance')}</th>
                  <th>{t('bank:reconDetail_colStatus')}</th>
                  {!isFinalized && <th>{t('bank:reconDetail_colAction')}</th>}
                </tr>
              </thead>
              <tbody>
                {lines.map((line: any) => (
                  <tr key={line.id}
                    className={selectedBankLine?.id === line.id ? 'bg-blue-50' : ''}>
                    <td className="text-sm text-gray-500 whitespace-nowrap">{formatDate(line.transaction_date)}</td>
                    <td className="text-sm max-w-xs truncate" title={line.description}>{line.description}</td>
                    <td className="text-xs font-mono text-gray-400">{line.reference_no ?? '-'}</td>
                    <td className="right text-sm">
                      {line.debit_amount > 0 ? formatRupiah(line.debit_amount) : '-'}
                    </td>
                    <td className="right text-sm text-green-700">
                      {line.credit_amount > 0 ? formatRupiah(line.credit_amount) : '-'}
                    </td>
                    <td className="right text-sm text-gray-500">
                      {line.running_balance != null ? formatRupiah(line.running_balance) : '-'}
                    </td>
                    <td>
                      <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border
                        ${MATCH_COLORS[line.match_status] ?? MATCH_COLORS['unmatched']}`}>
                        {MATCH_LABELS[line.match_status] ?? line.match_status}
                      </span>
                    </td>
                    {!isFinalized && (
                      <td>
                        <div className="flex items-center gap-1">
                          {(line.match_status === 'unmatched') && (
                            <button
                              onClick={() => {
                                setSelectedBankLine(line)
                                setShowSuggestions(true)
                              }}
                              className="inline-flex items-center gap-1 text-xs px-2 py-1 bg-blue-50 text-blue-600 hover:bg-blue-100 rounded-md">
                              <Link className="h-3 w-3" /> {t('bank:reconDetail_match')}
                            </button>
                          )}
                          {(line.match_status === 'auto_matched') && (
                            <button
                              onClick={() => confirmMutation.mutate({
                                bank_line_id: line.id,
                                gl_line_id: line.gl_line_id,
                              })}
                              className="inline-flex items-center gap-1 text-xs px-2 py-1 bg-green-50 text-green-600 hover:bg-green-100 rounded-md">
                              <CheckCircle className="h-3 w-3" /> {t('bank:reconDetail_confirm')}
                            </button>
                          )}
                          {(line.match_status === 'confirmed' || line.match_status === 'manual_matched') && (
                            <span className="text-xs text-gray-400 flex items-center gap-1">
                              <CheckCircle className="h-3 w-3 text-green-400" /> {t('bank:reconDetail_matched')}
                            </span>
                          )}
                        </div>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* Suggestions panel */}
      {selectedBankLine && showSuggestions && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-xl w-full max-w-2xl max-h-[80vh] flex flex-col">
            <div className="flex items-center justify-between p-4 border-b">
              <div>
                <p className="font-semibold text-gray-900">{t('bank:reconDetail_suggestionsTitle')}</p>
                <p className="text-sm text-gray-500 mt-0.5">
                  {selectedBankLine.description} — {formatDate(selectedBankLine.transaction_date)}
                  &nbsp;·&nbsp;
                  <span className="font-medium">
                    Rp {formatRupiah(selectedBankLine.debit_amount || selectedBankLine.credit_amount)}
                  </span>
                </p>
              </div>
              <button onClick={() => { setSelectedBankLine(null); setShowSuggestions(false) }}
                className="p-2 hover:bg-gray-100 rounded-lg">
                <XCircle className="h-5 w-5 text-gray-400" />
              </button>
            </div>

            <div className="flex-1 overflow-y-auto p-4 space-y-3">
              {sugsLoading ? (
                <div className="flex justify-center py-8"><Spinner /></div>
              ) : suggestions.length === 0 ? (
                <EmptyState title={t('bank:reconDetail_noSuggestionsTitle')} description={t('bank:reconDetail_noSuggestionsDescription')} />
              ) : (
                suggestions.map((sug: any) => (
                  <div key={sug.id ?? sug.gl_line_id}
                    className="border rounded-lg p-3 flex items-center justify-between hover:border-blue-300">
                    <div>
                      <p className="text-sm font-medium">{sug.description ?? sug.narasi}</p>
                      <p className="text-xs text-gray-400 mt-0.5">
                        {formatDate(sug.transaction_date ?? sug.entry_date)}
                        &nbsp;·&nbsp; {t('bank:reconDetail_journalLabel')}: {sug.journal_no ?? sug.entry_no ?? '-'}
                      </p>
                    </div>
                    <div className="flex items-center gap-3">
                      <div className="text-right">
                        <p className="text-sm font-semibold">Rp {formatRupiah(sug.amount ?? sug.debit ?? sug.credit)}</p>
                        <p className="text-xs text-gray-400">{sug.score ? t('bank:reconDetail_scoreLabel', { score: (sug.score * 100).toFixed(0) }) : ''}</p>
                      </div>
                      <button
                        onClick={() => confirmMutation.mutate({
                          bank_line_id: selectedBankLine.id,
                          gl_line_id: sug.id ?? sug.gl_line_id,
                        })}
                        className="btn-primary py-1 px-3 text-sm">
                        {t('bank:reconDetail_choose')}
                      </button>
                    </div>
                  </div>
                ))
              )}

              {/* Manual input */}
              <div className="border rounded-lg p-3">
                <p className="text-sm font-medium text-gray-700 mb-2">{t('bank:reconDetail_manualInputTitle')}</p>
                <div className="flex gap-2">
                  <input value={manualGlId} onChange={e => setManualGlId(e.target.value)}
                    placeholder={t('bank:reconDetail_manualInputPlaceholder')}
                    className="form-input flex-1 text-sm font-mono" />
                  <button
                    onClick={() => manualMatchMutation.mutate({
                      bank_line_id: selectedBankLine.id,
                      gl_line_id: manualGlId,
                    })}
                    disabled={!manualGlId || manualMatchMutation.isPending}
                    className="btn-secondary text-sm">
                    <Link className="h-3 w-3" /> {t('bank:reconDetail_manualMatchButton')}
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* CSV import preview modal */}
      {showImportModal && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-xl w-full max-w-3xl max-h-[80vh] flex flex-col">
            <div className="flex items-center justify-between p-4 border-b">
              <div>
                <p className="font-semibold text-gray-900">{t('bank:reconDetail_importPreviewTitle')}</p>
                <p className="text-sm text-gray-500">{t('bank:reconDetail_importPreviewSubtitle', { count: importPreview.length })}</p>
              </div>
              <button onClick={() => setShowImportModal(false)} className="p-2 hover:bg-gray-100 rounded-lg">
                <XCircle className="h-5 w-5 text-gray-400" />
              </button>
            </div>
            <div className="flex-1 overflow-auto p-4">
              <table className="data-table text-xs">
                <thead>
                  <tr>
                    <th>{t('bank:reconDetail_colDate')}</th>
                    <th>{t('bank:reconDetail_colDescription')}</th>
                    <th>{t('bank:reconDetail_colReference')}</th>
                    <th className="right">{t('bank:reconDetail_colDebit')}</th>
                    <th className="right">{t('bank:reconDetail_colCredit')}</th>
                    <th className="right">{t('bank:reconDetail_colBalance')}</th>
                  </tr>
                </thead>
                <tbody>
                  {importPreview.slice(0, 50).map((row, i) => (
                    <tr key={i}>
                      <td>{row.transaction_date}</td>
                      <td className="max-w-xs truncate">{row.description}</td>
                      <td>{row.reference_no ?? '-'}</td>
                      <td className="right">{row.debit_amount > 0 ? formatRupiah(row.debit_amount) : '-'}</td>
                      <td className="right text-green-700">{row.credit_amount > 0 ? formatRupiah(row.credit_amount) : '-'}</td>
                      <td className="right text-gray-400">{row.running_balance != null ? formatRupiah(row.running_balance) : '-'}</td>
                    </tr>
                  ))}
                  {importPreview.length > 50 && (
                    <tr><td colSpan={6} className="text-center text-gray-400">{t('bank:reconDetail_importMore', { count: importPreview.length - 50 })}</td></tr>
                  )}
                </tbody>
              </table>
            </div>
            <div className="flex gap-3 p-4 border-t">
              <button onClick={() => importMutation.mutate(importPreview)}
                disabled={importMutation.isPending}
                className="btn-primary">
                {importMutation.isPending ? t('bank:reconDetail_importing') : t('bank:reconDetail_importButton', { count: importPreview.length })}
              </button>
              <button onClick={() => setShowImportModal(false)} className="btn-secondary">{t('bank:reconDetail_cancel')}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
