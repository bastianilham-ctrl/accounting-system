# Accounting System — Frontend

React + TypeScript + Vite + Tailwind CSS

## Prasyarat

**Node.js v20 LTS** harus terinstall. Unduh dari:
https://nodejs.org/en/download (pilih "LTS" → "Windows Installer")

Verifikasi instalasi:
```bash
node --version   # harus v20.x.x
npm --version    # harus v10.x.x
```

## Instalasi

```bash
cd frontend
npm install
```

## Menjalankan

```bash
# Pastikan backend FastAPI sudah berjalan di http://localhost:8000
npm run dev
# Buka http://localhost:3000
```

## Build Production

```bash
npm run build
# Output di dist/
```

## Struktur

```
src/
  contexts/AuthContext.tsx    # JWT auth state
  lib/api.ts                  # Axios + proxy /api → localhost:8000
  lib/utils.ts                # formatRupiah, formatDate, helpers
  components/
    layout/                   # Sidebar, Header, Layout
    ui/                       # Badge, Card, Spinner, EmptyState
  pages/
    LoginPage.tsx
    DashboardPage.tsx
    coa/COAPage.tsx
    ar/ARInvoicePage.tsx
    ap/APInvoicePage.tsx
    reports/
      TrialBalancePage.tsx
      BalanceSheetPage.tsx
      ProfitLossPage.tsx
      GeneralLedgerPage.tsx
      CashFlowPage.tsx
```

## Konfigurasi

Vite sudah dikonfigurasi dengan proxy:
- `/api/*` → `http://localhost:8000/*`

Tidak perlu ubah apapun selama backend berjalan di port 8000.

## Halaman yang Tersedia

| URL | Halaman |
|-----|---------|
| `/login` | Login |
| `/dashboard` | Dashboard KPI + Chart |
| `/coa` | Chart of Accounts |
| `/ar/invoices` | Invoice Pelanggan |
| `/ap/invoices` | Invoice Vendor |
| `/reports/trial-balance` | Neraca Saldo |
| `/reports/balance-sheet` | Neraca |
| `/reports/profit-loss` | Laba Rugi |
| `/reports/general-ledger` | Buku Besar |
| `/reports/cash-flow` | Arus Kas |
