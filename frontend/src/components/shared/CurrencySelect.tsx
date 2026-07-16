import { useQuery } from '@tanstack/react-query'
import api from '../../lib/api'

interface CurrencySelectProps {
  value: string
  onChange: (code: string) => void
  className?: string
  disabled?: boolean
}

export default function CurrencySelect({ value, onChange, className, disabled }: CurrencySelectProps) {
  const { data } = useQuery({
    queryKey: ['currencies'],
    queryFn: () => api.get('/multicurrency/currencies').then((r) => r.data),
  })
  const currencies: any[] = Array.isArray(data) ? data : []

  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      disabled={disabled}
      className={className ?? 'form-select'}
    >
      {currencies.length === 0 && <option value="IDR">IDR</option>}
      {currencies.map((c) => (
        <option key={c.currency_code} value={c.currency_code}>
          {c.currency_code} — {c.currency_name}
        </option>
      ))}
    </select>
  )
}
