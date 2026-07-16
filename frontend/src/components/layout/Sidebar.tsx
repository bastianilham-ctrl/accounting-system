import { NavLink, useLocation } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import {
  LayoutDashboard, BookOpen, FileText, TrendingUp, ShoppingCart,
  BarChart2, Users, Settings, ChevronDown, ChevronRight, DollarSign,
  Briefcase, CreditCard, Receipt, Calculator, PieChart, ClipboardList,
  Package, FolderKanban, Warehouse,
} from 'lucide-react'
import { useState } from 'react'
import { cn } from '../../lib/utils'

interface NavItem {
  labelKey: string
  href?: string
  icon?: React.ReactNode
  children?: NavItem[]
}

const NAV: NavItem[] = [
  {
    labelKey: 'dashboard',
    href: '/dashboard',
    icon: <LayoutDashboard className="h-4 w-4" />,
  },
  {
    labelKey: 'accounting',
    icon: <BookOpen className="h-4 w-4" />,
    children: [
      { labelKey: 'coa', href: '/coa' },
      { labelKey: 'journals', href: '/journals' },
    ],
  },
  {
    labelKey: 'sales',
    icon: <TrendingUp className="h-4 w-4" />,
    children: [
      { labelKey: 'salesOrders', href: '/sales-orders' },
      { labelKey: 'arInvoices', href: '/ar/invoices' },
      { labelKey: 'arPayments', href: '/ar/payments' },
    ],
  },
  {
    labelKey: 'inventory',
    href: '/inventory',
    icon: <Warehouse className="h-4 w-4" />,
  },
  {
    labelKey: 'purchasing',
    icon: <ShoppingCart className="h-4 w-4" />,
    children: [
      { labelKey: 'apInvoices', href: '/ap/invoices' },
      { labelKey: 'apPayments', href: '/ap/payments' },
    ],
  },
  {
    labelKey: 'purchaseRequisition',
    href: '/pr',
    icon: <ClipboardList className="h-4 w-4" />,
  },
  {
    labelKey: 'purchaseOrder',
    href: '/po',
    icon: <ShoppingCart className="h-4 w-4" />,
  },
  {
    labelKey: 'itemMaster',
    href: '/item-master',
    icon: <Package className="h-4 w-4" />,
  },
  {
    labelKey: 'projects',
    href: '/projects',
    icon: <FolderKanban className="h-4 w-4" />,
  },
  {
    labelKey: 'finance',
    icon: <CreditCard className="h-4 w-4" />,
    children: [
      { labelKey: 'cashBank', href: '/cash-bank' },
      { labelKey: 'bank', href: '/bank' },
      { labelKey: 'bankRecon', href: '/bank-recon' },
      { labelKey: 'expenseClaims', href: '/expense-claims' },
    ],
  },
  {
    labelKey: 'budget',
    href: '/budget',
    icon: <PieChart className="h-4 w-4" />,
  },
  {
    labelKey: 'financialReports',
    icon: <BarChart2 className="h-4 w-4" />,
    children: [
      { labelKey: 'trialBalance', href: '/reports/trial-balance' },
      { labelKey: 'balanceSheet', href: '/reports/balance-sheet' },
      { labelKey: 'profitLoss', href: '/reports/profit-loss' },
      { labelKey: 'generalLedger', href: '/reports/general-ledger' },
      { labelKey: 'cashFlow', href: '/reports/cash-flow' },
    ],
  },
  {
    labelKey: 'hr',
    icon: <Users className="h-4 w-4" />,
    children: [
      { labelKey: 'employees', href: '/employees' },
      { labelKey: 'payroll', href: '/payroll' },
      { labelKey: 'attendance', href: '/attendance' },
    ],
  },
  {
    labelKey: 'tax',
    icon: <Receipt className="h-4 w-4" />,
    children: [
      { labelKey: 'pph21', href: '/tax/pph21' },
      { labelKey: 'withholding', href: '/tax/withholding' },
      { labelKey: 'ppn', href: '/tax/ppn' },
    ],
  },
  {
    labelKey: 'assets',
    icon: <Briefcase className="h-4 w-4" />,
    children: [
      { labelKey: 'assetList', href: '/assets' },
      { labelKey: 'depreciation', href: '/assets/depreciation' },
    ],
  },
  {
    labelKey: 'multicurrency',
    icon: <DollarSign className="h-4 w-4" />,
    children: [
      { labelKey: 'exchangeRates', href: '/multicurrency/rates' },
      { labelKey: 'revaluation', href: '/multicurrency/revaluation' },
    ],
  },
  {
    labelKey: 'settings',
    icon: <Settings className="h-4 w-4" />,
    children: [
      { labelKey: 'users', href: '/settings/users' },
      { labelKey: 'entities', href: '/settings/entities' },
    ],
  },
]

function NavGroup({ item }: { item: NavItem }) {
  const { t } = useTranslation('nav')
  const location = useLocation()
  const isActive = item.children?.some((c) => c.href && location.pathname.startsWith(c.href))
  const [open, setOpen] = useState(isActive ?? false)

  if (!item.children) {
    return (
      <NavLink
        to={item.href!}
        className={({ isActive }) =>
          cn(
            'flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors',
            isActive
              ? 'bg-primary-600 text-white'
              : 'text-gray-400 hover:bg-gray-800 hover:text-white',
          )
        }
      >
        {item.icon}
        {t(item.labelKey)}
      </NavLink>
    )
  }

  return (
    <div>
      <button
        onClick={() => setOpen(!open)}
        className={cn(
          'w-full flex items-center justify-between gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors',
          isActive ? 'text-white' : 'text-gray-400 hover:bg-gray-800 hover:text-white',
        )}
      >
        <span className="flex items-center gap-3">
          {item.icon}
          {t(item.labelKey)}
        </span>
        {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
      </button>
      {open && (
        <div className="ml-7 mt-1 space-y-0.5">
          {item.children!.map((child) => (
            <NavLink
              key={child.href}
              to={child.href!}
              className={({ isActive }) =>
                cn(
                  'flex items-center px-3 py-1.5 rounded-md text-sm transition-colors',
                  isActive
                    ? 'text-white font-medium'
                    : 'text-gray-500 hover:text-gray-300',
                )
              }
            >
              {t(child.labelKey)}
            </NavLink>
          ))}
        </div>
      )}
    </div>
  )
}

export default function Sidebar() {
  const { t } = useTranslation('nav')
  return (
    <aside className="fixed left-0 top-0 h-screen w-60 bg-gray-900 flex flex-col z-30 border-r border-gray-800">
      {/* Logo */}
      <div className="flex items-center gap-3 px-4 py-5 border-b border-gray-800">
        <div className="h-8 w-8 bg-primary-600 rounded-lg flex items-center justify-center">
          <Calculator className="h-5 w-5 text-white" />
        </div>
        <div>
          <p className="text-sm font-bold text-white leading-none">{t('appName')}</p>
          <p className="text-xs text-gray-500 mt-0.5">{t('appVersion')}</p>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 overflow-y-auto py-4 px-3 space-y-0.5">
        {NAV.map((item) => (
          <NavGroup key={item.labelKey} item={item} />
        ))}
      </nav>
    </aside>
  )
}
