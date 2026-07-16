import { useParams, Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useQuery, useMutation } from '@tanstack/react-query'
import { ArrowLeft, CheckCircle, Truck, FileText } from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext'
import api from '../../lib/api'
import { Card, CardHeader } from '../../components/ui/Card'
import Spinner from '../../components/ui/Spinner'
import EmptyState from '../../components/ui/EmptyState'
import Badge from '../../components/ui/Badge'
import { formatRupiah, formatDate, todayISO } from '../../lib/utils'
import { showToast } from '../../components/ui/Toast'

export default function SalesOrderDetailPage() {
  const { id } = useParams<{ id: string }>()
  const { t } = useTranslation(['sales', 'common'])
  const { user } = useAuth()

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['sales-order-detail', id],
    queryFn: () => api.get(`/sales/orders/${id}`).then((r) => r.data),
    enabled: !!id,
  })

  const confirmMutation = useMutation({
    mutationFn: () => api.post(`/sales/orders/${id}/confirm`, { confirmed_by: user?.email ?? user?.username ?? 'system' }),
    onSuccess: () => { showToast(t('sales:detail_confirmSuccess')); refetch() },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('sales:detail_confirmFailed'), 'error'),
  })

  const validatePickMutation = useMutation({
    mutationFn: (pickingId: string) => api.post(`/sales/pickings/${pickingId}/validate`, { picked_by: user?.email ?? user?.username ?? 'system' }),
    onSuccess: () => { showToast(t('common:success')); refetch() },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('common:failed'), 'error'),
  })

  const validateDoMutation = useMutation({
    mutationFn: (doId: string) => api.post(`/inventory/delivery-orders/${doId}/validate`, {
      validated_by: user?.email ?? user?.username ?? 'system',
      validated_by_role: user?.role ?? 'admin',
    }),
    onSuccess: () => { showToast(t('common:success')); refetch() },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('common:failed'), 'error'),
  })

  const invoiceMutation = useMutation({
    mutationFn: () => api.post(`/sales/orders/${id}/invoice`, {
      invoice_date: todayISO(),
      created_by: user?.email ?? user?.username ?? 'system',
      invoice_type: 'delivered',
    }),
    onSuccess: (res) => { showToast(t('sales:detail_invoiceSuccess', { no: res.data.invoice_no })); refetch() },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('sales:detail_invoiceFailed'), 'error'),
  })

  if (isLoading || !data || !id) {
    return <div className="flex justify-center py-20"><Spinner size="lg" /></div>
  }

  const so = data.so
  const lines: any[] = data.lines ?? []
  const pickings: any[] = data.pickings ?? []
  const deliveryOrders: any[] = data.delivery_orders ?? []

  const canConfirm = so.status === 'draft'
  const canInvoice = so.status === 'ready' || so.status === 'delivered'

  return (
    <div className="space-y-6">
      <Link to="/sales-orders" className="inline-flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-700">
        <ArrowLeft className="h-4 w-4" /> {t('sales:detail_backToList')}
      </Link>

      <div className="flex items-center justify-between flex-wrap gap-2">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">{so.so_no}</h1>
          <p className="text-sm text-gray-500 mt-0.5">{so.customer_name} · {formatDate(so.so_date)}</p>
        </div>
        <div className="flex items-center gap-2">
          <Badge status={so.status} />
          {canConfirm && (
            <button onClick={() => confirmMutation.mutate()} disabled={confirmMutation.isPending} className="btn-primary">
              <CheckCircle className="h-4 w-4" /> {t('sales:detail_confirmBtn')}
            </button>
          )}
          {canInvoice && (
            <button onClick={() => invoiceMutation.mutate()} disabled={invoiceMutation.isPending} className="btn-primary">
              <FileText className="h-4 w-4" /> {t('sales:detail_invoiceBtn')}
            </button>
          )}
        </div>
      </div>

      <Card noPad>
        <CardHeader title={t('sales:detail_linesTitle')} />
        <div className="overflow-x-auto">
          <table className="data-table">
            <thead>
              <tr>
                <th>{t('sales:detail_colProduct')}</th>
                <th className="right">{t('sales:detail_colQtyOrdered')}</th>
                <th className="right">{t('sales:detail_colQtyDelivered')}</th>
                <th className="right">{t('sales:detail_colQtyInvoiced')}</th>
                <th className="right">{t('sales:detail_colUnitPrice')}</th>
                <th className="right">{t('sales:detail_colSubtotal')}</th>
              </tr>
            </thead>
            <tbody>
              {lines.map((l: any) => (
                <tr key={l.id}>
                  <td className="text-sm font-medium">{l.product_name}</td>
                  <td className="right text-sm">{l.qty_ordered}</td>
                  <td className="right text-sm">{l.qty_delivered}</td>
                  <td className="right text-sm">{l.qty_invoiced}</td>
                  <td className="right text-sm">Rp {formatRupiah(l.unit_price)}</td>
                  <td className="right text-sm">Rp {formatRupiah(l.subtotal)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <Card noPad>
        <CardHeader title={t('sales:detail_pickingsTitle')} />
        {pickings.length === 0 ? (
          <EmptyState title={t('sales:detail_noPickings')} />
        ) : (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>{t('sales:detail_colPickingNo')}</th>
                  <th>{t('common:status')}</th>
                  <th>{t('sales:detail_colDate')}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {pickings.map((p: any) => (
                  <tr key={p.id}>
                    <td className="text-sm font-medium">{p.picking_no}</td>
                    <td><Badge status={p.status} /></td>
                    <td className="text-xs text-gray-400">{formatDate(p.picking_date)}</td>
                    <td>
                      {p.status !== 'done' && (
                        <button onClick={() => validatePickMutation.mutate(p.id)} disabled={validatePickMutation.isPending}
                          className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-md bg-blue-50 text-blue-700 hover:bg-blue-100">
                          <Truck className="h-3 w-3" /> {t('sales:detail_validatePickBtn')}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card noPad>
        <CardHeader title={t('sales:detail_doTitle')} />
        {deliveryOrders.length === 0 ? (
          <EmptyState title={t('sales:detail_noDOs')} />
        ) : (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>{t('sales:detail_colDoNo')}</th>
                  <th>{t('common:status')}</th>
                  <th>{t('sales:detail_colDate')}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {deliveryOrders.map((d: any) => (
                  <tr key={d.id}>
                    <td className="text-sm font-medium">{d.do_no}</td>
                    <td><Badge status={d.status} /></td>
                    <td className="text-xs text-gray-400">{formatDate(d.do_date)}</td>
                    <td>
                      {(d.status === 'draft' || d.status === 'ready') && (
                        <button onClick={() => validateDoMutation.mutate(d.id)} disabled={validateDoMutation.isPending}
                          className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-md bg-green-50 text-green-700 hover:bg-green-100">
                          <CheckCircle className="h-3 w-3" /> {t('sales:detail_validateDoBtn')}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}
