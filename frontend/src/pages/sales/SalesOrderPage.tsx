import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { useQuery, useMutation } from '@tanstack/react-query'
import { Plus, RefreshCw, ArrowRight, Trash2 } from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext'
import api from '../../lib/api'
import { Card, CardHeader } from '../../components/ui/Card'
import Spinner from '../../components/ui/Spinner'
import EmptyState from '../../components/ui/EmptyState'
import Badge from '../../components/ui/Badge'
import { formatRupiah, formatDate, todayISO } from '../../lib/utils'
import { showToast } from '../../components/ui/Toast'

const TABS = ['customers', 'orders'] as const
type Tab = typeof TABS[number]

function CustomersTab() {
  const { t } = useTranslation(['sales', 'common'])
  const { entityId } = useAuth()
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({ customer_code: '', customer_name: '', customer_type: 'company', phone: '', email: '' })

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['sales-customers', entityId],
    queryFn: () => api.get('/sales/customers', { params: { entity_id: entityId } }).then((r) => r.data),
    enabled: !!entityId,
  })
  const customers: any[] = Array.isArray(data) ? data : []

  const createMutation = useMutation({
    mutationFn: () => api.post('/sales/customers', { entity_id: entityId, ...form }),
    onSuccess: () => {
      showToast(t('sales:customers_createSuccess'))
      setShowForm(false)
      setForm({ customer_code: '', customer_name: '', customer_type: 'company', phone: '', email: '' })
      refetch()
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('sales:customers_createFailed'), 'error'),
  })

  const formValid = form.customer_code && form.customer_name

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <button onClick={() => setShowForm((s) => !s)} className="btn-primary">
          <Plus className="h-4 w-4" /> {t('sales:customers_newBtn')}
        </button>
      </div>

      {showForm && (
        <Card>
          <p className="text-sm font-semibold text-gray-800 mb-4">{t('sales:customers_formTitle')}</p>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-4 mb-4">
            <div>
              <label className="form-label">{t('sales:customers_codeLabel')}</label>
              <input value={form.customer_code} onChange={(e) => setForm({ ...form, customer_code: e.target.value })} className="form-input" />
            </div>
            <div className="md:col-span-2">
              <label className="form-label">{t('sales:customers_nameLabel')}</label>
              <input value={form.customer_name} onChange={(e) => setForm({ ...form, customer_name: e.target.value })} className="form-input" />
            </div>
            <div>
              <label className="form-label">{t('sales:customers_typeLabel')}</label>
              <select value={form.customer_type} onChange={(e) => setForm({ ...form, customer_type: e.target.value })} className="form-select">
                {['company', 'individual', 'government'].map((ty) => <option key={ty} value={ty}>{t(`sales:customers_type_${ty}`)}</option>)}
              </select>
            </div>
            <div>
              <label className="form-label">{t('sales:customers_phoneLabel')}</label>
              <input value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} className="form-input" />
            </div>
            <div>
              <label className="form-label">{t('sales:customers_emailLabel')}</label>
              <input value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} className="form-input" />
            </div>
          </div>
          <div className="flex justify-end gap-3">
            <button onClick={() => setShowForm(false)} className="btn-secondary">{t('common:cancel')}</button>
            <button onClick={() => createMutation.mutate()} disabled={createMutation.isPending || !formValid} className="btn-primary">
              {createMutation.isPending ? t('common:saving') : t('common:save')}
            </button>
          </div>
        </Card>
      )}

      <Card noPad>
        <CardHeader title={t('sales:customers_listTitle')}
          actions={<button onClick={() => refetch()} className="btn-secondary"><RefreshCw className="h-4 w-4" /></button>} />
        {isLoading ? (
          <div className="flex justify-center py-16"><Spinner size="lg" /></div>
        ) : customers.length === 0 ? (
          <EmptyState title={t('sales:customers_emptyTitle')} />
        ) : (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>{t('sales:customers_colCode')}</th>
                  <th>{t('sales:customers_colName')}</th>
                  <th>{t('sales:customers_colType')}</th>
                  <th>{t('sales:customers_colPhone')}</th>
                </tr>
              </thead>
              <tbody>
                {customers.map((c: any) => (
                  <tr key={c.id}>
                    <td className="text-sm font-medium">{c.customer_code}</td>
                    <td className="text-sm text-gray-700">{c.customer_name}</td>
                    <td className="text-sm text-gray-500">{t(`sales:customers_type_${c.customer_type}`)}</td>
                    <td className="text-sm text-gray-500">{c.phone ?? '-'}</td>
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

interface SOLineForm {
  product_id: string
  qty_ordered: string
  uom_id: string
  unit_price: string
}

function OrdersTab() {
  const { t } = useTranslation(['sales', 'common'])
  const { entityId } = useAuth()
  const [showForm, setShowForm] = useState(false)
  const [header, setHeader] = useState({ customer_id: '', so_date: todayISO(), warehouse_id: '' })
  const [lines, setLines] = useState<SOLineForm[]>([{ product_id: '', qty_ordered: '', uom_id: '', unit_price: '' }])

  const { data: custData } = useQuery({
    queryKey: ['sales-customers', entityId],
    queryFn: () => api.get('/sales/customers', { params: { entity_id: entityId } }).then((r) => r.data),
    enabled: !!entityId,
  })
  const customers: any[] = Array.isArray(custData) ? custData : []

  const { data: prodData } = useQuery({
    queryKey: ['inv-products-sellable', entityId],
    queryFn: () => api.get('/inventory/products', { params: { entity_id: entityId, is_sellable: true, size: 200 } }).then((r) => r.data),
    enabled: !!entityId,
  })
  const products: any[] = prodData?.items ?? []

  const { data: whData } = useQuery({
    queryKey: ['inv-locations-internal', entityId],
    queryFn: () => api.get('/inventory/locations', { params: { entity_id: entityId, location_type: 'internal' } }).then((r) => r.data),
    enabled: !!entityId,
  })
  const warehouses: any[] = whData ?? []

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['sales-orders', entityId],
    queryFn: () => api.get('/sales/orders', { params: { entity_id: entityId } }).then((r) => r.data),
    enabled: !!entityId,
  })
  const orders: any[] = Array.isArray(data) ? data : []

  const addLine = () => setLines((ls) => [...ls, { product_id: '', qty_ordered: '', uom_id: '', unit_price: '' }])
  const removeLine = (idx: number) => setLines((ls) => ls.filter((_, i) => i !== idx))
  const updateLine = (idx: number, patch: Partial<SOLineForm>) =>
    setLines((ls) => ls.map((l, i) => {
      if (i !== idx) return l
      const next = { ...l, ...patch }
      if (patch.product_id) {
        const prod = products.find((p) => p.id === patch.product_id)
        if (prod) {
          next.uom_id = prod.uom_id
          next.unit_price = String(prod.sales_price ?? '')
        }
      }
      return next
    }))

  const createMutation = useMutation({
    mutationFn: () => api.post('/sales/orders', {
      entity_id: entityId,
      customer_id: header.customer_id,
      so_date: header.so_date,
      warehouse_id: header.warehouse_id || undefined,
      lines: lines.map((l) => ({
        product_id: l.product_id,
        qty_ordered: parseFloat(l.qty_ordered),
        uom_id: l.uom_id,
        unit_price: parseFloat(l.unit_price),
      })),
    }),
    onSuccess: (res) => {
      showToast(t('sales:orders_createSuccess', { no: res.data.so_no }))
      setShowForm(false)
      setHeader({ customer_id: '', so_date: todayISO(), warehouse_id: '' })
      setLines([{ product_id: '', qty_ordered: '', uom_id: '', unit_price: '' }])
      refetch()
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('sales:orders_createFailed'), 'error'),
  })

  const linesValid = lines.length > 0 && lines.every((l) => l.product_id && parseFloat(l.qty_ordered) > 0 && l.unit_price !== '')
  const formValid = header.customer_id && header.so_date && linesValid

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <button onClick={() => setShowForm((s) => !s)} className="btn-primary">
          <Plus className="h-4 w-4" /> {t('sales:orders_newBtn')}
        </button>
      </div>

      {showForm && (
        <Card>
          <p className="text-sm font-semibold text-gray-800 mb-4">{t('sales:orders_formTitle')}</p>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-4 mb-4">
            <div>
              <label className="form-label">{t('sales:orders_customerLabel')}</label>
              <select value={header.customer_id} onChange={(e) => setHeader({ ...header, customer_id: e.target.value })} className="form-select">
                <option value="">—</option>
                {customers.map((c: any) => <option key={c.id} value={c.id}>{c.customer_name}</option>)}
              </select>
            </div>
            <div>
              <label className="form-label">{t('sales:orders_dateLabel')}</label>
              <input type="date" value={header.so_date} onChange={(e) => setHeader({ ...header, so_date: e.target.value })} className="form-input" />
            </div>
            <div>
              <label className="form-label">{t('sales:orders_warehouseLabel')}</label>
              <select value={header.warehouse_id} onChange={(e) => setHeader({ ...header, warehouse_id: e.target.value })} className="form-select">
                <option value="">—</option>
                {warehouses.map((w: any) => <option key={w.id} value={w.id}>{w.location_name}</option>)}
              </select>
            </div>
          </div>

          <p className="text-sm font-medium text-gray-700 mb-2">{t('sales:orders_linesTitle')}</p>
          <div className="space-y-2 mb-4">
            {lines.map((l, idx) => (
              <div key={idx} className="grid grid-cols-12 gap-2 items-end">
                <div className="col-span-5">
                  <label className="form-label">{t('sales:orders_lineProductLabel')}</label>
                  <select value={l.product_id} onChange={(e) => updateLine(idx, { product_id: e.target.value })} className="form-select">
                    <option value="">—</option>
                    {products.map((p: any) => (
                      <option key={p.id} value={p.id}>
                        {p.product_code ?? p.sku} — {p.product_name} {p.is_stock_item ? '' : t('sales:orders_serviceTag')}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="col-span-2">
                  <label className="form-label">{t('sales:orders_lineQtyLabel')}</label>
                  <input type="number" value={l.qty_ordered} onChange={(e) => updateLine(idx, { qty_ordered: e.target.value })} className="form-input" />
                </div>
                <div className="col-span-3">
                  <label className="form-label">{t('sales:orders_linePriceLabel')}</label>
                  <input type="number" value={l.unit_price} onChange={(e) => updateLine(idx, { unit_price: e.target.value })} className="form-input" />
                </div>
                <div className="col-span-2 flex justify-end">
                  <button onClick={() => removeLine(idx)} disabled={lines.length === 1} className="btn-secondary px-2">
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              </div>
            ))}
          </div>
          <button onClick={addLine} className="btn-secondary mb-4">
            <Plus className="h-4 w-4" /> {t('sales:orders_addLineBtn')}
          </button>

          <div className="flex justify-end gap-3">
            <button onClick={() => setShowForm(false)} className="btn-secondary">{t('common:cancel')}</button>
            <button onClick={() => createMutation.mutate()} disabled={createMutation.isPending || !formValid} className="btn-primary">
              {createMutation.isPending ? t('common:saving') : t('common:save')}
            </button>
          </div>
        </Card>
      )}

      <Card noPad>
        <CardHeader title={t('sales:orders_listTitle')}
          actions={<button onClick={() => refetch()} className="btn-secondary"><RefreshCw className="h-4 w-4" /></button>} />
        {isLoading ? (
          <div className="flex justify-center py-16"><Spinner size="lg" /></div>
        ) : orders.length === 0 ? (
          <EmptyState title={t('sales:orders_emptyTitle')} />
        ) : (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>{t('sales:orders_colNo')}</th>
                  <th>{t('sales:orders_colCustomer')}</th>
                  <th>{t('sales:orders_colDate')}</th>
                  <th>{t('common:status')}</th>
                  <th className="right">{t('sales:orders_colTotal')}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {orders.map((o: any) => (
                  <tr key={o.so_id}>
                    <td className="text-sm font-medium">
                      <Link to={`/sales-orders/${o.so_id}`} className="text-primary-700 hover:underline inline-flex items-center gap-1">
                        {o.so_no} <ArrowRight className="h-3 w-3" />
                      </Link>
                    </td>
                    <td className="text-sm text-gray-700">{o.customer_name}</td>
                    <td className="text-xs text-gray-400">{formatDate(o.so_date)}</td>
                    <td><Badge status={o.status} /></td>
                    <td className="right text-sm">Rp {formatRupiah(o.total_amount)}</td>
                    <td />
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

export default function SalesOrderPage() {
  const { t } = useTranslation(['sales', 'common'])
  const [tab, setTab] = useState<Tab>('orders')

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">{t('sales:pageTitle')}</h1>
        <p className="text-sm text-gray-500 mt-0.5">{t('sales:pageSubtitle')}</p>
      </div>

      <div className="flex gap-1 border-b border-gray-200">
        {TABS.map((tb) => (
          <button key={tb} onClick={() => setTab(tb)}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
              tab === tb ? 'border-primary-600 text-primary-700' : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}>
            {t(`sales:tab_${tb}`)}
          </button>
        ))}
      </div>

      {tab === 'customers' && <CustomersTab />}
      {tab === 'orders' && <OrdersTab />}
    </div>
  )
}
