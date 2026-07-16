import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function formatRupiah(value: number | null | undefined, decimals = 0): string {
  if (value == null) return '-'
  const absVal = Math.abs(value)
  const formatted = new Intl.NumberFormat('id-ID', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(absVal)
  return value < 0 ? `(${formatted})` : formatted
}

export function formatCurrency(value: number | null | undefined, currencyCode = 'IDR', decimals?: number): string {
  if (currencyCode === 'IDR') return formatRupiah(value, decimals)
  if (value == null) return '-'
  const absVal = Math.abs(value)
  const formatted = new Intl.NumberFormat('en-US', {
    minimumFractionDigits: decimals ?? 2,
    maximumFractionDigits: decimals ?? 2,
  }).format(absVal)
  const signed = value < 0 ? `(${formatted})` : formatted
  return `${currencyCode} ${signed}`
}

export function formatPercent(value: number | null | undefined): string {
  if (value == null) return '-'
  const sign = value >= 0 ? '+' : ''
  return `${sign}${value.toFixed(1)}%`
}

export function formatDate(dateStr: string | null | undefined): string {
  if (!dateStr) return '-'
  try {
    return new Date(dateStr).toLocaleDateString('id-ID', {
      day: '2-digit', month: 'short', year: 'numeric',
    })
  } catch {
    return dateStr
  }
}

export function formatDateInput(dateStr: string | null | undefined): string {
  if (!dateStr) return ''
  return dateStr.split('T')[0]
}

export function todayISO(): string {
  return new Date().toISOString().split('T')[0]
}

export function firstDayOfMonth(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-01`
}

export function lastDayOfMonth(): string {
  const d = new Date()
  const last = new Date(d.getFullYear(), d.getMonth() + 1, 0)
  return last.toISOString().split('T')[0]
}

export function currentYear(): number {
  return new Date().getFullYear()
}

export function currentMonth(): number {
  return new Date().getMonth() + 1
}

export const MONTHS = [
  { value: 1, label: 'Januari' }, { value: 2, label: 'Februari' },
  { value: 3, label: 'Maret' },   { value: 4, label: 'April' },
  { value: 5, label: 'Mei' },     { value: 6, label: 'Juni' },
  { value: 7, label: 'Juli' },    { value: 8, label: 'Agustus' },
  { value: 9, label: 'September' },{ value: 10, label: 'Oktober' },
  { value: 11, label: 'November' },{ value: 12, label: 'Desember' },
]

export function getStatusColor(status: string): string {
  const map: Record<string, string> = {
    posted: 'bg-green-100 text-green-700',
    paid: 'bg-green-100 text-green-700',
    draft: 'bg-gray-100 text-gray-600',
    pending: 'bg-yellow-100 text-yellow-700',
    overdue: 'bg-red-100 text-red-700',
    cancelled: 'bg-red-100 text-red-500',
    partial: 'bg-blue-100 text-blue-700',
    approved: 'bg-emerald-100 text-emerald-700',
    submitted: 'bg-purple-100 text-purple-700',
    open: 'bg-amber-100 text-amber-700',
    converted: 'bg-indigo-100 text-indigo-700',
    rejected: 'bg-red-100 text-red-700',
    pending_approval: 'bg-yellow-100 text-yellow-700',
    on_hold: 'bg-orange-100 text-orange-700',
    closed: 'bg-gray-100 text-gray-500',
    not_started: 'bg-gray-100 text-gray-500',
    in_progress: 'bg-blue-100 text-blue-700',
    completed: 'bg-green-100 text-green-700',
    achieved: 'bg-green-100 text-green-700',
    missed: 'bg-red-100 text-red-700',
    at_risk: 'bg-orange-100 text-orange-700',
    low: 'bg-gray-100 text-gray-600',
    medium: 'bg-yellow-100 text-yellow-700',
    high: 'bg-orange-100 text-orange-700',
    critical: 'bg-red-100 text-red-700',
  }
  return map[status?.toLowerCase()] ?? 'bg-gray-100 text-gray-600'
}
