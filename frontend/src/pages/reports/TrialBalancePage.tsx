import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import { FileText, Download, Search } from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext'
import api, { downloadFile } from '../../lib/api'
import { Card, CardHeader } from '../../components/ui/Card'
import Spinner from '../../components/ui/Spinner'
import EmptyState from '../../components/ui/EmptyState'
import { formatRupiah, currentYear, currentMonth, MONTHS } from '../../lib/utils'

export default function TrialBalancePage() {
  const { entityId } = useAuth()
  const { t } = useTranslation(['reports', 'common'])
  const [year, setYear] = useState(currentYear())
  const [month, setMonth] = useState(currentMonth())
  const [search, setSearch] = useState('')
  const [submitted, setSubmitted] = useState(false)
  const [downloading, setDownloading] = useState<string | null>(null)

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['trial-balance', entityId, year, month],
    queryFn: () =>
      api.get('/financial-reports/trial-balance', {
        params: { entity_id: entityId, fiscal_year: year, fiscal_month: month },
      }).then((r) => r.data),
    enabled: submitted && !!entityId,
  })

  const accounts: any[] = data?.accounts ?? []
  const totals = data?.totals ?? {}
  const filtered = accounts.filter(
    (a) =>
      !search ||
      a.account_code?.toLowerCase().includes(search.toLowerCase()) ||
      a.account_name?.toLowerCase().includes(search.toLowerCase()),
  )

  async function handleDownload(fmt: 'pdf' | 'excel') {
    setDownloading(fmt)
    try {
      const ext = fmt === 'pdf' ? 'pdf' : 'xlsx'
      await downloadFile(
        `/financial-reports/trial-balance?entity_id=${entityId}&fiscal_year=${year}&fiscal_month=${month}&format=${fmt}`,
        `trial_balance_${year}${String(month).padStart(2, '0')}.${ext}`,
      )
    } finally {
      setDownloading(null)
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">{t('reports:tbTitle')}</h1>
        <p className="text-sm text-gray-500 mt-0.5">{t('reports:tbSubtitle')}</p>
      </div>

      {/* Filter */}
      <Card>
        <div className="flex items-end gap-4 flex-wrap">
          <div>
            <label className="form-label">{t('reports:year')}</label>
            <input type="number" value={year} onChange={(e) => setYear(+e.target.value)}
              className="form-input w-24" min={2020} max={2099} />
          </div>
          <div>
            <label className="form-label">{t('reports:month')}</label>
            <select value={month} onChange={(e) => setMonth(+e.target.value)} className="form-select w-36">
              {MONTHS.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
            </select>
          </div>
          <button onClick={() => { setSubmitted(true); refetch() }} disabled={!entityId}
            className="btn-primary">
            <FileText className="h-4 w-4" /> {t('reports:show')}
          </button>
          {data && (
            <>
              <button onClick={() => handleDownload('pdf')} disabled={!!downloading}
                className="btn-secondary">
                <Download className="h-4 w-4" />
                {downloading === 'pdf' ? t('reports:downloading') : 'PDF'}
              </button>
              <button onClick={() => handleDownload('excel')} disabled={!!downloading}
                className="btn-secondary">
                <Download className="h-4 w-4" />
                {downloading === 'excel' ? t('reports:downloading') : 'Excel'}
              </button>
            </>
          )}
        </div>
      </Card>

      {submitted && (
        <Card noPad>
          <CardHeader
            title={t('reports:tbHeaderTitle', { month: MONTHS.find((m) => m.value === month)?.label, year })}
            subtitle={data ? t('reports:tbHeaderSubtitle', { date: data.as_of_date }) : ''}
            actions={
              data && (
                <span className={`text-sm font-medium ${totals.is_balanced ? 'text-green-600' : 'text-red-600'}`}>
                  {totals.is_balanced ? t('reports:balanceOk') : t('reports:balanceDiff', { amount: formatRupiah(totals.difference) })}
                </span>
              )
            }
          />

          {/* Search */}
          {data && (
            <div className="px-6 py-3 border-b border-gray-100">
              <div className="relative max-w-xs">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
                <input value={search} onChange={(e) => setSearch(e.target.value)}
                  placeholder={t('reports:tbFilterPlaceholder')} className="form-input pl-9" />
              </div>
            </div>
          )}

          {isLoading ? (
            <div className="flex justify-center py-16"><Spinner size="lg" /></div>
          ) : !data ? (
            <EmptyState title={t('reports:loadPrompt')} />
          ) : filtered.length === 0 ? (
            <EmptyState title={t('reports:tbNoMatch')} />
          ) : (
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>{t('reports:tbColCode')}</th>
                    <th>{t('reports:tbColAccountName')}</th>
                    <th className="right">{t('reports:tbColOpeningDr')}</th>
                    <th className="right">{t('reports:tbColOpeningCr')}</th>
                    <th className="right">{t('reports:tbColMutationDr')}</th>
                    <th className="right">{t('reports:tbColMutationCr')}</th>
                    <th className="right">{t('reports:tbColClosingDr')}</th>
                    <th className="right">{t('reports:tbColClosingCr')}</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((a) => (
                    <tr key={a.account_code}>
                      <td className="font-mono text-xs">{a.account_code}</td>
                      <td className="text-sm">{a.account_name}</td>
                      <td className="right">{a.opening_debit ? formatRupiah(a.opening_debit) : '-'}</td>
                      <td className="right">{a.opening_credit ? formatRupiah(a.opening_credit) : '-'}</td>
                      <td className="right">{a.period_debit ? formatRupiah(a.period_debit) : '-'}</td>
                      <td className="right">{a.period_credit ? formatRupiah(a.period_credit) : '-'}</td>
                      <td className="right font-medium">{a.closing_debit ? formatRupiah(a.closing_debit) : '-'}</td>
                      <td className="right font-medium">{a.closing_credit ? formatRupiah(a.closing_credit) : '-'}</td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr className="total">
                    <td colSpan={2} className="font-bold">{t('reports:total')}</td>
                    <td className="right">{formatRupiah(totals.opening_debit)}</td>
                    <td className="right">{formatRupiah(totals.opening_credit)}</td>
                    <td className="right">{formatRupiah(totals.period_debit)}</td>
                    <td className="right">{formatRupiah(totals.period_credit)}</td>
                    <td className="right">{formatRupiah(totals.closing_debit)}</td>
                    <td className="right">{formatRupiah(totals.closing_credit)}</td>
                  </tr>
                </tfoot>
              </table>
            </div>
          )}
        </Card>
      )}
    </div>
  )
}
