import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { AuthProvider, useAuth } from './contexts/AuthContext'
import Layout from './components/layout/Layout'
import Spinner from './components/ui/Spinner'
import { ToastContainer } from './components/ui/Toast'

// Pages
import LoginPage from './pages/LoginPage'
import DashboardPage from './pages/DashboardPage'
import COAPage from './pages/coa/COAPage'
import ARInvoicePage from './pages/ar/ARInvoicePage'
import APInvoicePage from './pages/ap/APInvoicePage'
import TrialBalancePage from './pages/reports/TrialBalancePage'
import BalanceSheetPage from './pages/reports/BalanceSheetPage'
import ProfitLossPage from './pages/reports/ProfitLossPage'
import GeneralLedgerPage from './pages/reports/GeneralLedgerPage'
import CashFlowPage from './pages/reports/CashFlowPage'
import JournalListPage from './pages/journals/JournalListPage'
import JournalFormPage from './pages/journals/JournalFormPage'
import EmployeeListPage from './pages/employees/EmployeeListPage'
import PayrollListPage from './pages/payroll/PayrollListPage'
import LaborReclassPage from './pages/payroll/LaborReclassPage'
import CashBankPage from './pages/cashbank/CashBankPage'
import BankAccountPage from './pages/bank/BankAccountPage'
import BankReconPage from './pages/bank/BankReconPage'
import BankReconDetailPage from './pages/bank/BankReconDetailPage'
import AssetListPage from './pages/assets/AssetListPage'
import PPh21Page from './pages/tax/PPh21Page'
import PPNPage from './pages/tax/PPNPage'
import WithholdingPage from './pages/tax/WithholdingPage'
import ExpenseClaimsPage from './pages/expense/ExpenseClaimsPage'
import BudgetPage from './pages/budget/BudgetPage'
import PRPage from './pages/procurement/PRPage'
import POPage from './pages/procurement/POPage'
import ItemMasterPage from './pages/procurement/ItemMasterPage'
import ProjectsPage from './pages/project/ProjectsPage'
import ProjectDetailPage from './pages/project/ProjectDetailPage'
import InventoryPage from './pages/inventory/InventoryPage'
import SalesOrderPage from './pages/sales/SalesOrderPage'
import SalesOrderDetailPage from './pages/sales/SalesOrderDetailPage'
import ARPaymentsPage from './pages/ar/ARPaymentsPage'
import APPaymentsPage from './pages/ap/APPaymentsPage'
import AttendancePage from './pages/attendance/AttendancePage'
import AssetDepreciationPage from './pages/assets/AssetDepreciationPage'
import ExchangeRatesPage from './pages/multicurrency/ExchangeRatesPage'
import RevaluationPage from './pages/multicurrency/RevaluationPage'
import UsersPage from './pages/settings/UsersPage'
import EntitiesPage from './pages/settings/EntitiesPage'
import OpeningBalancePage from './pages/settings/OpeningBalancePage'
import YearEndClosingPage from './pages/accounting/YearEndClosingPage'
import IntercompanyPage from './pages/accounting/IntercompanyPage'
import PayrollDisbursementPage from './pages/payroll/PayrollDisbursementPage'
import VendorRegistrationPage from './pages/procurement/VendorRegistrationPage'
import ForecastPage from './pages/reports/ForecastPage'
import InvoiceTemplatePage from './pages/settings/InvoiceTemplatePage'
import OCRInvoicePage from './pages/accounting/OCRInvoicePage'
import ContactPage from './pages/crm/ContactPage'
import EmailMarketingPage from './pages/crm/EmailMarketingPage'
import AuditTrailPage from './pages/audit/AuditTrailPage'
import LeavePage from './pages/hr/LeavePage'
import ContractPage from './pages/contracts/ContractPage'
import DeferredRevenuePage from './pages/finance/DeferredRevenuePage'
import ComingSoonPage from './pages/ComingSoonPage'

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, isLoading } = useAuth()
  if (isLoading) return <div className="flex items-center justify-center h-screen"><Spinner size="lg" /></div>
  if (!isAuthenticated) return <Navigate to="/login" replace />
  return <Layout>{children}</Layout>
}

function P({ children }: { children: React.ReactNode }) {
  return <ProtectedRoute>{children}</ProtectedRoute>
}

function AppRoutes() {
  const { isAuthenticated, isLoading } = useAuth()
  if (isLoading) return <div className="flex items-center justify-center h-screen"><Spinner size="lg" /></div>

  return (
    <Routes>
      <Route path="/login" element={isAuthenticated ? <Navigate to="/dashboard" replace /> : <LoginPage />} />

      {/* Dashboard */}
      <Route path="/dashboard" element={<P><DashboardPage /></P>} />

      {/* Akuntansi */}
      <Route path="/coa"           element={<P><COAPage /></P>} />
      <Route path="/journals"      element={<P><JournalListPage /></P>} />
      <Route path="/journals/new"  element={<P><JournalFormPage /></P>} />
      <Route path="/year-end"        element={<P><YearEndClosingPage /></P>} />
      <Route path="/intercompany"   element={<P><IntercompanyPage /></P>} />

      {/* AR */}
      <Route path="/ar/invoices"  element={<P><ARInvoicePage /></P>} />
      <Route path="/ar/payments"  element={<P><ARPaymentsPage /></P>} />
      <Route path="/sales-orders"     element={<P><SalesOrderPage /></P>} />
      <Route path="/sales-orders/:id" element={<P><SalesOrderDetailPage /></P>} />
      <Route path="/inventory"    element={<P><InventoryPage /></P>} />

      {/* AP */}
      <Route path="/ap/invoices"  element={<P><APInvoicePage /></P>} />
      <Route path="/ap/payments"  element={<P><APPaymentsPage /></P>} />
      <Route path="/pr"           element={<P><PRPage /></P>} />
      <Route path="/po"           element={<P><POPage /></P>} />
      <Route path="/item-master"          element={<P><ItemMasterPage /></P>} />
      <Route path="/vendor-registration"  element={<P><VendorRegistrationPage /></P>} />
      <Route path="/projects"     element={<P><ProjectsPage /></P>} />
      <Route path="/projects/:id" element={<P><ProjectDetailPage /></P>} />

      {/* Keuangan */}
      <Route path="/cash-bank"      element={<P><CashBankPage /></P>} />
      <Route path="/bank"           element={<P><BankAccountPage /></P>} />
      <Route path="/bank-recon"   element={<P><BankReconPage /></P>} />
      <Route path="/bank-recon/:id" element={<P><BankReconDetailPage /></P>} />
      <Route path="/expense-claims" element={<P><ExpenseClaimsPage /></P>} />
      <Route path="/budget"        element={<P><BudgetPage /></P>} />

      {/* Laporan Keuangan */}
      <Route path="/reports/trial-balance"   element={<P><TrialBalancePage /></P>} />
      <Route path="/reports/balance-sheet"   element={<P><BalanceSheetPage /></P>} />
      <Route path="/reports/profit-loss"     element={<P><ProfitLossPage /></P>} />
      <Route path="/reports/general-ledger"  element={<P><GeneralLedgerPage /></P>} />
      <Route path="/reports/cash-flow"        element={<P><CashFlowPage /></P>} />
      <Route path="/reports/forecast"        element={<P><ForecastPage /></P>} />

      {/* SDM & Payroll */}
      <Route path="/employees"    element={<P><EmployeeListPage /></P>} />
      <Route path="/payroll"               element={<P><PayrollListPage /></P>} />
      <Route path="/payroll/labor-reclass" element={<P><LaborReclassPage /></P>} />
      <Route path="/payroll/disbursement"  element={<P><PayrollDisbursementPage /></P>} />
      <Route path="/attendance"   element={<P><AttendancePage /></P>} />

      {/* Perpajakan */}
      <Route path="/tax/pph21"     element={<P><PPh21Page /></P>} />
      <Route path="/tax/withholding" element={<P><WithholdingPage /></P>} />
      <Route path="/tax/ppn"       element={<P><PPNPage /></P>} />

      {/* Aset Tetap */}
      <Route path="/assets"               element={<P><AssetListPage /></P>} />
      <Route path="/assets/depreciation"  element={<P><AssetDepreciationPage /></P>} />

      {/* Multi-Currency */}
      <Route path="/multicurrency/rates"       element={<P><ExchangeRatesPage /></P>} />
      <Route path="/multicurrency/revaluation" element={<P><RevaluationPage /></P>} />

      {/* Pengaturan */}
      <Route path="/settings/users"            element={<P><UsersPage /></P>} />
      <Route path="/settings/entities"         element={<P><EntitiesPage /></P>} />
      <Route path="/settings/opening-balance"   element={<P><OpeningBalancePage /></P>} />
      <Route path="/settings/invoice-template"  element={<P><InvoiceTemplatePage /></P>} />
      <Route path="/ocr"                         element={<P><OCRInvoicePage /></P>} />
      <Route path="/crm/contacts"               element={<P><ContactPage /></P>} />
      <Route path="/crm/email-marketing"        element={<P><EmailMarketingPage /></P>} />
      <Route path="/audit"                      element={<P><AuditTrailPage /></P>} />
      <Route path="/leave"                      element={<P><LeavePage /></P>} />
      <Route path="/contracts"                  element={<P><ContractPage /></P>} />
      <Route path="/deferred-revenue"           element={<P><DeferredRevenuePage /></P>} />

      <Route path="/" element={<Navigate to="/dashboard" replace />} />
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <AppRoutes />
        <ToastContainer />
      </AuthProvider>
    </BrowserRouter>
  )
}
