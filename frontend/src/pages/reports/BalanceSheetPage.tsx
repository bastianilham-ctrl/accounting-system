import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import { FileText, Download, ChevronDown, ChevronRight } from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext'
import api, { downloadFile } from '../../lib/api'
import { Card, CardHeader } from '../../components/ui/Card'
import Spinner from '../../components/ui/Spinner'
import EmptyState from '../../components/ui/EmptyState'
import { formatRupiah, todayISO, lastDayOfMonth } from '../../lib/utils'

function SectionGroup({ groups }: { groups: any[] }) {
  const { t } = useTranslation(['reports', 'common'])
  const [openGroups, setOpenGroups] = useState<Set<string>>(new Set())
  const toggleGroup = (name: string) =>
    setOpenGroups((s) => { const n = new Set(s); n.has(name) ? n.delete(name) : n.add(name); return n })

  return (
    <>
      {groups.map((grp) => (
        <tbody key={grp.group_name}>
          <tr className="group-header cursor-pointer select-none"
            onClick={() => toggleGroup(grp.group_name)}>
            <td colSpan={2} className="flex items-center gap-1 py-2 px-4">
              {openGroups.has(grp.group_name)
                ? <ChevronDown className="h-3 w-3" />
                : <ChevronRight className="h-3 w-3" />}
              {grp.group_name}
            </td>
          </tr>
          {openGroups.has(grp.group_name) && grp.items?.map((item: any) => (
            <tr key={item.account_code} className="hover:bg-gray-50">
              <td className="pl-10 text-sm text-gray-600 py-1.5">
                <span className="font-mono text-xs text-gray-400 mr-2">{item.account_code}</span>
                {item.account_name}
              </td>
              <td className="right text-sm">{formatRupiah(item.balance)}</td>
            </tr>
          ))}
          <tr className="subtotal">
            <td className="pl-4 text-sm">{t('reports:bsGroupTotal', { group: grp.group_name })}</td>
            <td className="right">{formatRupiah(grp.subtotal)}</td>
          </tr>
        </tbody>
      ))}
    </>
  )
}

export default function BalanceSheetPage() {
  const { entityId } = useAuth()
  const { t } = useTranslation(['reports', 'common'])
  const [asOfDate, setAsOfDate] = useState(lastDayOfMonth())
  const [compareDate, setCompareDate] = useState('')
  const [submitted, setSubmitted] = useState(false)
  const [downloading, setDownloading] = useState<string | null>(null)

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['balance-sheet', entityId, asOfDate, compareDate],
    queryFn: () =>
      api.get('/financial-reports/balance-sheet', {
        params: { entity_id: entityId, as_of_date: asOfDate, compare_date: compareDate || undefined },
      }).then((r) => r.data),
    enabled: submitted && !!entityId,
  })

  const c = data?.current

  async function handleDownload(fmt: 'pdf' | 'excel') {
    setDownloading(fmt)
    try {
      const ext = fmt === 'pdf' ? 'pdf' : 'xlsx'
      const q = new URLSearchParams({ entity_id: entityId, as_of_date: asOfDate, format: fmt })
      if (compareDate) q.append('compare_date', compareDate)
      await downloadFile(`/financial-reports/balance-sheet?${q}`, `neraca_${asOfDate}.${ext}`)
    } finally { setDownloading(null) }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">{t('reports:bsTitle')}</h1>
        <p className="text-sm text-gray-500 mt-0.5">{t('reports:bsSubtitle')}</p>
      </div>

      <Card>
        <div className="flex items-end gap-4 flex-wrap">
          <div>
            <label className="form-label">{t('reports:asOfDate')}</label>
            <input type="date" value={asOfDate} onChange={(e) => setAsOfDate(e.target.value)}
              className="form-input" />
          </div>
          <div>
            <label className="form-label">{t('reports:compareDateOptional')}</label>
            <input type="date" value={compareDate} onChange={(e) => setCompareDate(e.target.value)}
              className="form-input" />
          </div>
          <button onClick={() => { setSubmitted(true); refetch() }} disabled={!entityId || !asOfDate}
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
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
            {/* AKTIVA */}
            <Card noPad>
              <CardHeader title={t('reports:bsAktiva')} subtitle={t('reports:bsAktivaSubtitle', { date: asOfDate })} />
              <div className="overflow-x-auto">
                <table className="data-table">
                  <thead><tr><th>{t('reports:bsColAccount')}</th><th className="right">{t('reports:bsColAmount')}</th></tr></thead>
                  <tr className="group-header"><td colSpan={2} className="px-4 py-2">{t('reports:bsAktivaLancar')}</td></tr>
                  <SectionGroup groups={c.aktiva.aktiva_lancar.groups} />
                  <tbody>
                    <tr className="subtotal">
                      <td>{t('reports:bsTotalAktivaLancar')}</td>
                      <td className="right">{formatRupiah(c.aktiva.aktiva_lancar.total)}</td>
                    </tr>
                  </tbody>
                  <tr className="group-header"><td colSpan={2} className="px-4 py-2">{t('reports:bsAktivaTetapLain')}</td></tr>
                  <SectionGroup groups={c.aktiva.aktiva_tetap.groups} />
                  <tbody>
                    <tr className="subtotal">
                      <td>{t('reports:bsTotalAktivaTetap')}</td>
                      <td className="right">{formatRupiah(c.aktiva.aktiva_tetap.total)}</td>
                    </tr>
                    <tr className="total">
                      <td>{t('reports:bsTotalAktiva')}</td>
                      <td className="right">{formatRupiah(c.aktiva.total)}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </Card>

            {/* PASIVA */}
            <Card noPad>
              <CardHeader
                title={t('reports:bsPasiva')}
                subtitle={c.is_balanced ? t('reports:balanceOk') : t('reports:balanceDiff', { amount: formatRupiah(c.difference) })}
              />
              <div className="overflow-x-auto">
                <table className="data-table">
                  <thead><tr><th>{t('reports:bsColAccount')}</th><th className="right">{t('reports:bsColAmount')}</th></tr></thead>
                  <tr className="group-header"><td colSpan={2} className="px-4 py-2">{t('reports:bsKewajibanLancar')}</td></tr>
                  <SectionGroup groups={c.pasiva.kewajiban_lancar.groups} />
                  <tbody>
                    <tr className="subtotal">
                      <td>{t('reports:bsTotalKewajibanLancar')}</td>
                      <td className="right">{formatRupiah(c.pasiva.kewajiban_lancar.total)}</td>
                    </tr>
                  </tbody>
                  <tr className="group-header"><td colSpan={2} className="px-4 py-2">{t('reports:bsKewajibanJangkaPanjang')}</td></tr>
                  <SectionGroup groups={c.pasiva.kewajiban_jangka_panjang.groups} />
                  <tbody>
                    <tr className="subtotal">
                      <td>{t('reports:bsTotalKewajibanJangkaPanjang')}</td>
                      <td className="right">{formatRupiah(c.pasiva.kewajiban_jangka_panjang.total)}</td>
                    </tr>
                  </tbody>
                  <tr className="group-header"><td colSpan={2} className="px-4 py-2">{t('reports:bsEkuitas')}</td></tr>
                  <SectionGroup groups={c.pasiva.ekuitas.groups} />
                  <tbody>
                    <tr className="hover:bg-gray-50">
                      <td className="pl-4 text-sm text-gray-600">{t('reports:bsLabaPeriodeBerjalan')}</td>
                      <td className="right text-sm">{formatRupiah(c.pasiva.ekuitas.laba_periode_berjalan)}</td>
                    </tr>
                    <tr className="subtotal">
                      <td>{t('reports:bsTotalEkuitas')}</td>
                      <td className="right">{formatRupiah(c.pasiva.ekuitas.total)}</td>
                    </tr>
                    <tr className="total">
                      <td>{t('reports:bsTotalPasiva')}</td>
                      <td className="right">{formatRupiah(c.pasiva.total)}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </Card>
          </div>
        )
      )}
    </div>
  )
}
