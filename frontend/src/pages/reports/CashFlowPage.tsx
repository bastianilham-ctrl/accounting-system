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

function CFSection({ title, items, total, isPositiveGood = true }: {
  title: string
  items: any[]
  total: number
  isPositiveGood?: boolean
}) {
  const { t } = useTranslation(['reports', 'common'])
  return (
    <>
      <tr className="group-header"><td colSpan={2} className="px-4 py-2">{title}</td></tr>
      {items.map((item, i) => (
        <tr key={i} className="hover:bg-gray-50">
          <td className="pl-8 text-sm text-gray-600 py-1.5">
            <span className="font-mono text-xs text-gray-400 mr-2">{item.account_code}</span>
            {item.account_name}
          </td>
          <td className={`right text-sm ${item.amount >= 0 ? 'text-green-600' : 'text-red-600'}`}>
            {item.amount >= 0 ? '' : '('}{formatRupiah(Math.abs(item.amount))}{item.amount < 0 ? ')' : ''}
          </td>
        </tr>
      ))}
      <tr className="subtotal">
        <td className="pl-4 font-semibold">{t('reports:plSectionTotal', { title })}</td>
        <td className={`right font-semibold ${total >= 0 ? 'text-green-700' : 'text-red-600'}`}>
          {total >= 0 ? '' : '('}{formatRupiah(Math.abs(total))}{total < 0 ? ')' : ''}
        </td>
      </tr>
    </>
  )
}

export default function CashFlowPage() {
  const { entityId } = useAuth()
  const { t } = useTranslation(['reports', 'common'])
  const [fromDate, setFromDate] = useState(firstDayOfMonth())
  const [toDate, setToDate] = useState(lastDayOfMonth())
  const [submitted, setSubmitted] = useState(false)
  const [downloading, setDownloading] = useState<string | null>(null)

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['cash-flow', entityId, fromDate, toDate],
    queryFn: () =>
      api.get('/financial-reports/cash-flow', {
        params: { entity_id: entityId, from_date: fromDate, to_date: toDate },
      }).then((r) => r.data),
    enabled: submitted && !!entityId,
  })

  const c = data?.current

  async function handleDownload(fmt: 'pdf' | 'excel') {
    setDownloading(fmt)
    try {
      const ext = fmt === 'pdf' ? 'pdf' : 'xlsx'
      await downloadFile(
        `/financial-reports/cash-flow?entity_id=${entityId}&from_date=${fromDate}&to_date=${toDate}&format=${fmt}`,
        `arus_kas_${fromDate}_${toDate}.${ext}`,
      )
    } finally { setDownloading(null) }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">{t('reports:cfTitle')}</h1>
        <p className="text-sm text-gray-500 mt-0.5">{t('reports:cfSubtitle')}</p>
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
          <div className="grid grid-cols-1 xl:grid-cols-3 gap-4 mb-4">
            {/* Summary cards */}
            {[
              { label: t('reports:cfOperasi'), value: c.operasi.total, color: 'border-l-4 border-primary-500' },
              { label: t('reports:cfInvestasi'), value: c.investasi.total, color: 'border-l-4 border-amber-500' },
              { label: t('reports:cfPendanaan'), value: c.pendanaan.total, color: 'border-l-4 border-purple-500' },
            ].map((item) => (
              <div key={item.label} className={`card p-4 ${item.color}`}>
                <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">{item.label}</p>
                <p className={`text-xl font-bold mt-1 ${item.value >= 0 ? 'text-gray-900' : 'text-red-600'}`}>
                  {item.value >= 0 ? 'Rp ' : '(Rp '}{formatRupiah(Math.abs(item.value))}{item.value < 0 ? ')' : ''}
                </p>
              </div>
            ))}
          </div>
        )
      )}

      {submitted && c && (
        <Card noPad>
          <CardHeader title={t('reports:cfTitle')} subtitle={t('reports:cfHeaderSubtitle', { fromDate, toDate })} />
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead><tr><th>{t('reports:cfColDescription')}</th><th className="right">{t('reports:cfColAmount')}</th></tr></thead>
              <tbody>
                {/* Operasi */}
                <tr className="group-header">
                  <td colSpan={2} className="px-4 py-2 font-semibold">{t('reports:cfOperasiHeader')}</td>
                </tr>
                <tr className="hover:bg-gray-50">
                  <td className="pl-8 text-sm py-1.5">{t('reports:cfLabaBersih')}</td>
                  <td className="right">{formatRupiah(c.operasi.laba_bersih)}</td>
                </tr>
                <tr className="hover:bg-gray-50">
                  <td className="pl-8 text-sm py-1.5">{t('reports:cfDepresiasi')}</td>
                  <td className="right">{formatRupiah(c.operasi.depresiasi)}</td>
                </tr>
                <tr className="group-header">
                  <td colSpan={2} className="pl-8 py-1.5 text-xs italic font-normal text-gray-500">
                    {t('reports:cfPerubahanModalKerja')}
                  </td>
                </tr>
                {c.operasi.perubahan_modal_kerja?.map((item: any, i: number) => (
                  <tr key={i} className="hover:bg-gray-50">
                    <td className="pl-10 text-sm text-gray-600 py-1">
                      <span className="font-mono text-xs text-gray-400 mr-2">{item.account_code}</span>
                      {item.account_name}
                    </td>
                    <td className={`right text-sm ${item.amount >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                      {item.amount >= 0 ? '' : '('}{formatRupiah(Math.abs(item.amount))}{item.amount < 0 ? ')' : ''}
                    </td>
                  </tr>
                ))}
                <tr className="subtotal">
                  <td className="pl-4">{t('reports:cfTotalOperasi')}</td>
                  <td className={`right font-semibold ${c.operasi.total >= 0 ? 'text-green-700' : 'text-red-600'}`}>
                    {c.operasi.total >= 0 ? '' : '('}{formatRupiah(Math.abs(c.operasi.total))}{c.operasi.total < 0 ? ')' : ''}
                  </td>
                </tr>

                {/* Investasi */}
                <CFSection
                  title={t('reports:cfInvestasiHeader')}
                  items={c.investasi.items ?? []}
                  total={c.investasi.total}
                />

                {/* Pendanaan */}
                <CFSection
                  title={t('reports:cfPendanaanHeader')}
                  items={c.pendanaan.items ?? []}
                  total={c.pendanaan.total}
                />

                {/* Rekonsiliasi */}
                <tr style={{ height: 8 }}><td colSpan={2}></td></tr>
                <tr className="subtotal">
                  <td>{t('reports:cfSaldoKasAwal')}</td>
                  <td className="right">{formatRupiah(c.saldo_kas_awal)}</td>
                </tr>
                <tr className="subtotal">
                  <td>{t('reports:cfKenaikanKas')}</td>
                  <td className={`right ${c.kenaikan_kas >= 0 ? 'text-green-700' : 'text-red-600'}`}>
                    {c.kenaikan_kas >= 0 ? '' : '('}{formatRupiah(Math.abs(c.kenaikan_kas))}{c.kenaikan_kas < 0 ? ')' : ''}
                  </td>
                </tr>
              </tbody>
              <tfoot>
                <tr className="net-income">
                  <td className="px-4 py-3">{t('reports:cfSaldoKasAkhir')}</td>
                  <td className="right px-4 font-bold">{formatRupiah(c.saldo_kas_akhir)}</td>
                </tr>
              </tfoot>
            </table>
          </div>
        </Card>
      )}
    </div>
  )
}
