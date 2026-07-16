import { useState, useMemo, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useQuery, useMutation } from '@tanstack/react-query'
import { Plus, Trash2, ArrowLeft, Save, AlertCircle } from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext'
import api from '../../lib/api'
import { Card, CardHeader } from '../../components/ui/Card'
import Spinner from '../../components/ui/Spinner'
import { formatRupiah, todayISO } from '../../lib/utils'
import { showToast } from '../../components/ui/Toast'
import CurrencySelect from '../../components/shared/CurrencySelect'
import { useLatestRate } from '../../lib/currency'

const JOURNAL_TYPE_VALUES = [
  'general', 'adjustment', 'accrual', 'prepaid',
  'depreciation', 'provision', 'write_off', 'closing',
]

interface Line {
  _key: number
  account_code: string
  description: string
  debit_amount: string
  credit_amount: string
  cost_center: string
  project_id: string
}

let _keyCounter = 0
const newLine = (): Line => ({
  _key: ++_keyCounter,
  account_code: '', description: '', debit_amount: '', credit_amount: '',
  cost_center: '', project_id: '',
})

export default function JournalFormPage() {
  const { entityId, user } = useAuth()
  const navigate = useNavigate()
  const { t } = useTranslation(['journal', 'common', 'multicurrency'])

  // Header state
  const [journalDate, setJournalDate] = useState(todayISO())
  const [journalType, setJournalType] = useState('general')
  const [description, setDescription] = useState('')
  const [referenceNo, setReferenceNo] = useState('')
  const [currency, setCurrency] = useState('IDR')
  const [exchangeRate, setExchangeRate] = useState('1')

  // Lines state — start with 2 empty lines
  const [lines, setLines] = useState<Line[]>([newLine(), newLine()])

  // Auto-fill kurs terbaru saat ganti mata uang (tetap bisa dioverride manual)
  const { rate: latestRate } = useLatestRate(currency)
  useEffect(() => {
    if (currency === 'IDR') { setExchangeRate('1'); return }
    if (latestRate != null) setExchangeRate(String(latestRate))
  }, [currency, latestRate])

  // COA
  const { data: coaData, isLoading: coaLoading } = useQuery({
    queryKey: ['coa', entityId],
    queryFn: () => api.get(`/coa/`, { params: { entity_id: entityId, limit: 1000 } }).then((r) => r.data),
    enabled: !!entityId,
  })
  const accounts: any[] = useMemo(() => {
    const raw = Array.isArray(coaData) ? coaData : (coaData?.accounts ?? [])
    return raw.filter((a: any) => !a.is_header)
  }, [coaData])

  // Lookup name from code
  const accountMap = useMemo(() => {
    const m: Record<string, string> = {}
    accounts.forEach((a: any) => { m[a.account_code] = a.account_name })
    return m
  }, [accounts])

  // Cost Centers (analytic accounting)
  const { data: ccData } = useQuery({
    queryKey: ['cost-centers', entityId],
    queryFn: () => api.get('/projects/cost-centers', { params: { entity_id: entityId } }).then((r) => r.data),
    enabled: !!entityId,
  })
  const costCenters: any[] = Array.isArray(ccData) ? ccData : (ccData?.items ?? [])

  // Projects (analytic accounting)
  const { data: projData } = useQuery({
    queryKey: ['projects-list', entityId],
    queryFn: () => api.get('/projects', { params: { entity_id: entityId, size: 100 } }).then((r) => r.data),
    enabled: !!entityId,
  })
  const projects: any[] = Array.isArray(projData) ? projData : (projData?.items ?? [])

  // Totals
  const totalDebit  = lines.reduce((s, l) => s + (parseFloat(l.debit_amount) || 0), 0)
  const totalCredit = lines.reduce((s, l) => s + (parseFloat(l.credit_amount) || 0), 0)
  const isBalanced  = Math.abs(totalDebit - totalCredit) < 0.01 && totalDebit > 0

  // Line operations
  const updateLine = (key: number, field: keyof Line, value: string) =>
    setLines((prev) => prev.map((l) => l._key === key ? { ...l, [field]: value } : l))

  const addLine = () => setLines((prev) => [...prev, newLine()])

  const removeLine = (key: number) =>
    setLines((prev) => prev.length > 2 ? prev.filter((l) => l._key !== key) : prev)

  // Quick balance: set one side automatically when only 2 lines
  const autoBalance = (key: number, field: 'debit_amount' | 'credit_amount', value: string) => {
    updateLine(key, field, value)
    if (lines.length === 2) {
      const otherKey = lines.find((l) => l._key !== key)?._key
      if (otherKey != null) {
        const otherField = field === 'debit_amount' ? 'credit_amount' : 'debit_amount'
        setLines((prev) => prev.map((l) => {
          if (l._key === key) return { ...l, [field]: value }
          if (l._key === otherKey) return { ...l, [otherField]: value, [field === 'debit_amount' ? 'debit_amount' : 'credit_amount']: '' }
          return l
        }))
        return
      }
    }
    updateLine(key, field, value)
  }

  // Submit
  const mutation = useMutation({
    mutationFn: (body: object) => api.post('/journal-entries', body),
    onSuccess: () => {
      showToast(t('journal:createSuccess'))
      navigate('/journals')
    },
    onError: (e: any) => {
      const detail = e?.response?.data?.detail
      showToast(
        typeof detail === 'string' ? detail : JSON.stringify(detail) ?? t('journal:createFailed'),
        'error',
      )
    },
  })

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!isBalanced) { showToast(t('journal:notBalanced'), 'error'); return }
    const validLines = lines.filter((l) => l.account_code && (parseFloat(l.debit_amount) > 0 || parseFloat(l.credit_amount) > 0))
    if (validLines.length < 2) { showToast(t('journal:minLines'), 'error'); return }

    mutation.mutate({
      entity_id:    entityId,
      journal_date: journalDate,
      journal_type: journalType,
      description,
      reference_no: referenceNo || undefined,
      currency,
      exchange_rate: parseFloat(exchangeRate) || 1,
      lines: validLines.map((l) => ({
        account_code:  l.account_code,
        description:   l.description || description,
        debit_amount:  parseFloat(l.debit_amount) || 0,
        credit_amount: parseFloat(l.credit_amount) || 0,
        cost_center:   l.cost_center || undefined,
        project_id:    l.project_id || undefined,
      })),
    })
  }

  if (coaLoading) {
    return <div className="flex justify-center py-16"><Spinner size="lg" /></div>
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-4">
        <button type="button" onClick={() => navigate('/journals')}
          className="btn-secondary">
          <ArrowLeft className="h-4 w-4" />
        </button>
        <div>
          <h1 className="text-2xl font-bold text-gray-900">{t('journal:createTitle')}</h1>
          <p className="text-sm text-gray-500 mt-0.5">{t('journal:createSubtitle')}</p>
        </div>
      </div>

      {/* Header form */}
      <Card>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div>
            <label className="form-label">{t('common:date')}</label>
            <input type="date" value={journalDate} onChange={(e) => setJournalDate(e.target.value)}
              className="form-input" required />
          </div>
          <div>
            <label className="form-label">{t('journal:journalType')}</label>
            <select value={journalType} onChange={(e) => setJournalType(e.target.value)}
              className="form-select">
              {JOURNAL_TYPE_VALUES.map((v) => (
                <option key={v} value={v}>{t(`journal:type_${v}`)}</option>
              ))}
            </select>
          </div>
          <div className="col-span-2">
            <label className="form-label">{t('journal:descriptionLabel')} <span className="text-red-500">*</span></label>
            <input value={description} onChange={(e) => setDescription(e.target.value)}
              className="form-input" placeholder={t('journal:descriptionPlaceholder')} required minLength={3} />
          </div>
          <div>
            <label className="form-label">{t('journal:referenceNo')}</label>
            <input value={referenceNo} onChange={(e) => setReferenceNo(e.target.value)}
              className="form-input" placeholder={t('common:optional')} />
          </div>
          <div>
            <label className="form-label">{t('multicurrency:invoice_currencyLabel')}</label>
            <CurrencySelect value={currency} onChange={setCurrency} />
          </div>
          {currency !== 'IDR' && (
            <div>
              <label className="form-label">{t('multicurrency:invoice_exchangeRateLabel')}</label>
              <input type="number" value={exchangeRate} onChange={(e) => setExchangeRate(e.target.value)}
                min={0} step="0.000001" className="form-input" />
            </div>
          )}
        </div>
      </Card>

      {/* Lines */}
      <Card noPad>
        <CardHeader title={t('journal:linesTitle')} subtitle={t('journal:linesSubtitle')} />

        {/* datalist for autocomplete */}
        <datalist id="coa-list">
          {accounts.map((a: any) => (
            <option key={a.account_code} value={a.account_code}>{a.account_name}</option>
          ))}
        </datalist>

        <div className="overflow-x-auto">
          <table className="data-table">
            <thead>
              <tr>
                <th style={{ width: 130 }}>{t('journal:accountCode')}</th>
                <th>{t('journal:accountName')}</th>
                <th>{t('journal:lineDescription')}</th>
                <th style={{ width: 140 }}>{t('journal:costCenter')}</th>
                <th style={{ width: 160 }}>{t('journal:project')}</th>
                <th className="right" style={{ width: 160 }}>{t('journal:debit')}</th>
                <th className="right" style={{ width: 160 }}>{t('journal:credit')}</th>
                <th style={{ width: 40 }}></th>
              </tr>
            </thead>
            <tbody>
              {lines.map((line, idx) => (
                <tr key={line._key}>
                  <td className="py-1.5">
                    <input
                      list="coa-list"
                      value={line.account_code}
                      onChange={(e) => updateLine(line._key, 'account_code', e.target.value)}
                      placeholder="1-1110"
                      className="form-input py-1.5 text-sm font-mono"
                    />
                  </td>
                  <td className="py-1.5 text-sm text-gray-500">
                    {accountMap[line.account_code] ?? (
                      line.account_code ? <span className="text-red-400 text-xs">{t('journal:accountNotFound')}</span> : ''
                    )}
                  </td>
                  <td className="py-1.5">
                    <input
                      value={line.description}
                      onChange={(e) => updateLine(line._key, 'description', e.target.value)}
                      placeholder={t('journal:lineDescPlaceholder')}
                      className="form-input py-1.5 text-sm"
                    />
                  </td>
                  <td className="py-1.5">
                    <select
                      value={line.cost_center}
                      onChange={(e) => updateLine(line._key, 'cost_center', e.target.value)}
                      className="form-select py-1.5 text-sm"
                    >
                      <option value="">—</option>
                      {costCenters.map((cc: any) => (
                        <option key={cc.cc_code} value={cc.cc_code}>{cc.cc_name}</option>
                      ))}
                    </select>
                  </td>
                  <td className="py-1.5">
                    <select
                      value={line.project_id}
                      onChange={(e) => updateLine(line._key, 'project_id', e.target.value)}
                      className="form-select py-1.5 text-sm"
                    >
                      <option value="">—</option>
                      {projects.map((p: any) => (
                        <option key={p.id} value={p.id}>{p.project_name}</option>
                      ))}
                    </select>
                  </td>
                  <td className="py-1.5">
                    <input
                      type="number"
                      value={line.debit_amount}
                      onChange={(e) => autoBalance(line._key, 'debit_amount', e.target.value)}
                      min={0}
                      step="0.01"
                      placeholder="0"
                      className="form-input py-1.5 text-sm text-right font-mono"
                    />
                  </td>
                  <td className="py-1.5">
                    <input
                      type="number"
                      value={line.credit_amount}
                      onChange={(e) => autoBalance(line._key, 'credit_amount', e.target.value)}
                      min={0}
                      step="0.01"
                      placeholder="0"
                      className="form-input py-1.5 text-sm text-right font-mono"
                    />
                  </td>
                  <td className="py-1.5 text-center">
                    <button type="button" onClick={() => removeLine(line._key)}
                      disabled={lines.length <= 2}
                      className="text-gray-300 hover:text-red-500 disabled:cursor-not-allowed transition-colors">
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className={isBalanced ? 'total' : 'subtotal'}>
                <td colSpan={5} className="py-2.5">
                  <button type="button" onClick={addLine}
                    className="inline-flex items-center gap-1.5 text-sm text-primary-600 hover:text-primary-700 font-medium">
                    <Plus className="h-4 w-4" /> {t('journal:addLine')}
                  </button>
                </td>
                <td className="right py-2.5">
                  <span className="font-mono font-bold">{formatRupiah(totalDebit)}</span>
                </td>
                <td className="right py-2.5">
                  <span className="font-mono font-bold">{formatRupiah(totalCredit)}</span>
                </td>
                <td></td>
              </tr>
            </tfoot>
          </table>
        </div>

        {/* Balance indicator */}
        <div className="px-6 py-3 border-t border-gray-100 flex items-center justify-between">
          <div className="flex items-center gap-2">
            {totalDebit > 0 && !isBalanced && (
              <div className="flex items-center gap-2 text-sm text-red-600">
                <AlertCircle className="h-4 w-4" />
                {t('journal:difference')} {formatRupiah(Math.abs(totalDebit - totalCredit))}
                {' '}({totalDebit > totalCredit ? t('journal:debitGreater') : t('journal:creditGreater')})
              </div>
            )}
            {isBalanced && (
              <div className="flex items-center gap-2 text-sm text-green-600 font-medium">
                ✓ {t('journal:balanced')} {formatRupiah(totalDebit)}
              </div>
            )}
          </div>
          <div className="flex items-center gap-3">
            <button type="button" onClick={() => navigate('/journals')} className="btn-secondary">
              {t('common:cancel')}
            </button>
            <button type="submit" disabled={!isBalanced || mutation.isPending}
              className="btn-primary">
              {mutation.isPending ? <><Spinner size="sm" /> {t('common:saving')}</> : <><Save className="h-4 w-4" /> {t('common:saveDraft')}</>}
            </button>
          </div>
        </div>
      </Card>
    </form>
  )
}
