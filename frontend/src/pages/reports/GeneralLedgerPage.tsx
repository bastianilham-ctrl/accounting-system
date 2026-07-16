import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import { FileText, Download, ChevronDown, ChevronRight } from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext'
import api, { downloadFile } from '../../lib/api'
import { Card, CardHeader } from '../../components/ui/Card'
import Spinner from '../../components/ui/Spinner'
import EmptyState from '../../components/ui/EmptyState'
import { formatRupiah, formatDate, firstDayOfMonth, lastDayOfMonth } from '../../lib/utils'

function AccountLedger({ account }: { account: any }) {
  const { t } = useTranslation(['reports', 'common'])
  const [open, setOpen] = useState(true)
  const txns: any[] = account.transactions ?? []

  return (
    <div className="border-b border-gray-100 last:border-0">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-6 py-3 hover:bg-gray-50 text-left"
      >
        <div className="flex items-center gap-3">
          {open ? <ChevronDown className="h-4 w-4 text-gray-400" /> : <ChevronRight className="h-4 w-4 text-gray-400" />}
          <span className="font-mono text-sm font-medium text-primary-700">{account.account_code}</span>
          <span className="text-sm font-medium text-gray-800">{account.account_name}</span>
          <span className="text-xs text-gray-400 capitalize">({account.account_type})</span>
        </div>
        <div className="flex items-center gap-6 text-sm">
          <span className="text-gray-500">{t('reports:glClosingBalance')}</span>
          <span className={`font-bold ${account.closing_balance >= 0 ? 'text-gray-800' : 'text-red-600'}`}>
            Rp {formatRupiah(account.closing_balance)}
          </span>
        </div>
      </button>

      {open && (
        <div className="overflow-x-auto border-t border-gray-100 bg-gray-50/50">
          <table className="data-table">
            <thead>
              <tr>
                <th>{t('reports:glColDate')}</th><th>{t('reports:glColJournalNo')}</th><th>{t('reports:glColDescription')}</th>
                <th className="right">{t('reports:glColDebit')}</th><th className="right">{t('reports:glColCredit')}</th>
                <th className="right">{t('reports:glColRunningBalance')}</th>
              </tr>
            </thead>
            <tbody>
              <tr className="group-header">
                <td colSpan={5}>{t('reports:glOpeningBalance')}</td>
                <td className="right">{formatRupiah(account.opening_balance)}</td>
              </tr>
              {txns.length === 0 ? (
                <tr><td colSpan={6} className="text-center text-gray-400 py-4 text-sm">{t('reports:glNoTransactions')}</td></tr>
              ) : (
                txns.map((t, i) => (
                  <tr key={i}>
                    <td className="text-sm">{formatDate(t.date)}</td>
                    <td className="font-mono text-xs text-primary-600">{t.journal_number}</td>
                    <td className="text-sm max-w-xs truncate">{t.description}</td>
                    <td className="right">{t.debit ? formatRupiah(t.debit) : '-'}</td>
                    <td className="right">{t.credit ? formatRupiah(t.credit) : '-'}</td>
                    <td className={`right font-medium ${t.balance < 0 ? 'text-red-600' : ''}`}>
                      {formatRupiah(t.balance)}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
            <tfoot>
              <tr className="total">
                <td colSpan={3} className="font-bold">{t('reports:glClosingBalanceFooter')}</td>
                <td className="right">{formatRupiah(account.period_debit)}</td>
                <td className="right">{formatRupiah(account.period_credit)}</td>
                <td className="right">{formatRupiah(account.closing_balance)}</td>
              </tr>
            </tfoot>
          </table>
        </div>
      )}
    </div>
  )
}

export default function GeneralLedgerPage() {
  const { entityId } = useAuth()
  const { t } = useTranslation(['reports', 'common'])
  const [fromDate, setFromDate] = useState(firstDayOfMonth())
  const [toDate, setToDate] = useState(lastDayOfMonth())
  const [accountCode, setAccountCode] = useState('')
  const [accountType, setAccountType] = useState('')
  const [submitted, setSubmitted] = useState(false)
  const [downloading, setDownloading] = useState<string | null>(null)

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['general-ledger', entityId, fromDate, toDate, accountCode, accountType],
    queryFn: () =>
      api.get('/financial-reports/general-ledger', {
        params: {
          entity_id: entityId, from_date: fromDate, to_date: toDate,
          account_code: accountCode || undefined,
          account_type: accountType || undefined,
          size: 200,
        },
      }).then((r) => r.data),
    enabled: submitted && !!entityId,
  })

  const accounts: any[] = data?.accounts ?? []

  async function handleDownload(fmt: 'pdf' | 'excel') {
    setDownloading(fmt)
    try {
      const ext = fmt === 'pdf' ? 'pdf' : 'xlsx'
      const q = new URLSearchParams({
        entity_id: entityId, from_date: fromDate, to_date: toDate, format: fmt,
      })
      if (accountCode) q.append('account_code', accountCode)
      await downloadFile(`/financial-reports/general-ledger?${q}`,
        `buku_besar_${accountCode || 'all'}_${fromDate}.${ext}`)
    } finally { setDownloading(null) }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">{t('reports:glTitle')}</h1>
        <p className="text-sm text-gray-500 mt-0.5">{t('reports:glSubtitle')}</p>
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
          <div>
            <label className="form-label">{t('reports:accountCodeOptional')}</label>
            <input value={accountCode} onChange={(e) => setAccountCode(e.target.value)}
              placeholder={t('reports:glAccountCodePlaceholder')} className="form-input w-32" />
          </div>
          <div>
            <label className="form-label">{t('reports:accountType')}</label>
            <select value={accountType} onChange={(e) => setAccountType(e.target.value)} className="form-select w-36">
              <option value="">{t('reports:glTypeAll')}</option>
              <option value="asset">{t('reports:glTypeAsset')}</option>
              <option value="liability">{t('reports:glTypeLiability')}</option>
              <option value="equity">{t('reports:glTypeEquity')}</option>
              <option value="revenue">{t('reports:glTypeRevenue')}</option>
              <option value="expense">{t('reports:glTypeExpense')}</option>
            </select>
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
        <Card noPad>
          <CardHeader
            title={t('reports:glHeaderTitle')}
            subtitle={data ? t('reports:glHeaderSubtitle', { count: accounts.length, fromDate, toDate }) : ''}
          />
          {isLoading ? (
            <div className="flex justify-center py-16"><Spinner size="lg" /></div>
          ) : accounts.length === 0 ? (
            <EmptyState
              title={submitted ? t('reports:glNoData') : t('reports:loadPrompt')}
            />
          ) : (
            <div>
              {accounts.map((a) => <AccountLedger key={a.account_code} account={a} />)}
            </div>
          )}
        </Card>
      )}
    </div>
  )
}
