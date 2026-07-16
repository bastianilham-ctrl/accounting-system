import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import { FileText, Download } from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext'
import api, { downloadFile } from '../../lib/api'
import { Card, CardHeader } from '../../components/ui/Card'
import Spinner from '../../components/ui/Spinner'
import EmptyState from '../../components/ui/EmptyState'
import { formatRupiah, firstDayOfMonth, lastDayOfMonth } from '../../lib/utils'

function PLSection({ title, data, isSubtraction }: { title: string; data: any; isSubtraction?: boolean }) {
  const { t } = useTranslation(['reports', 'common'])
  return (
    <>
      <tr className="group-header"><td colSpan={2} className="px-4 py-2">{title}</td></tr>
      {data.groups?.map((grp: any) => (
        <>
          {grp.items?.map((item: any) => (
            <tr key={item.account_code} className="hover:bg-gray-50">
              <td className="pl-8 text-sm text-gray-600 py-1.5">
                <span className="font-mono text-xs text-gray-400 mr-2">{item.account_code}</span>
                {item.account_name}
              </td>
              <td className="right text-sm">{formatRupiah(Math.abs(item.balance))}</td>
            </tr>
          ))}
        </>
      ))}
      <tr className="subtotal">
        <td className="pl-4">{t('reports:plSectionTotal', { title })}</td>
        <td className="right">{isSubtraction ? `(${formatRupiah(data.total)})` : formatRupiah(data.total)}</td>
      </tr>
    </>
  )
}

function SubtotalRow({ label, value, highlighted }: { label: string; value: number; highlighted?: boolean }) {
  if (highlighted) {
    return (
      <tr className="net-income">
        <td className="px-4 py-3 font-bold text-sm">{label}</td>
        <td className="right px-4 font-bold">{formatRupiah(value)}</td>
      </tr>
    )
  }
  return (
    <tr className="total">
      <td className="px-4 font-semibold text-sm">{label}</td>
      <td className="right px-4 font-semibold">{formatRupiah(value)}</td>
    </tr>
  )
}

export default function ProfitLossPage() {
  const { entityId } = useAuth()
  const { t } = useTranslation(['reports', 'common'])
  const [fromDate, setFromDate] = useState(firstDayOfMonth())
  const [toDate, setToDate] = useState(lastDayOfMonth())
  const [compareFrom, setCompareFrom] = useState('')
  const [compareTo, setCompareTo] = useState('')
  const [submitted, setSubmitted] = useState(false)
  const [downloading, setDownloading] = useState<string | null>(null)

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['profit-loss', entityId, fromDate, toDate, compareFrom, compareTo],
    queryFn: () =>
      api.get('/financial-reports/profit-loss', {
        params: {
          entity_id: entityId, from_date: fromDate, to_date: toDate,
          compare_from: compareFrom || undefined, compare_to: compareTo || undefined,
        },
      }).then((r) => r.data),
    enabled: submitted && !!entityId,
  })

  const c = data?.current

  async function handleDownload(fmt: 'pdf' | 'excel') {
    setDownloading(fmt)
    try {
      const ext = fmt === 'pdf' ? 'pdf' : 'xlsx'
      const q = new URLSearchParams({ entity_id: entityId, from_date: fromDate, to_date: toDate, format: fmt })
      if (compareFrom) { q.append('compare_from', compareFrom); q.append('compare_to', compareTo) }
      await downloadFile(`/financial-reports/profit-loss?${q}`, `laba_rugi_${fromDate}_${toDate}.${ext}`)
    } finally { setDownloading(null) }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">{t('reports:plTitle')}</h1>
        <p className="text-sm text-gray-500 mt-0.5">{t('reports:plSubtitle')}</p>
      </div>

      <Card>
        <div className="flex items-end gap-4 flex-wrap">
          <div>
            <label className="form-label">{t('reports:fromDate')}</label>
            <input type="date" value={fromDate} onChange={(e) => setFromDate(e.target.value)} className="form-input" />
          </div>
          <div>
            <label className="form-label">{t('reports:toDate')}</label>
            <input type="date" value={toDate} onChange={(e) => setToDate(e.target.value)} className="form-input" />
          </div>
          <div className="border-l border-gray-200 pl-4 flex gap-3">
            <div>
              <label className="form-label text-gray-400">{t('reports:compareFrom')}</label>
              <input type="date" value={compareFrom} onChange={(e) => setCompareFrom(e.target.value)} className="form-input" />
            </div>
            <div>
              <label className="form-label text-gray-400">{t('reports:compareTo')}</label>
              <input type="date" value={compareTo} onChange={(e) => setCompareTo(e.target.value)} className="form-input" />
            </div>
          </div>
          <button onClick={() => { setSubmitted(true); refetch() }} disabled={!entityId}
            className="btn-primary">
            <FileText className="h-4 w-4" /> {t('reports:show')}
          </button>
          {data && (
            <>
              <button onClick={() => handleDownload('pdf')} disabled={!!downloading} className="btn-secondary">
                <Download className="h-4 w-4" />{downloading === 'pdf' ? t('reports:downloading') : 'PDF'}
              </button>
              <button onClick={() => handleDownload('excel')} disabled={!!downloading} className="btn-secondary">
                <Download className="h-4 w-4" />{downloading === 'excel' ? t('reports:downloading') : 'Excel'}
              </button>
            </>
          )}
        </div>
      </Card>

      {submitted && (
        isLoading ? (
          <div className="flex justify-center py-16"><Spinner size="lg" /></div>
        ) : !c ? (
          <EmptyState title={t('reports:loadPrompt')} />
        ) : (
          <Card noPad>
            <CardHeader
              title={t('reports:plTitle')}
              subtitle={t('reports:plHeaderSubtitle', { fromDate, toDate })}
            />
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead><tr><th>{t('reports:plColDescription')}</th><th className="right">{t('reports:plColAmount')}</th></tr></thead>
                <tbody>
                  <PLSection title={t('reports:plPendapatan')} data={c.pendapatan} />
                  <PLSection title={t('reports:plHpp')} data={c.hpp} isSubtraction />
                  <SubtotalRow label={t('reports:plLabaBruto')} value={c.laba_bruto} />
                  <PLSection title={t('reports:plBebanOperasi')} data={c.beban_operasi} isSubtraction />
                  <SubtotalRow label={t('reports:plLabaOperasi')} value={c.laba_operasi} />
                  {c.pendapatan_lain?.groups?.length > 0 && (
                    <PLSection title={t('reports:plPendapatanLain')} data={c.pendapatan_lain} />
                  )}
                  {c.beban_lain?.groups?.length > 0 && (
                    <PLSection title={t('reports:plBebanLain')} data={c.beban_lain} isSubtraction />
                  )}
                  <SubtotalRow label={t('reports:plLabaSebelumPajak')} value={c.laba_sebelum_pajak} />
                  {c.beban_pajak?.groups?.length > 0 && (
                    <PLSection title={t('reports:plBebanPajak')} data={c.beban_pajak} isSubtraction />
                  )}
                </tbody>
                <SubtotalRow label={t('reports:plLabaBersih')} value={c.laba_bersih} highlighted />
              </table>
            </div>
          </Card>
        )
      )}
    </div>
  )
}
