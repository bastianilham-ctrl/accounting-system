import { useQuery } from '@tanstack/react-query'
import api from './api'

/** Kurs terbaru (rate_type middle) untuk satu mata uang ke IDR. null kalau IDR atau belum ada kurs. */
export function useLatestRate(currencyCode: string) {
  const { data, isLoading } = useQuery({
    queryKey: ['exchange-rate-latest', currencyCode],
    queryFn: () =>
      api.get('/multicurrency/exchange-rates/latest', { params: { currencies: currencyCode } }).then((r) => r.data),
    enabled: !!currencyCode && currencyCode !== 'IDR',
  })
  const rates: any[] = Array.isArray(data) ? data : []
  const rate = rates.find((r) => r.from_currency === currencyCode)?.rate
  return { rate: rate != null ? Number(rate) : null, isLoading }
}
