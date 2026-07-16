import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { FileDown, RefreshCw, TrendingUp, TrendingDown, Minus } from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext'
import api, { downloadFile } from '../../lib/api'
import { Card, CardHeader } from '../../components/ui/Card'
import Spinner from '../../components/ui/Spinner'
import EmptyState from '../../components/ui/EmptyState'
import { formatRupiah, formatDate, currentYear, currentMonth, MONTHS } from '../../lib/utils'
import { showToast } from '../../components/ui/Toast'

function useStatusCfg() {
  const { t } = useTranslation(['tax', 'common'])
  const STATUS_CFG: Record<string, { label: string; icon: JSX.Element; cls: string }> = {
    kurang_bayar: {
      label: t('tax:ppnPage_statusKurangBayar'),
      icon: <TrendingUp className="h-4 w-4" />,
      cls: 'text-red-600 bg-red-50 border-red-200',
    },
    lebih_bayar: {
      label: t('tax:ppnPage_statusLebihBayar'),
      icon: <TrendingDown className="h-4 w-4" />,
      cls: 'text-green-700 bg-green-50 border-green-200',
    },
    nihil: {
      label: t('tax:ppnPage_statusNihil'),
      icon: <Minus className="h-4 w-4" />,
      cls: 'text-gray-500 bg-gray-50 border-gray-200',
    },
  }
  return STATUS_CFG
}

export default function PPNPage() {
  const { t } = useTranslation(['tax', 'common'])
  const { entityId } = useAuth()
  const STATUS_CFG = useStatusCfg()
  const [year, setYear]   = useState(currentYear())
  const [month, setMonth] = useState(currentMonth())
  const [activeTab, setActiveTab] = useState<'ytd' | 'masa'>('ytd')

  // YTD per bulan
  const { data: ytdData, isLoading: ytdLoading, refetch: refetchYtd } = useQuery({
    queryKey: ['ppn-ytd', entityId, year],
    queryFn: () =>
      api.get(`/ppn/ytd/${entityId}`, { params: { year } }).then(r => r.data),
    enabled: !!entityId,
  })
  const perBulan: any[] = ytdData?.per_bulan ?? []
  const ytdSummary      = ytdData?.ytd_summary

  // Masa rekonsiliasi
  const { data: masaData, isLoading: masaLoading, refetch: refetchMasa } = useQuery({
    queryKey: ['ppn-masa', entityId, year, month],
    queryFn: () =>
      api.get(`/ppn/reconcile/${entityId}`, { params: { year, month } }).then(r => r.data),
    enabled: !!entityId && activeTab === 'masa',
  })

  const exportSPT = async () => {
    try {
      await downloadFile(
        `/ppn/export-spt/${entityId}?year=${year}&month=${month}`,
        `spt_masa_ppn_${year}-${String(month).padStart(2, '0')}.xlsx`,
      )
    } catch {
      showToast(t('tax:ppnPage_exportFailed'), 'error')
    }
  }

  const years = [currentYear(), currentYear() - 1, currentYear() - 2]

  // Compute max for bar chart scaling
  const maxVal = Math.max(...perBulan.map(b => Math.max(b.ppn_keluaran, b.ppn_masukan)), 1)

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">{t('tax:ppnPage_title')}</h1>
          <p className="text-sm text-gray-500 mt-0.5">{t('tax:ppnPage_subtitle')}</p>
        </div>
        <div className="flex items-center gap-2">
          <select value={year} onChange={e => setYear(+e.target.value)} className="form-select w-24">
            {years.map(y => <option key={y} value={y}>{y}</option>)}
          </select>
          <button onClick={() => { refetchYtd(); refetchMasa() }} className="btn-secondary">
            <RefreshCw className="h-4 w-4" />
          </button>
        </div>
      </div>

      {/* YTD summary cards */}
      {ytdSummary && (
        <div className="grid grid-cols-3 gap-4">
          <div className="card p-4">
            <p className="text-xs text-gray-500 uppercase tracking-wide">{t('tax:ppnPage_cardKeluaranYtd')}</p>
            <p className="text-xl font-bold text-gray-900 mt-1">Rp {formatRupiah(ytdSummary.total_ppn_keluaran)}</p>
            <p className="text-xs text-gray-400 mt-0.5">{t('tax:ppnPage_cardKeluaranHint')}</p>
          </div>
          <div className="card p-4">
            <p className="text-xs text-gray-500 uppercase tracking-wide">{t('tax:ppnPage_cardMasukanYtd')}</p>
            <p className="text-xl font-bold text-gray-900 mt-1">Rp {formatRupiah(ytdSummary.total_ppn_masukan)}</p>
            <p className="text-xs text-gray-400 mt-0.5">{t('tax:ppnPage_cardMasukanHint')}</p>
          </div>
          <div className={`card p-4 border-l-4 ${ytdSummary.net_ppn > 0 ? 'border-red-400' : ytdSummary.net_ppn < 0 ? 'border-green-400' : 'border-gray-300'}`}>
            <p className="text-xs text-gray-500 uppercase tracking-wide">{t('tax:ppnPage_cardNetYtd')}</p>
            <p className={`text-xl font-bold mt-1 ${ytdSummary.net_ppn > 0 ? 'text-red-600' : ytdSummary.net_ppn < 0 ? 'text-green-700' : 'text-gray-600'}`}>
              Rp {formatRupiah(Math.abs(ytdSummary.net_ppn))}
            </p>
            <p className="text-xs text-gray-400 mt-0.5">
              {ytdSummary.net_ppn > 0 ? t('tax:ppnPage_netKurangBayar') : ytdSummary.net_ppn < 0 ? t('tax:ppnPage_netLebihBayar') : t('tax:ppnPage_netNihil')}
            </p>
          </div>
        </div>
      )}

      {/* Tabs */}
      <div className="border-b border-gray-200">
        <nav className="flex gap-1">
          {[
            { key: 'ytd',  label: t('tax:ppnPage_tabYtd', { year }) },
            { key: 'masa', label: t('tax:ppnPage_tabMasa') },
          ].map(tb => (
            <button key={tb.key}
              onClick={() => setActiveTab(tb.key as any)}
              className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                activeTab === tb.key
                  ? 'border-primary-600 text-primary-700'
                  : 'border-transparent text-gray-500 hover:text-gray-700'
              }`}>
              {tb.label}
            </button>
          ))}
        </nav>
      </div>

      {/* Tab: YTD */}
      {activeTab === 'ytd' && (
        <Card noPad>
          <CardHeader title={t('tax:ppnPage_ytdTitle', { year })} />
          {ytdLoading ? (
            <div className="flex justify-center py-16"><Spinner size="lg" /></div>
          ) : (
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>{t('tax:ppnPage_colMonth')}</th>
                    <th className="right">{t('tax:ppnPage_colKeluaran')}</th>
                    <th className="right">{t('tax:ppnPage_colMasukan')}</th>
                    <th className="right">{t('tax:ppnPage_colSelisih')}</th>
                    <th>{t('common:status')}</th>
                    <th>{t('tax:ppnPage_colVisualization')}</th>
                  </tr>
                </thead>
                <tbody>
                  {perBulan.map((b: any) => {
                    const cfg = STATUS_CFG[b.status] ?? STATUS_CFG.nihil
                    const kPct = Math.round((b.ppn_keluaran / maxVal) * 100)
                    const mPct = Math.round((b.ppn_masukan  / maxVal) * 100)
                    const hasData = b.ppn_keluaran > 0 || b.ppn_masukan > 0
                    return (
                      <tr key={b.bulan}
                        className={hasData ? 'cursor-pointer hover:bg-blue-50' : 'text-gray-300'}
                        onClick={() => { if (hasData) { setMonth(b.bulan); setActiveTab('masa') } }}>
                        <td className="font-medium text-sm">{b.nama_bulan}</td>
                        <td className="right text-sm">
                          {hasData ? `Rp ${formatRupiah(b.ppn_keluaran)}` : '—'}
                        </td>
                        <td className="right text-sm">
                          {hasData ? `Rp ${formatRupiah(b.ppn_masukan)}` : '—'}
                        </td>
                        <td className={`right text-sm font-semibold ${b.selisih > 0 ? 'text-red-600' : b.selisih < 0 ? 'text-green-700' : ''}`}>
                          {hasData ? `Rp ${formatRupiah(Math.abs(b.selisih))}` : '—'}
                        </td>
                        <td>
                          {hasData && (
                            <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium border ${cfg.cls}`}>
                              {cfg.icon} {cfg.label}
                            </span>
                          )}
                        </td>
                        <td className="min-w-[140px]">
                          {hasData && (
                            <div className="space-y-1">
                              <div className="flex items-center gap-1">
                                <span className="text-xs text-gray-400 w-4">K</span>
                                <div className="flex-1 h-1.5 bg-gray-100 rounded-full">
                                  <div className="h-full bg-blue-500 rounded-full" style={{ width: `${kPct}%` }} />
                                </div>
                              </div>
                              <div className="flex items-center gap-1">
                                <span className="text-xs text-gray-400 w-4">M</span>
                                <div className="flex-1 h-1.5 bg-gray-100 rounded-full">
                                  <div className="h-full bg-green-500 rounded-full" style={{ width: `${mPct}%` }} />
                                </div>
                              </div>
                            </div>
                          )}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      )}

      {/* Tab: Masa rekonsiliasi */}
      {activeTab === 'masa' && (
        <div className="space-y-4">
          {/* Controls */}
          <div className="flex items-center gap-3">
            <select value={month} onChange={e => setMonth(+e.target.value)} className="form-select w-36">
              {MONTHS.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
            </select>
            <button onClick={exportSPT} className="btn-secondary">
              <FileDown className="h-4 w-4" /> {t('tax:ppnPage_exportExcel')}
            </button>
            <p className="text-xs text-gray-400">{t('tax:ppnPage_exportHint')}</p>
          </div>

          {masaLoading ? (
            <div className="flex justify-center py-16"><Spinner size="lg" /></div>
          ) : masaData ? (
            <>
              {/* Summary */}
              <div className="grid grid-cols-3 gap-4">
                <div className="card p-4">
                  <p className="text-xs text-gray-500 uppercase">{t('tax:ppnPage_cardKeluaranLabel')}</p>
                  <p className="text-xl font-bold text-gray-900 mt-1">
                    Rp {formatRupiah(masaData.ppn_keluaran?.total)}
                  </p>
                  <p className="text-xs text-gray-400 mt-0.5">{t('tax:ppnPage_invoiceCount', { count: masaData.ppn_keluaran?.count })}</p>
                </div>
                <div className="card p-4">
                  <p className="text-xs text-gray-500 uppercase">{t('tax:ppnPage_cardMasukanLabel')}</p>
                  <p className="text-xl font-bold text-gray-900 mt-1">
                    Rp {formatRupiah(masaData.ppn_masukan?.total)}
                  </p>
                  <p className="text-xs text-gray-400 mt-0.5">{t('tax:ppnPage_invoiceCount', { count: masaData.ppn_masukan?.count })}</p>
                </div>
                <div className={`card p-4 border-l-4 ${
                  masaData.summary?.status === 'kurang_bayar' ? 'border-red-400' :
                  masaData.summary?.status === 'lebih_bayar'  ? 'border-green-400' : 'border-gray-300'}`}>
                  <p className="text-xs text-gray-500 uppercase">
                    {STATUS_CFG[masaData.summary?.status]?.label ?? t('tax:ppnPage_statusNihil')}
                  </p>
                  <p className={`text-xl font-bold mt-1 ${
                    masaData.summary?.status === 'kurang_bayar' ? 'text-red-600' :
                    masaData.summary?.status === 'lebih_bayar'  ? 'text-green-700' : 'text-gray-600'}`}>
                    Rp {formatRupiah(Math.abs(masaData.summary?.selisih ?? 0))}
                  </p>
                  <p className="text-xs text-gray-400 mt-0.5 line-clamp-2">{masaData.summary?.keterangan}</p>
                </div>
              </div>

              {/* Keluaran detail */}
              <Card noPad>
                <CardHeader
                  title={t('tax:ppnPage_keluaranDetailTitle')}
                  subtitle={t('tax:ppnPage_invoiceCount', { count: masaData.ppn_keluaran?.count ?? 0 })}
                />
                {masaData.ppn_keluaran?.detail?.length === 0 ? (
                  <EmptyState title={t('tax:ppnPage_emptyKeluaran')} />
                ) : (
                  <InvoiceTable rows={masaData.ppn_keluaran?.detail ?? []} counterpartyLabel={t('tax:ppnPage_counterpartyBuyer')} />
                )}
              </Card>

              {/* Masukan detail */}
              <Card noPad>
                <CardHeader
                  title={t('tax:ppnPage_masukanDetailTitle')}
                  subtitle={t('tax:ppnPage_invoiceCount', { count: masaData.ppn_masukan?.count ?? 0 })}
                />
                {masaData.ppn_masukan?.detail?.length === 0 ? (
                  <EmptyState title={t('tax:ppnPage_emptyMasukan')} />
                ) : (
                  <InvoiceTable rows={masaData.ppn_masukan?.detail ?? []} counterpartyLabel={t('tax:ppnPage_counterpartySupplier')} />
                )}
              </Card>
            </>
          ) : null}
        </div>
      )}
    </div>
  )
}

function InvoiceTable({ rows, counterpartyLabel }: { rows: any[]; counterpartyLabel: string }) {
  const { t } = useTranslation(['tax', 'common'])
  const total_dpp = rows.reduce((s, r) => s + (r.dpp ?? 0), 0)
  const total_ppn = rows.reduce((s, r) => s + (r.ppn_amount ?? 0), 0)
  return (
    <div className="overflow-x-auto">
      <table className="data-table">
        <thead>
          <tr>
            <th>{t('tax:ppnPage_colInvoiceNo')}</th>
            <th>{t('tax:ppnPage_colDate')}</th>
            <th>{counterpartyLabel}</th>
            <th>{t('tax:ppnPage_colNpwp')}</th>
            <th className="right">{t('tax:ppnPage_colDpp')}</th>
            <th className="right">{t('tax:ppnPage_colPpn')}</th>
            <th>{t('tax:ppnPage_colStatus')}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r: any, i: number) => (
            <tr key={i}>
              <td className="font-mono text-xs">{r.invoice_no}</td>
              <td className="text-sm text-gray-500 whitespace-nowrap">{formatDate(r.invoice_date)}</td>
              <td className="text-sm">{r.customer_name ?? r.vendor_name}</td>
              <td className="font-mono text-xs text-gray-400">
                {r.npwp || <span className="text-amber-500">{t('tax:ppnPage_noNpwp')}</span>}
              </td>
              <td className="right">Rp {formatRupiah(r.dpp)}</td>
              <td className="right font-medium">Rp {formatRupiah(r.ppn_amount)}</td>
              <td><span className="text-xs text-gray-500 capitalize">{r.status}</span></td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr className="bg-gray-50 font-semibold">
            <td colSpan={4} className="text-sm text-gray-600 pl-4 py-2">{t('tax:ppnPage_total')}</td>
            <td className="right pr-4">Rp {formatRupiah(total_dpp)}</td>
            <td className="right pr-4">Rp {formatRupiah(total_ppn)}</td>
            <td />
          </tr>
        </tfoot>
      </table>
    </div>
  )
}
