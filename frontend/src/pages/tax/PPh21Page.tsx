import { useState } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Calculator, FileDown, RefreshCw, Info, ChevronDown, ChevronUp } from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext'
import api from '../../lib/api'
import { downloadFile } from '../../lib/api'
import { Card, CardHeader } from '../../components/ui/Card'
import Spinner from '../../components/ui/Spinner'
import EmptyState from '../../components/ui/EmptyState'
import { formatRupiah, formatPercent, currentYear, currentMonth, MONTHS } from '../../lib/utils'
import { showToast } from '../../components/ui/Toast'

// ── Tarif brackets untuk referensi (kode/figure resmi, tidak diterjemahkan) ──
const BRACKETS = [
  { range: 's/d Rp 60.000.000',               rate: '5%' },
  { range: 'Rp 60.000.000 – Rp 250.000.000',  rate: '15%' },
  { range: 'Rp 250.000.000 – Rp 500.000.000', rate: '25%' },
  { range: 'Rp 500.000.000 – Rp 5.000.000.000', rate: '30%' },
  { range: '> Rp 5.000.000.000',              rate: '35%' },
]

export default function PPh21Page() {
  const { t } = useTranslation(['tax', 'common'])
  const { entityId } = useAuth()
  const [year, setYear] = useState(currentYear())
  const [tab, setTab] = useState<'summary' | 'calc'>('summary')

  // ── YTD Summary ──
  const { data: summaryData, isLoading, refetch } = useQuery({
    queryKey: ['pph21-summary', entityId, year],
    queryFn: () =>
      api.get(`/pph21/summary/${entityId}`, { params: { year } }).then(r => r.data),
    enabled: !!entityId,
  })
  const payees: any[] = summaryData?.payees ?? []
  const summary = summaryData?.summary

  // ── Bupot Export ──
  const [bupotMonth, setBupotMonth] = useState(currentMonth())
  const [bupotYear, setBupotYear]   = useState(currentYear())

  const exportBupot = async () => {
    try {
      await downloadFile(
        `/pph21/bupot/${entityId}?year=${bupotYear}&month=${bupotMonth}`,
        `bupot_pph21_${bupotYear}-${String(bupotMonth).padStart(2, '0')}.xlsx`,
      )
    } catch {
      showToast(t('tax:pph21Page_exportFailed'), 'error')
    }
  }

  // ── Kalkulator Simulasi ──
  const [calc, setCalc] = useState({
    gross_amount: '',
    has_npwp: true,
    is_tenaga_ahli: true,
    ytd_gross_before: '',
  })
  const [calcResult, setCalcResult] = useState<any>(null)
  const [showBreakdown, setShowBreakdown] = useState(false)

  const calcMutation = useMutation({
    mutationFn: () =>
      api.post('/pph21/calculate', {
        gross_amount:      parseFloat(calc.gross_amount)      || 0,
        has_npwp:          calc.has_npwp,
        is_tenaga_ahli:    calc.is_tenaga_ahli,
        ytd_gross_before:  parseFloat(calc.ytd_gross_before)  || 0,
      }).then(r => r.data),
    onSuccess: (data) => setCalcResult(data),
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('tax:pph21Page_calcFailed'), 'error'),
  })

  const years = [currentYear(), currentYear() - 1, currentYear() - 2]

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">{t('tax:pph21Page_title')}</h1>
          <p className="text-sm text-gray-500 mt-0.5">
            {t('tax:pph21Page_subtitle')}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select value={year} onChange={e => setYear(+e.target.value)} className="form-select w-24">
            {years.map(y => <option key={y} value={y}>{y}</option>)}
          </select>
          <button onClick={() => refetch()} className="btn-secondary"><RefreshCw className="h-4 w-4" /></button>
        </div>
      </div>

      {/* Summary cards */}
      {summary && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div className="card p-4">
            <p className="text-xs text-gray-500 uppercase tracking-wide">{t('tax:pph21Page_cardPayee')}</p>
            <p className="text-2xl font-bold text-gray-900 mt-1">{summary.total_payees}</p>
            <p className="text-xs text-gray-400 mt-0.5">{t('tax:pph21Page_cardPayeeUnit')}</p>
          </div>
          <div className="card p-4">
            <p className="text-xs text-gray-500 uppercase tracking-wide">{t('tax:pph21Page_cardTotalBrutoYtd')}</p>
            <p className="text-xl font-bold text-gray-900 mt-1">Rp {formatRupiah(summary.total_bruto)}</p>
          </div>
          <div className="card p-4 border-l-4 border-red-400">
            <p className="text-xs text-gray-500 uppercase tracking-wide">{t('tax:pph21Page_cardTotalPph21')}</p>
            <p className="text-xl font-bold text-red-600 mt-1">Rp {formatRupiah(summary.total_pph21)}</p>
          </div>
          <div className="card p-4">
            <p className="text-xs text-gray-500 uppercase tracking-wide">{t('tax:pph21Page_cardEffectiveRate')}</p>
            <p className="text-xl font-bold text-gray-900 mt-1">{summary.effective_rate?.toFixed(2) ?? '0.00'}%</p>
          </div>
        </div>
      )}

      {/* Bupot export bar */}
      <Card>
        <div className="flex flex-wrap items-center gap-4">
          <div className="flex items-center gap-1.5">
            <FileDown className="h-4 w-4 text-primary-600" />
            <span className="text-sm font-medium text-gray-700">{t('tax:pph21Page_exportBupot')}</span>
          </div>
          <div className="flex items-center gap-2 flex-1 min-w-0">
            <select value={bupotMonth} onChange={e => setBupotMonth(+e.target.value)} className="form-select w-36">
              {MONTHS.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
            </select>
            <select value={bupotYear} onChange={e => setBupotYear(+e.target.value)} className="form-select w-24">
              {years.map(y => <option key={y} value={y}>{y}</option>)}
            </select>
            <button onClick={exportBupot} className="btn-primary">
              <FileDown className="h-4 w-4" /> {t('tax:pph21Page_downloadExcel')}
            </button>
          </div>
          <p className="text-xs text-gray-400">{t('tax:pph21Page_exportHint')}</p>
        </div>
      </Card>

      {/* Tabs */}
      <div className="border-b border-gray-200">
        <nav className="flex gap-1">
          {[
            { key: 'summary', label: t('tax:pph21Page_tabSummary') },
            { key: 'calc',    label: t('tax:pph21Page_tabCalc') },
          ].map(tb => (
            <button key={tb.key}
              onClick={() => setTab(tb.key as any)}
              className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                tab === tb.key
                  ? 'border-primary-600 text-primary-700'
                  : 'border-transparent text-gray-500 hover:text-gray-700'
              }`}>
              {tb.label}
            </button>
          ))}
        </nav>
      </div>

      {/* Tab: Summary */}
      {tab === 'summary' && (
        <Card noPad>
          <CardHeader title={t('tax:pph21Page_summaryTitle', { year })} subtitle={t('tax:pph21Page_summarySubtitle', { count: payees.length })} />
          {isLoading ? (
            <div className="flex justify-center py-16"><Spinner size="lg" /></div>
          ) : payees.length === 0 ? (
            <EmptyState title={t('tax:pph21Page_emptyTitle')} description={t('tax:pph21Page_emptyDescription')} />
          ) : (
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>{t('tax:pph21Page_colCode')}</th>
                    <th>{t('tax:pph21Page_colVendorName')}</th>
                    <th>{t('tax:pph21Page_colNpwp')}</th>
                    <th className="right">{t('tax:pph21Page_colInvoiceCount')}</th>
                    <th className="right">{t('tax:pph21Page_colTotalBruto')}</th>
                    <th className="right">{t('tax:pph21Page_colTotalPph21')}</th>
                    <th className="right">{t('tax:pph21Page_colEffectiveRate')}</th>
                  </tr>
                </thead>
                <tbody>
                  {payees.map((p: any) => {
                    const rate = p.total_bruto > 0
                      ? ((p.total_pph21 / p.total_bruto) * 100).toFixed(2)
                      : '0.00'
                    return (
                      <tr key={p.vendor_id}>
                        <td className="font-mono text-xs text-gray-400">{p.vendor_code ?? '-'}</td>
                        <td className="font-medium text-sm">{p.vendor_name}</td>
                        <td className="font-mono text-xs text-gray-500">
                          {p.npwp || <span className="text-amber-500 text-xs">{t('tax:pph21Page_noNpwp')}</span>}
                        </td>
                        <td className="right text-sm">{p.invoice_count}</td>
                        <td className="right">Rp {formatRupiah(p.total_bruto)}</td>
                        <td className="right font-semibold text-red-600">Rp {formatRupiah(p.total_pph21)}</td>
                        <td className="right text-sm text-gray-500">{rate}%</td>
                      </tr>
                    )
                  })}
                </tbody>
                {payees.length > 1 && (
                  <tfoot>
                    <tr className="bg-gray-50 font-semibold">
                      <td colSpan={4} className="text-sm text-gray-600 pl-4 py-2">{t('tax:pph21Page_total')}</td>
                      <td className="right pr-4">Rp {formatRupiah(summary?.total_bruto)}</td>
                      <td className="right text-red-600 pr-4">Rp {formatRupiah(summary?.total_pph21)}</td>
                      <td className="right text-gray-500 pr-4">{summary?.effective_rate?.toFixed(2)}%</td>
                    </tr>
                  </tfoot>
                )}
              </table>
            </div>
          )}
        </Card>
      )}

      {/* Tab: Kalkulator */}
      {tab === 'calc' && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <Card>
            <p className="text-sm font-semibold text-gray-800 mb-4">{t('tax:pph21Page_calcTitle')}</p>
            <div className="space-y-4">
              <div>
                <label className="form-label">{t('tax:pph21Page_calcGrossAmount')}</label>
                <input
                  type="number" value={calc.gross_amount}
                  onChange={e => setCalc({ ...calc, gross_amount: e.target.value })}
                  className="form-input" placeholder="10000000" />
              </div>
              <div>
                <label className="form-label">{t('tax:pph21Page_calcYtdGrossBefore')}</label>
                <input
                  type="number" value={calc.ytd_gross_before}
                  onChange={e => setCalc({ ...calc, ytd_gross_before: e.target.value })}
                  className="form-input" placeholder="0" />
                <p className="text-xs text-gray-400 mt-1">
                  {t('tax:pph21Page_calcYtdGrossBeforeHint')}
                </p>
              </div>
              <div className="flex gap-6">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input type="checkbox" checked={calc.is_tenaga_ahli}
                    onChange={e => setCalc({ ...calc, is_tenaga_ahli: e.target.checked })}
                    className="h-4 w-4 rounded border-gray-300 text-primary-600" />
                  <span className="text-sm text-gray-700">{t('tax:pph21Page_calcTenagaAhli')}</span>
                </label>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input type="checkbox" checked={calc.has_npwp}
                    onChange={e => setCalc({ ...calc, has_npwp: e.target.checked })}
                    className="h-4 w-4 rounded border-gray-300 text-primary-600" />
                  <span className="text-sm text-gray-700">{t('tax:pph21Page_calcHasNpwp')}</span>
                </label>
              </div>
              <button
                onClick={() => calcMutation.mutate()}
                disabled={calcMutation.isPending || !calc.gross_amount}
                className="btn-primary w-full">
                <Calculator className="h-4 w-4" />
                {calcMutation.isPending ? t('tax:pph21Page_calcCalculating') : t('tax:pph21Page_calcButton')}
              </button>
            </div>

            {/* Result */}
            {calcResult && (
              <div className="mt-6 border-t pt-4 space-y-2">
                <div className="flex justify-between text-sm">
                  <span className="text-gray-500">{t('tax:pph21Page_resultGrossAmount')}</span>
                  <span>Rp {formatRupiah(calcResult.gross_amount)}</span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-gray-500">
                    {t('tax:pph21Page_resultPkp', { basis: calcResult.pkp_note?.includes('50%') ? t('tax:pph21Page_resultPkpBasis50') : t('tax:pph21Page_resultPkpBasisFull') })}
                  </span>
                  <span>Rp {formatRupiah(calcResult.pkp)}</span>
                </div>
                {!calcResult.has_npwp && (
                  <div className="flex justify-between text-sm text-amber-600">
                    <span>{t('tax:pph21Page_resultNoNpwpCorrection')}</span>
                    <span>+20%</span>
                  </div>
                )}
                <div className="flex justify-between text-sm font-bold text-red-600 border-t pt-2">
                  <span>{t('tax:pph21Page_resultPph21Cut')}</span>
                  <span>Rp {formatRupiah(calcResult.pph21_amount)}</span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-gray-500">{t('tax:pph21Page_resultEffectiveRate')}</span>
                  <span className="font-medium">{calcResult.effective_rate?.toFixed(2)}%</span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-gray-500">{t('tax:pph21Page_resultYtdGrossAfter')}</span>
                  <span>Rp {formatRupiah(calcResult.ytd_gross_after)}</span>
                </div>

                {/* Breakdown toggle */}
                {calcResult.tax_breakdown?.length > 0 && (
                  <div className="mt-2">
                    <button
                      onClick={() => setShowBreakdown(!showBreakdown)}
                      className="flex items-center gap-1 text-xs text-primary-600 hover:underline">
                      {showBreakdown ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
                      {t('tax:pph21Page_resultBreakdownToggle')}
                    </button>
                    {showBreakdown && (
                      <table className="data-table mt-2 text-xs">
                        <thead>
                          <tr>
                            <th>{t('tax:pph21Page_resultColLayer')}</th>
                            <th className="right">{t('tax:pph21Page_resultColTaxable')}</th>
                            <th className="right">{t('tax:pph21Page_resultColRate')}</th>
                            <th className="right">{t('tax:pph21Page_resultColTax')}</th>
                          </tr>
                        </thead>
                        <tbody>
                          {calcResult.tax_breakdown.map((b: any, i: number) => (
                            <tr key={i}>
                              <td>{b.layer}</td>
                              <td className="right">{formatRupiah(b.taxable_amount)}</td>
                              <td className="right">{b.rate_pct}%</td>
                              <td className="right font-medium">{formatRupiah(b.tax)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </div>
                )}
                <p className="text-xs text-gray-400 mt-1">{calcResult.regulation}</p>
              </div>
            )}
          </Card>

          {/* Tarif reference */}
          <Card>
            <div className="flex items-center gap-2 mb-4">
              <Info className="h-4 w-4 text-primary-500" />
              <p className="text-sm font-semibold text-gray-800">{t('tax:pph21Page_bracketsTitle')}</p>
            </div>
            <table className="data-table text-sm mb-4">
              <thead>
                <tr>
                  <th>{t('tax:pph21Page_bracketsColLayer')}</th>
                  <th className="right">{t('tax:pph21Page_bracketsColRate')}</th>
                </tr>
              </thead>
              <tbody>
                {BRACKETS.map((b, i) => (
                  <tr key={i}>
                    <td>{b.range}</td>
                    <td className="right font-semibold text-primary-700">{b.rate}</td>
                  </tr>
                ))}
              </tbody>
            </table>

            <div className="space-y-2 text-xs text-gray-500">
              <p className="font-semibold text-gray-700">{t('tax:pph21Page_rulesTitle')}</p>
              <p>• <strong>{t('tax:pph21Page_rule1Strong')}</strong> {t('tax:pph21Page_rule1Rest')}</p>
              <p>• <strong>{t('tax:pph21Page_rule2Strong')}</strong>: {t('tax:pph21Page_rule2Rest')}</p>
              <p>• <strong>{t('tax:pph21Page_rule3Strong')}</strong>: {t('tax:pph21Page_rule3Rest')}</p>
              <p>• <strong>{t('tax:pph21Page_rule4Strong')}</strong>: {t('tax:pph21Page_rule4Rest')}</p>
            </div>
          </Card>
        </div>
      )}
    </div>
  )
}
