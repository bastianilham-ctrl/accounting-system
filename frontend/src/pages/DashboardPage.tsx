import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell, Legend,
} from 'recharts'
import {
  TrendingUp, TrendingDown, DollarSign, CreditCard,
  AlertCircle, RefreshCw, Wallet,
} from 'lucide-react'
import { useAuth } from '../contexts/AuthContext'
import api from '../lib/api'
import { formatRupiah, formatPercent, todayISO } from '../lib/utils'
import Spinner from '../components/ui/Spinner'
import { Card, CardHeader } from '../components/ui/Card'

const COLORS = ['#1a56db', '#f59e0b', '#ef4444', '#10b981']

function KpiCard({
  title, value, sub, trend, icon, colorClass, trendLabel,
}: {
  title: string
  value: string
  sub?: string
  trend?: number | null
  icon: React.ReactNode
  colorClass: string
  trendLabel: string
}) {
  return (
    <div className="card p-5 flex items-start gap-4">
      <div className={`h-11 w-11 rounded-xl flex items-center justify-center flex-shrink-0 ${colorClass}`}>
        {icon}
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">{title}</p>
        <p className="text-xl font-bold text-gray-900 mt-0.5 truncate">{value}</p>
        {sub && <p className="text-xs text-gray-400 mt-0.5">{sub}</p>}
        {trend != null && (
          <div className={`flex items-center gap-1 mt-1 text-xs font-medium ${trend >= 0 ? 'text-green-600' : 'text-red-500'}`}>
            {trend >= 0
              ? <TrendingUp className="h-3 w-3" />
              : <TrendingDown className="h-3 w-3" />
            }
            {formatPercent(trend)} {trendLabel}
          </div>
        )}
      </div>
    </div>
  )
}

function formatMillions(val: number) {
  if (Math.abs(val) >= 1_000_000_000) return `${(val / 1_000_000_000).toFixed(1)}M`
  if (Math.abs(val) >= 1_000_000) return `${(val / 1_000_000).toFixed(0)}jt`
  return formatRupiah(val)
}

export default function DashboardPage() {
  const { entityId } = useAuth()
  const { t, i18n } = useTranslation(['dashboard', 'common'])
  const today = todayISO()

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['dashboard', entityId, today],
    queryFn: () => api.get(`/dashboard/?entity_id=${entityId}&as_of_date=${today}`).then((r) => r.data),
    enabled: !!entityId,
  })

  if (!entityId) {
    return (
      <div className="flex flex-col items-center justify-center h-64 text-center">
        <AlertCircle className="h-12 w-12 text-gray-300 mb-4" />
        <p className="text-gray-500 font-medium">{t('dashboard:noEntityTitle')}</p>
        <p className="text-sm text-gray-400 mt-1">{t('dashboard:noEntityDescription')}</p>
      </div>
    )
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Spinner size="lg" />
      </div>
    )
  }

  if (isError) {
    return (
      <div className="flex flex-col items-center justify-center h-64 text-center">
        <AlertCircle className="h-12 w-12 text-red-300 mb-4" />
        <p className="text-gray-500 font-medium">{t('dashboard:loadFailed')}</p>
        <button onClick={() => refetch()} className="btn-secondary mt-4">
          <RefreshCw className="h-4 w-4" /> {t('dashboard:tryAgain')}
        </button>
      </div>
    )
  }

  const kpi = data?.kpi_cards ?? {}
  const plTrend: any[] = data?.pl_trend ?? []
  const arAging = data?.ar_aging ?? {}
  const apAging = data?.ap_aging ?? {}

  // AR Aging pie data
  const arPieData = [
    { name: t('dashboard:days1_30'), value: Math.abs(arAging['1_30'] ?? 0) },
    { name: t('dashboard:days31_60'), value: Math.abs(arAging['31_60'] ?? 0) },
    { name: t('dashboard:days61_90'), value: Math.abs(arAging['61_90'] ?? 0) },
    { name: t('dashboard:daysOver90'), value: Math.abs(arAging['over_90'] ?? 0) },
  ].filter((d) => d.value > 0)

  const apPieData = [
    { name: t('dashboard:days1_30'), value: Math.abs(apAging['1_30'] ?? 0) },
    { name: t('dashboard:days31_60'), value: Math.abs(apAging['31_60'] ?? 0) },
    { name: t('dashboard:days61_90'), value: Math.abs(apAging['61_90'] ?? 0) },
    { name: t('dashboard:daysOver90'), value: Math.abs(apAging['over_90'] ?? 0) },
  ].filter((d) => d.value > 0)

  return (
    <div className="space-y-6">
      {/* Page title */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">{t('dashboard:title')}</h1>
          <p className="text-sm text-gray-500 mt-0.5">{t('dashboard:asOf')} {new Date().toLocaleDateString(i18n.language === 'id' ? 'id-ID' : 'en-US', { day: 'numeric', month: 'long', year: 'numeric' })}</p>
        </div>
        <button onClick={() => refetch()} className="btn-secondary">
          <RefreshCw className="h-4 w-4" /> {t('dashboard:refresh')}
        </button>
      </div>

      {/* KPI Cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <KpiCard
          title={t('dashboard:revenueMtd')}
          value={`Rp ${formatMillions(kpi.revenue_mtd ?? 0)}`}
          sub="MTD"
          trend={kpi.revenue_mom_pct}
          trendLabel={t('dashboard:vsLastMonth')}
          icon={<TrendingUp className="h-5 w-5 text-green-600" />}
          colorClass="bg-green-100"
        />
        <KpiCard
          title={t('dashboard:netIncomeMtd')}
          value={`Rp ${formatMillions(kpi.net_income_mtd ?? 0)}`}
          sub={`${t('dashboard:margin')}: ${((kpi.net_income_mtd ?? 0) / Math.max(kpi.revenue_mtd ?? 1, 1) * 100).toFixed(1)}%`}
          trend={kpi.net_income_mom_pct}
          trendLabel={t('dashboard:vsLastMonth')}
          icon={<DollarSign className="h-5 w-5 text-primary-600" />}
          colorClass="bg-primary-50"
        />
        <KpiCard
          title={t('dashboard:cashBalance')}
          value={`Rp ${formatMillions(kpi.cash_balance ?? 0)}`}
          sub={t('dashboard:totalBankAccounts')}
          trendLabel={t('dashboard:vsLastMonth')}
          icon={<Wallet className="h-5 w-5 text-blue-600" />}
          colorClass="bg-blue-100"
        />
        <KpiCard
          title={t('dashboard:arOutstanding')}
          value={`Rp ${formatMillions(kpi.ar_outstanding ?? 0)}`}
          sub={`${t('dashboard:overdue')}: Rp ${formatMillions(kpi.ar_overdue ?? 0)}`}
          trendLabel={t('dashboard:vsLastMonth')}
          icon={<CreditCard className="h-5 w-5 text-amber-600" />}
          colorClass="bg-amber-100"
        />
      </div>

      {/* P&L Trend Chart */}
      <Card noPad>
        <CardHeader title={t('dashboard:plTrendTitle')} subtitle={t('dashboard:plTrendSubtitle')} />
        <div className="p-6">
          {plTrend.length > 0 ? (
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={plTrend} margin={{ top: 0, right: 0, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f3f4f6" />
                <XAxis dataKey="month_label" tick={{ fontSize: 11, fill: '#6b7280' }} />
                <YAxis tickFormatter={formatMillions} tick={{ fontSize: 11, fill: '#6b7280' }} />
                <Tooltip
                  formatter={(val: number) => `Rp ${formatRupiah(val)}`}
                  labelStyle={{ fontWeight: 600 }}
                />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Bar dataKey="revenue" name={t('dashboard:revenue')} fill="#1a56db" radius={[3, 3, 0, 0]} />
                <Bar dataKey="expense" name={t('dashboard:expense')} fill="#e5e7eb" radius={[3, 3, 0, 0]} />
                <Bar dataKey="net_income" name={t('dashboard:netIncome')} fill="#10b981" radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <div className="flex items-center justify-center h-40 text-gray-400 text-sm">
              {t('dashboard:noPlData')}
            </div>
          )}
        </div>
      </Card>

      {/* AR & AP Aging */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card noPad>
          <CardHeader
            title={t('dashboard:arAgingTitle')}
            subtitle={`${t('common:total')}: Rp ${formatMillions(kpi.ar_outstanding ?? 0)}`}
          />
          <div className="p-6">
            {arPieData.length > 0 ? (
              <ResponsiveContainer width="100%" height={200}>
                <PieChart>
                  <Pie data={arPieData} dataKey="value" nameKey="name" cx="50%" cy="50%"
                    outerRadius={80} label={({ name, percent }) => `${name}: ${(percent * 100).toFixed(0)}%`}
                    labelLine={false}>
                    {arPieData.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
                  </Pie>
                  <Tooltip formatter={(val: number) => `Rp ${formatRupiah(val)}`} />
                </PieChart>
              </ResponsiveContainer>
            ) : (
              <div className="flex items-center justify-center h-40 text-gray-400 text-sm">{t('dashboard:noArOutstanding')}</div>
            )}
          </div>
        </Card>

        <Card noPad>
          <CardHeader
            title={t('dashboard:apAgingTitle')}
            subtitle={`${t('common:total')}: Rp ${formatMillions(kpi.ap_outstanding ?? 0)}`}
          />
          <div className="p-6">
            {apPieData.length > 0 ? (
              <ResponsiveContainer width="100%" height={200}>
                <PieChart>
                  <Pie data={apPieData} dataKey="value" nameKey="name" cx="50%" cy="50%"
                    outerRadius={80} label={({ name, percent }) => `${name}: ${(percent * 100).toFixed(0)}%`}
                    labelLine={false}>
                    {apPieData.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
                  </Pie>
                  <Tooltip formatter={(val: number) => `Rp ${formatRupiah(val)}`} />
                </PieChart>
              </ResponsiveContainer>
            ) : (
              <div className="flex items-center justify-center h-40 text-gray-400 text-sm">{t('dashboard:noApOutstanding')}</div>
            )}
          </div>
        </Card>
      </div>
    </div>
  )
}
