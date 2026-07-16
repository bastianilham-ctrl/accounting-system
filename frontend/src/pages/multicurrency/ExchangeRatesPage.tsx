import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Plus, RefreshCw, Trash2, ArrowRightLeft } from 'lucide-react'
import api from '../../lib/api'
import { Card, CardHeader } from '../../components/ui/Card'
import Spinner from '../../components/ui/Spinner'
import EmptyState from '../../components/ui/EmptyState'
import { formatDate, todayISO } from '../../lib/utils'
import { showToast } from '../../components/ui/Toast'

export default function ExchangeRatesPage() {
  const { t } = useTranslation(['multicurrency', 'common'])
  const qc = useQueryClient()
  const [tab, setTab] = useState<'rates' | 'currencies' | 'convert'>('rates')

  // ── Currencies ───────────────────────────────────────────────────────────────
  const { data: curData, isLoading: curLoading, refetch: refetchCur } = useQuery({
    queryKey: ['currencies'],
    queryFn: () => api.get('/multicurrency/currencies', { params: { active_only: false } }).then(r => r.data),
  })
  const currencies: any[] = Array.isArray(curData) ? curData : []

  const [curForm, setCurForm] = useState({ currency_code: '', currency_name: '', symbol: '', decimal_places: '2' })
  const addCurMutation = useMutation({
    mutationFn: () => api.post('/multicurrency/currencies', {
      currency_code:  curForm.currency_code.toUpperCase(),
      currency_name:  curForm.currency_name,
      symbol:         curForm.symbol || undefined,
      decimal_places: parseInt(curForm.decimal_places) || 2,
    }),
    onSuccess: () => {
      showToast(t('multicurrency:rates_addedSuccess'))
      setCurForm({ currency_code: '', currency_name: '', symbol: '', decimal_places: '2' })
      refetchCur()
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('multicurrency:rates_genericFailed'), 'error'),
  })

  // ── Exchange rates ────────────────────────────────────────────────────────────
  const { data: ratesData, isLoading: ratesLoading, refetch: refetchRates } = useQuery({
    queryKey: ['exchange-rates-latest'],
    queryFn: () => api.get('/multicurrency/exchange-rates/latest').then(r => r.data),
    enabled: tab === 'rates',
  })
  const rates: any[] = Array.isArray(ratesData) ? ratesData : []

  const [rateForm, setRateForm] = useState({
    from_currency: '', to_currency: 'IDR', rate_date: todayISO(),
    rate: '', rate_type: 'middle', source: 'manual',
  })
  const addRateMutation = useMutation({
    mutationFn: () => api.post('/multicurrency/exchange-rates', {
      ...rateForm,
      from_currency: rateForm.from_currency.toUpperCase(),
      to_currency:   rateForm.to_currency.toUpperCase(),
      rate:          parseFloat(rateForm.rate) || 0,
    }),
    onSuccess: () => {
      showToast(t('multicurrency:rates_rateAddedSuccess'))
      setRateForm(f => ({ ...f, rate: '' }))
      qc.invalidateQueries({ queryKey: ['exchange-rates-latest'] })
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('multicurrency:rates_genericFailed'), 'error'),
  })

  const deleteRateMutation = useMutation({
    mutationFn: ({ from, date }: { from: string; date: string }) =>
      api.delete('/multicurrency/exchange-rates', { params: { from_currency: from, rate_date: date } }),
    onSuccess: () => {
      showToast(t('multicurrency:rates_rateDeletedSuccess'), 'warning')
      qc.invalidateQueries({ queryKey: ['exchange-rates-latest'] })
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('multicurrency:rates_rateDeleteFailed'), 'error'),
  })

  // ── Currency converter ────────────────────────────────────────────────────────
  const [convForm, setConvForm] = useState({
    amount: '', from_currency: 'USD', to_currency: 'IDR',
    rate_date: todayISO(), rate_type: 'middle',
  })
  const [convResult, setConvResult] = useState<any>(null)
  const convertMutation = useMutation({
    mutationFn: () => api.post('/multicurrency/convert', {
      ...convForm,
      from_currency: convForm.from_currency.toUpperCase(),
      to_currency:   convForm.to_currency.toUpperCase(),
      amount:        parseFloat(convForm.amount) || 0,
    }).then(r => r.data),
    onSuccess: (data) => setConvResult(data),
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('multicurrency:rates_convertFailed'), 'error'),
  })

  const tabs = [
    { key: 'rates',      label: t('multicurrency:rates_tabRates') },
    { key: 'currencies', label: t('multicurrency:rates_tabCurrencies') },
    { key: 'convert',    label: t('multicurrency:rates_tabConvert') },
  ]

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">{t('multicurrency:rates_pageTitle')}</h1>
        <p className="text-sm text-gray-500 mt-0.5">{t('multicurrency:rates_pageSubtitle')}</p>
      </div>

      {/* Tabs */}
      <div className="border-b border-gray-200">
        <nav className="flex gap-1">
          {tabs.map(tabItem => (
            <button key={tabItem.key}
              onClick={() => setTab(tabItem.key as any)}
              className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                tab === tabItem.key ? 'border-primary-600 text-primary-700' : 'border-transparent text-gray-500 hover:text-gray-700'
              }`}>
              {tabItem.label}
            </button>
          ))}
        </nav>
      </div>

      {/* Tab: Rates */}
      {tab === 'rates' && (
        <div className="space-y-4">
          {/* Add rate form */}
          <Card>
            <p className="text-sm font-semibold text-gray-700 mb-3">{t('multicurrency:rates_newRateInput')}</p>
            <div className="grid grid-cols-2 md:grid-cols-6 gap-3 items-end">
              <div>
                <label className="form-label">{t('multicurrency:rates_from')}</label>
                <input value={rateForm.from_currency}
                  onChange={e => setRateForm({ ...rateForm, from_currency: e.target.value })}
                  className="form-input font-mono uppercase" placeholder="USD" maxLength={5} />
              </div>
              <div>
                <label className="form-label">{t('multicurrency:rates_to')}</label>
                <input value={rateForm.to_currency}
                  onChange={e => setRateForm({ ...rateForm, to_currency: e.target.value })}
                  className="form-input font-mono uppercase" placeholder="IDR" maxLength={5} />
              </div>
              <div>
                <label className="form-label">{t('multicurrency:rates_dateLabel')}</label>
                <input type="date" value={rateForm.rate_date}
                  onChange={e => setRateForm({ ...rateForm, rate_date: e.target.value })}
                  className="form-input" />
              </div>
              <div>
                <label className="form-label">{t('multicurrency:rates_rateLabel')}</label>
                <input type="number" value={rateForm.rate}
                  onChange={e => setRateForm({ ...rateForm, rate: e.target.value })}
                  className="form-input" placeholder="15750" />
              </div>
              <div>
                <label className="form-label">{t('multicurrency:rates_typeLabel')}</label>
                <select value={rateForm.rate_type}
                  onChange={e => setRateForm({ ...rateForm, rate_type: e.target.value })}
                  className="form-select">
                  <option value="middle">{t('multicurrency:rates_typeMiddle')}</option>
                  <option value="buying">{t('multicurrency:rates_typeBuying')}</option>
                  <option value="selling">{t('multicurrency:rates_typeSelling')}</option>
                </select>
              </div>
              <button onClick={() => addRateMutation.mutate()}
                disabled={addRateMutation.isPending || !rateForm.from_currency || !rateForm.rate}
                className="btn-primary">
                <Plus className="h-4 w-4" /> {t('multicurrency:rates_add')}
              </button>
            </div>
          </Card>

          <Card noPad>
            <CardHeader
              title={t('multicurrency:rates_currentRatesTitle')}
              subtitle={`${rates.length} ${t('multicurrency:rates_currencyCountSuffix')}`}
              actions={<button onClick={() => refetchRates()} className="btn-secondary"><RefreshCw className="h-4 w-4" /></button>}
            />
            {ratesLoading ? (
              <div className="flex justify-center py-16"><Spinner size="lg" /></div>
            ) : rates.length === 0 ? (
              <EmptyState title={t('multicurrency:rates_emptyTitle')} description={t('multicurrency:rates_emptyDescription')} />
            ) : (
              <div className="overflow-x-auto">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>{t('multicurrency:rates_colCurrency')}</th>
                      <th>{t('multicurrency:rates_colName')}</th>
                      <th>{t('multicurrency:rates_colDate')}</th>
                      <th className="right">{t('multicurrency:rates_colMiddleRate')}</th>
                      <th className="right">{t('multicurrency:rates_colBuyRate')}</th>
                      <th className="right">{t('multicurrency:rates_colSellRate')}</th>
                      <th>{t('multicurrency:rates_colSource')}</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {rates.map((r: any) => (
                      <tr key={r.from_currency}>
                        <td className="font-mono font-bold text-sm">{r.from_currency}</td>
                        <td className="text-sm">{r.currency_name ?? '—'}</td>
                        <td className="text-sm text-gray-500 whitespace-nowrap">{formatDate(r.rate_date)}</td>
                        <td className="right font-semibold">{r.middle_rate?.toLocaleString('id-ID') ?? r.rate?.toLocaleString('id-ID') ?? '—'}</td>
                        <td className="right text-sm text-gray-500">{r.buying_rate?.toLocaleString('id-ID') ?? '—'}</td>
                        <td className="right text-sm text-gray-500">{r.selling_rate?.toLocaleString('id-ID') ?? '—'}</td>
                        <td className="text-xs text-gray-400">{r.source ?? '—'}</td>
                        <td>
                          <button
                            onClick={() => deleteRateMutation.mutate({ from: r.from_currency, date: r.rate_date })}
                            className="text-gray-300 hover:text-red-400">
                            <Trash2 className="h-4 w-4" />
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </div>
      )}

      {/* Tab: Currencies */}
      {tab === 'currencies' && (
        <div className="space-y-4">
          <Card>
            <p className="text-sm font-semibold text-gray-700 mb-3">{t('multicurrency:rates_addCurrencyTitle')}</p>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 items-end">
              <div>
                <label className="form-label">{t('multicurrency:rates_codeLabel')}</label>
                <input value={curForm.currency_code}
                  onChange={e => setCurForm({ ...curForm, currency_code: e.target.value.toUpperCase() })}
                  className="form-input font-mono uppercase" maxLength={5} />
              </div>
              <div>
                <label className="form-label">{t('multicurrency:rates_currencyNameLabel')}</label>
                <input value={curForm.currency_name}
                  onChange={e => setCurForm({ ...curForm, currency_name: e.target.value })}
                  className="form-input" placeholder={t('multicurrency:rates_currencyNamePlaceholder')} />
              </div>
              <div>
                <label className="form-label">{t('multicurrency:rates_symbolLabel')}</label>
                <input value={curForm.symbol}
                  onChange={e => setCurForm({ ...curForm, symbol: e.target.value })}
                  className="form-input" placeholder="$" maxLength={5} />
              </div>
              <button onClick={() => addCurMutation.mutate()}
                disabled={addCurMutation.isPending || !curForm.currency_code || !curForm.currency_name}
                className="btn-primary">
                <Plus className="h-4 w-4" /> {t('multicurrency:rates_add')}
              </button>
            </div>
          </Card>

          <Card noPad>
            <CardHeader title={t('multicurrency:rates_currencyListTitle')} subtitle={`${currencies.length} ${t('multicurrency:rates_currencyCountSuffix')}`}
              actions={<button onClick={() => refetchCur()} className="btn-secondary"><RefreshCw className="h-4 w-4" /></button>} />
            {curLoading ? (
              <div className="flex justify-center py-12"><Spinner size="lg" /></div>
            ) : (
              <div className="overflow-x-auto">
                <table className="data-table">
                  <thead>
                    <tr><th>{t('multicurrency:rates_colCode')}</th><th>{t('multicurrency:rates_colName')}</th><th>{t('multicurrency:rates_colSymbol')}</th><th>{t('multicurrency:rates_colDecimal')}</th><th>{t('multicurrency:rates_colStatus')}</th></tr>
                  </thead>
                  <tbody>
                    {currencies.map((c: any) => (
                      <tr key={c.currency_code}>
                        <td className="font-mono font-bold">{c.currency_code}</td>
                        <td>{c.currency_name}</td>
                        <td className="font-mono">{c.symbol ?? '—'}</td>
                        <td className="text-center">{c.decimal_places}</td>
                        <td><span className={`text-xs px-2 py-0.5 rounded-full ${c.is_active ? 'bg-green-50 text-green-700' : 'bg-gray-100 text-gray-400'}`}>{c.is_active ? t('multicurrency:rates_statusActive') : t('multicurrency:rates_statusInactive')}</span></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </div>
      )}

      {/* Tab: Convert */}
      {tab === 'convert' && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <Card>
            <p className="text-sm font-semibold text-gray-800 mb-4 flex items-center gap-2">
              <ArrowRightLeft className="h-4 w-4 text-primary-600" /> {t('multicurrency:rates_convertTitle')}
            </p>
            <div className="space-y-3">
              <div className="grid grid-cols-3 gap-2 items-end">
                <div>
                  <label className="form-label">{t('multicurrency:rates_from')}</label>
                  <input value={convForm.from_currency}
                    onChange={e => setConvForm({ ...convForm, from_currency: e.target.value.toUpperCase() })}
                    className="form-input font-mono uppercase text-center" maxLength={5} />
                </div>
                <div className="flex justify-center pb-1">
                  <ArrowRightLeft className="h-5 w-5 text-gray-400" />
                </div>
                <div>
                  <label className="form-label">{t('multicurrency:rates_to')}</label>
                  <input value={convForm.to_currency}
                    onChange={e => setConvForm({ ...convForm, to_currency: e.target.value.toUpperCase() })}
                    className="form-input font-mono uppercase text-center" maxLength={5} />
                </div>
              </div>
              <div>
                <label className="form-label">{t('multicurrency:rates_amountLabel')}</label>
                <input type="number" value={convForm.amount}
                  onChange={e => setConvForm({ ...convForm, amount: e.target.value })}
                  className="form-input" placeholder="1000" />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="form-label">{t('multicurrency:rates_rateDateLabel')}</label>
                  <input type="date" value={convForm.rate_date}
                    onChange={e => setConvForm({ ...convForm, rate_date: e.target.value })}
                    className="form-input" />
                </div>
                <div>
                  <label className="form-label">{t('multicurrency:rates_rateTypeLabel')}</label>
                  <select value={convForm.rate_type}
                    onChange={e => setConvForm({ ...convForm, rate_type: e.target.value })}
                    className="form-select">
                    <option value="middle">{t('multicurrency:rates_typeMiddle')}</option>
                    <option value="buying">{t('multicurrency:rates_typeBuying')}</option>
                    <option value="selling">{t('multicurrency:rates_typeSelling')}</option>
                  </select>
                </div>
              </div>
              <button onClick={() => convertMutation.mutate()}
                disabled={convertMutation.isPending || !convForm.amount || !convForm.from_currency}
                className="btn-primary w-full">
                {convertMutation.isPending ? t('multicurrency:rates_calculating') : t('multicurrency:rates_convert')}
              </button>
            </div>

            {convResult && (
              <div className="mt-4 bg-blue-50 rounded-lg p-4 text-center">
                <p className="text-sm text-gray-600 mb-1">
                  {parseFloat(convForm.amount).toLocaleString()} {convForm.from_currency} =
                </p>
                <p className="text-3xl font-bold text-primary-700">
                  {convResult.converted_amount?.toLocaleString('id-ID', { maximumFractionDigits: 2 })}
                  <span className="text-lg ml-2">{convForm.to_currency}</span>
                </p>
                <p className="text-xs text-gray-400 mt-2">
                  {t('multicurrency:rates_rateInfoPrefix')} {convForm.from_currency} = {convResult.rate?.toLocaleString('id-ID')} {convForm.to_currency}
                  {convResult.rate_date ? ` · ${formatDate(convResult.rate_date)}` : ''}
                </p>
              </div>
            )}
          </Card>

          <Card>
            <p className="text-sm font-semibold text-gray-700 mb-3">{t('multicurrency:rates_activeRatesTitle')}</p>
            {ratesLoading ? <Spinner /> : (
              <div className="space-y-2">
                {rates.slice(0, 10).map((r: any) => (
                  <div key={r.from_currency} className="flex items-center justify-between py-1.5 border-b border-gray-100 last:border-0">
                    <span className="font-mono font-bold text-sm">{r.from_currency} / IDR</span>
                    <div className="text-right">
                      <p className="font-semibold text-sm">{(r.middle_rate ?? r.rate ?? 0).toLocaleString('id-ID')}</p>
                      <p className="text-xs text-gray-400">{formatDate(r.rate_date)}</p>
                    </div>
                  </div>
                ))}
                {rates.length === 0 && <p className="text-sm text-gray-400">{t('multicurrency:rates_noRatesRegistered')}</p>}
              </div>
            )}
          </Card>
        </div>
      )}
    </div>
  )
}
