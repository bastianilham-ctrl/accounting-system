import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery, useMutation } from '@tanstack/react-query'
import { Plus, RefreshCw } from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext'
import api from '../../lib/api'
import { Card, CardHeader } from '../../components/ui/Card'
import Spinner from '../../components/ui/Spinner'
import EmptyState from '../../components/ui/EmptyState'
import Badge from '../../components/ui/Badge'
import { formatRupiah } from '../../lib/utils'
import { showToast } from '../../components/ui/Toast'

const TABS = ['categories', 'products', 'receipts', 'stock'] as const
type Tab = typeof TABS[number]

const PRODUCT_TYPES = ['storable', 'consumable', 'service'] as const

function CategoriesTab() {
  const { t } = useTranslation(['inventory', 'common'])
  const { entityId } = useAuth()
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({
    category_code: '', category_name: '', cost_method: 'average_cost',
    inventory_account_code: '', cogs_account_code: '', grir_account_code: '',
  })

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['inv-categories', entityId],
    queryFn: () => api.get('/inventory/categories', { params: { entity_id: entityId } }).then((r) => r.data),
    enabled: !!entityId,
  })
  const categories: any[] = data ?? []

  const createMutation = useMutation({
    mutationFn: () => api.post('/inventory/categories', { entity_id: entityId, ...form }),
    onSuccess: () => {
      showToast(t('inventory:categories_createSuccess'))
      setShowForm(false)
      setForm({ category_code: '', category_name: '', cost_method: 'average_cost', inventory_account_code: '', cogs_account_code: '', grir_account_code: '' })
      refetch()
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('inventory:categories_createFailed'), 'error'),
  })

  const formValid = form.category_code && form.category_name

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <button onClick={() => setShowForm((s) => !s)} className="btn-primary">
          <Plus className="h-4 w-4" /> {t('inventory:categories_newBtn')}
        </button>
      </div>

      {showForm && (
        <Card>
          <p className="text-sm font-semibold text-gray-800 mb-4">{t('inventory:categories_formTitle')}</p>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-4 mb-4">
            <div>
              <label className="form-label">{t('inventory:categories_codeLabel')}</label>
              <input value={form.category_code} onChange={(e) => setForm({ ...form, category_code: e.target.value })} className="form-input" />
            </div>
            <div className="md:col-span-2">
              <label className="form-label">{t('inventory:categories_nameLabel')}</label>
              <input value={form.category_name} onChange={(e) => setForm({ ...form, category_name: e.target.value })} className="form-input" />
            </div>
            <div>
              <label className="form-label">{t('inventory:categories_costMethodLabel')}</label>
              <select value={form.cost_method} onChange={(e) => setForm({ ...form, cost_method: e.target.value })} className="form-select">
                {['average_cost', 'fifo', 'standard_cost'].map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            </div>
            <div>
              <label className="form-label">{t('inventory:categories_invAccLabel')}</label>
              <input value={form.inventory_account_code} onChange={(e) => setForm({ ...form, inventory_account_code: e.target.value })} className="form-input" placeholder="1-3-001" />
            </div>
            <div>
              <label className="form-label">{t('inventory:categories_cogsAccLabel')}</label>
              <input value={form.cogs_account_code} onChange={(e) => setForm({ ...form, cogs_account_code: e.target.value })} className="form-input" placeholder="5-1-001" />
            </div>
            <div>
              <label className="form-label">{t('inventory:categories_grirAccLabel')}</label>
              <input value={form.grir_account_code} onChange={(e) => setForm({ ...form, grir_account_code: e.target.value })} className="form-input" placeholder="2-1-001" />
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
        <CardHeader title={t('inventory:categories_listTitle')}
          actions={<button onClick={() => refetch()} className="btn-secondary"><RefreshCw className="h-4 w-4" /></button>} />
        {isLoading ? (
          <div className="flex justify-center py-16"><Spinner size="lg" /></div>
        ) : categories.length === 0 ? (
          <EmptyState title={t('inventory:categories_emptyTitle')} />
        ) : (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>{t('inventory:categories_colCode')}</th>
                  <th>{t('inventory:categories_colName')}</th>
                  <th>{t('inventory:categories_colCostMethod')}</th>
                </tr>
              </thead>
              <tbody>
                {categories.map((c: any) => (
                  <tr key={c.id}>
                    <td className="text-sm font-medium">{c.category_code}</td>
                    <td className="text-sm text-gray-700">{c.category_name}</td>
                    <td className="text-sm text-gray-500">{c.cost_method}</td>
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

function ProductsTab() {
  const { t } = useTranslation(['inventory', 'common'])
  const { entityId } = useAuth()
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({
    sku: '', product_name: '', category_id: '', uom_id: '',
    product_type: 'storable', sales_price: '', standard_price: '', is_sellable: true,
  })

  const { data: catData } = useQuery({
    queryKey: ['inv-categories', entityId],
    queryFn: () => api.get('/inventory/categories', { params: { entity_id: entityId } }).then((r) => r.data),
    enabled: !!entityId,
  })
  const categories: any[] = catData ?? []

  const { data: uomData } = useQuery({
    queryKey: ['inv-uom'],
    queryFn: () => api.get('/inventory/uom').then((r) => r.data),
  })
  const uoms: any[] = uomData ?? []

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['inv-products', entityId],
    queryFn: () => api.get('/inventory/products', { params: { entity_id: entityId, size: 200 } }).then((r) => r.data),
    enabled: !!entityId,
  })
  const products: any[] = data?.items ?? []

  const createMutation = useMutation({
    mutationFn: () => api.post('/inventory/products', {
      entity_id: entityId,
      sku: form.sku,
      product_name: form.product_name,
      category_id: form.category_id,
      uom_id: form.uom_id,
      product_type: form.product_type,
      sales_price: parseFloat(form.sales_price) || 0,
      standard_price: parseFloat(form.standard_price) || 0,
      is_sellable: form.is_sellable,
    }),
    onSuccess: () => {
      showToast(t('inventory:products_createSuccess'))
      setShowForm(false)
      setForm({ sku: '', product_name: '', category_id: '', uom_id: '', product_type: 'storable', sales_price: '', standard_price: '', is_sellable: true })
      refetch()
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('inventory:products_createFailed'), 'error'),
  })

  const formValid = form.sku && form.product_name && form.category_id && form.uom_id

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <button onClick={() => setShowForm((s) => !s)} className="btn-primary">
          <Plus className="h-4 w-4" /> {t('inventory:products_newBtn')}
        </button>
      </div>

      {showForm && (
        <Card>
          <p className="text-sm font-semibold text-gray-800 mb-4">{t('inventory:products_formTitle')}</p>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-4 mb-4">
            <div>
              <label className="form-label">{t('inventory:products_skuLabel')}</label>
              <input value={form.sku} onChange={(e) => setForm({ ...form, sku: e.target.value })} className="form-input" />
            </div>
            <div className="md:col-span-2">
              <label className="form-label">{t('inventory:products_nameLabel')}</label>
              <input value={form.product_name} onChange={(e) => setForm({ ...form, product_name: e.target.value })} className="form-input" />
            </div>
            <div>
              <label className="form-label">{t('inventory:products_categoryLabel')}</label>
              <select value={form.category_id} onChange={(e) => setForm({ ...form, category_id: e.target.value })} className="form-select">
                <option value="">—</option>
                {categories.map((c: any) => <option key={c.id} value={c.id}>{c.category_name}</option>)}
              </select>
            </div>
            <div>
              <label className="form-label">{t('inventory:products_uomLabel')}</label>
              <select value={form.uom_id} onChange={(e) => setForm({ ...form, uom_id: e.target.value })} className="form-select">
                <option value="">—</option>
                {uoms.map((u: any) => <option key={u.id} value={u.id}>{u.uom_name}</option>)}
              </select>
            </div>
            <div>
              <label className="form-label">{t('inventory:products_typeLabel')}</label>
              <select value={form.product_type} onChange={(e) => setForm({ ...form, product_type: e.target.value })} className="form-select">
                {PRODUCT_TYPES.map((pt) => <option key={pt} value={pt}>{t(`inventory:products_type_${pt}`)}</option>)}
              </select>
              <p className="text-xs text-gray-400 mt-1">{t('inventory:products_typeHint')}</p>
            </div>
            <div>
              <label className="form-label">{t('inventory:products_priceLabel')}</label>
              <input type="number" value={form.sales_price} onChange={(e) => setForm({ ...form, sales_price: e.target.value })} className="form-input" />
            </div>
            <div>
              <label className="form-label">{t('inventory:products_costLabel')}</label>
              <input type="number" value={form.standard_price} onChange={(e) => setForm({ ...form, standard_price: e.target.value })} className="form-input" />
            </div>
            <div className="flex items-center gap-2 pt-6">
              <input type="checkbox" checked={form.is_sellable} onChange={(e) => setForm({ ...form, is_sellable: e.target.checked })} id="is_sellable" />
              <label htmlFor="is_sellable" className="text-sm text-gray-700">{t('inventory:products_sellableLabel')}</label>
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
        <CardHeader title={t('inventory:products_listTitle')} subtitle={t('inventory:products_listSubtitle', { count: products.length })}
          actions={<button onClick={() => refetch()} className="btn-secondary"><RefreshCw className="h-4 w-4" /></button>} />
        {isLoading ? (
          <div className="flex justify-center py-16"><Spinner size="lg" /></div>
        ) : products.length === 0 ? (
          <EmptyState title={t('inventory:products_emptyTitle')} />
        ) : (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>{t('inventory:products_colCode')}</th>
                  <th>{t('inventory:products_colName')}</th>
                  <th>{t('inventory:products_colCategory')}</th>
                  <th>{t('inventory:products_colType')}</th>
                  <th>{t('inventory:products_colStock')}</th>
                  <th>{t('inventory:products_colSellable')}</th>
                  <th className="right">{t('inventory:products_colPrice')}</th>
                </tr>
              </thead>
              <tbody>
                {products.map((p: any) => (
                  <tr key={p.id}>
                    <td className="text-sm font-medium">{p.product_code ?? p.sku}</td>
                    <td className="text-sm text-gray-700">{p.product_name}</td>
                    <td className="text-sm text-gray-500">{p.category_name}</td>
                    <td className="text-sm text-gray-500">{t(`inventory:products_type_${p.product_type}`)}</td>
                    <td><Badge status={p.is_stock_item ? 'yes' : 'no'} label={p.is_stock_item ? t('common:yes') : t('common:no')} /></td>
                    <td><Badge status={p.is_sellable ? 'yes' : 'no'} label={p.is_sellable ? t('common:yes') : t('common:no')} /></td>
                    <td className="right text-sm">Rp {formatRupiah(p.sales_price)}</td>
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

function ReceiptsTab() {
  const { t } = useTranslation(['inventory', 'common'])
  const { entityId } = useAuth()
  const [showLocForm, setShowLocForm] = useState(false)
  const [locForm, setLocForm] = useState({ location_code: '', location_name: '' })
  const [form, setForm] = useState({ product_id: '', destination_location_id: '', qty: '', unit_cost: '', reference_no: '' })

  const { data: prodData } = useQuery({
    queryKey: ['inv-products', entityId],
    queryFn: () => api.get('/inventory/products', { params: { entity_id: entityId, product_type: 'storable', size: 200 } }).then((r) => r.data),
    enabled: !!entityId,
  })
  const products: any[] = prodData?.items ?? []

  const { data: locData, refetch: refetchLocs } = useQuery({
    queryKey: ['inv-locations', entityId],
    queryFn: () => api.get('/inventory/locations', { params: { entity_id: entityId, location_type: 'internal' } }).then((r) => r.data),
    enabled: !!entityId,
  })
  const locations: any[] = locData ?? []

  const setupMutation = useMutation({
    mutationFn: () => api.post('/inventory/setup/locations', { entity_id: entityId }),
    onSuccess: () => showToast(t('inventory:receipts_setupSuccess')),
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('inventory:receipts_setupFailed'), 'error'),
  })

  const createLocMutation = useMutation({
    mutationFn: () => api.post('/inventory/locations', {
      entity_id: entityId, location_code: locForm.location_code, location_name: locForm.location_name, location_type: 'internal',
    }),
    onSuccess: () => {
      showToast(t('common:success'))
      setShowLocForm(false)
      setLocForm({ location_code: '', location_name: '' })
      refetchLocs()
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('common:failed'), 'error'),
  })

  const { data: movesData, refetch: refetchMoves } = useQuery({
    queryKey: ['inv-moves', entityId],
    queryFn: () => api.get('/inventory/stock/moves', { params: { entity_id: entityId, move_type: 'receipt', size: 20 } }).then((r) => r.data),
    enabled: !!entityId,
  })
  const moves: any[] = Array.isArray(movesData) ? movesData : movesData?.items ?? []

  const submitMutation = useMutation({
    mutationFn: () => api.post('/inventory/goods-receipts', {
      entity_id: entityId,
      product_id: form.product_id,
      destination_location_id: form.destination_location_id,
      qty: parseFloat(form.qty),
      unit_cost: parseFloat(form.unit_cost),
      reference_no: form.reference_no || undefined,
    }),
    onSuccess: () => {
      showToast(t('inventory:receipts_success'))
      setForm({ product_id: '', destination_location_id: '', qty: '', unit_cost: '', reference_no: '' })
      refetchMoves()
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('inventory:receipts_failed'), 'error'),
  })

  const formValid = form.product_id && form.destination_location_id && parseFloat(form.qty) > 0 && parseFloat(form.unit_cost) >= 0

  return (
    <div className="space-y-4">
      <div className="flex justify-end gap-2">
        <button onClick={() => setupMutation.mutate()} disabled={setupMutation.isPending} className="btn-secondary">
          {t('inventory:receipts_setupBtn')}
        </button>
        <button onClick={() => setShowLocForm((s) => !s)} className="btn-secondary">
          <Plus className="h-4 w-4" /> {t('inventory:receipts_newLocationBtn')}
        </button>
      </div>

      {showLocForm && (
        <Card>
          <p className="text-sm font-semibold text-gray-800 mb-4">{t('inventory:receipts_locFormTitle')}</p>
          <div className="grid grid-cols-2 gap-4 mb-4">
            <div>
              <label className="form-label">{t('inventory:receipts_locCodeLabel')}</label>
              <input value={locForm.location_code} onChange={(e) => setLocForm({ ...locForm, location_code: e.target.value })} className="form-input" placeholder="WH-MAIN" />
            </div>
            <div>
              <label className="form-label">{t('inventory:receipts_locNameLabel')}</label>
              <input value={locForm.location_name} onChange={(e) => setLocForm({ ...locForm, location_name: e.target.value })} className="form-input" />
            </div>
          </div>
          <div className="flex justify-end gap-3">
            <button onClick={() => setShowLocForm(false)} className="btn-secondary">{t('common:cancel')}</button>
            <button onClick={() => createLocMutation.mutate()} disabled={createLocMutation.isPending || !locForm.location_code || !locForm.location_name} className="btn-primary">
              {t('common:save')}
            </button>
          </div>
        </Card>
      )}

      <Card>
        <p className="text-sm font-semibold text-gray-800 mb-4">{t('inventory:receipts_formTitle')}</p>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-4 mb-4">
          <div>
            <label className="form-label">{t('inventory:receipts_productLabel')}</label>
            <select value={form.product_id} onChange={(e) => setForm({ ...form, product_id: e.target.value })} className="form-select">
              <option value="">—</option>
              {products.map((p: any) => <option key={p.id} value={p.id}>{p.product_code ?? p.sku} — {p.product_name}</option>)}
            </select>
          </div>
          <div>
            <label className="form-label">{t('inventory:receipts_locationLabel')}</label>
            <select value={form.destination_location_id} onChange={(e) => setForm({ ...form, destination_location_id: e.target.value })} className="form-select">
              <option value="">—</option>
              {locations.map((l: any) => <option key={l.id} value={l.id}>{l.location_name}</option>)}
            </select>
          </div>
          <div>
            <label className="form-label">{t('inventory:receipts_refLabel')}</label>
            <input value={form.reference_no} onChange={(e) => setForm({ ...form, reference_no: e.target.value })} className="form-input" />
          </div>
          <div>
            <label className="form-label">{t('inventory:receipts_qtyLabel')}</label>
            <input type="number" value={form.qty} onChange={(e) => setForm({ ...form, qty: e.target.value })} className="form-input" />
          </div>
          <div>
            <label className="form-label">{t('inventory:receipts_unitCostLabel')}</label>
            <input type="number" value={form.unit_cost} onChange={(e) => setForm({ ...form, unit_cost: e.target.value })} className="form-input" />
          </div>
        </div>
        <div className="flex justify-end">
          <button onClick={() => submitMutation.mutate()} disabled={submitMutation.isPending || !formValid} className="btn-primary">
            {submitMutation.isPending ? t('common:saving') : t('inventory:receipts_submitBtn')}
          </button>
        </div>
      </Card>

      <Card noPad>
        <CardHeader title={t('inventory:receipts_recentTitle')}
          actions={<button onClick={() => refetchMoves()} className="btn-secondary"><RefreshCw className="h-4 w-4" /></button>} />
        {moves.length === 0 ? (
          <EmptyState title={t('inventory:receipts_emptyTitle')} />
        ) : (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>{t('inventory:receipts_colRef')}</th>
                  <th>{t('inventory:receipts_colProduct')}</th>
                  <th className="right">{t('inventory:receipts_colQty')}</th>
                  <th className="right">{t('inventory:receipts_colUnitCost')}</th>
                </tr>
              </thead>
              <tbody>
                {moves.map((m: any) => (
                  <tr key={m.id}>
                    <td className="text-sm font-medium">{m.reference_no}</td>
                    <td className="text-sm text-gray-700">{m.product_name ?? m.sku}</td>
                    <td className="right text-sm">{m.qty_done}</td>
                    <td className="right text-sm">Rp {formatRupiah(m.unit_cost)}</td>
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

function StockTab() {
  const { t } = useTranslation(['inventory', 'common'])
  const { entityId } = useAuth()

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['inv-stock-summary', entityId],
    queryFn: () => api.get('/inventory/stock/summary', { params: { entity_id: entityId } }).then((r) => r.data),
    enabled: !!entityId,
  })
  const rows: any[] = Array.isArray(data) ? data : []

  return (
    <Card noPad>
      <CardHeader title={t('inventory:stock_title')}
        actions={<button onClick={() => refetch()} className="btn-secondary"><RefreshCw className="h-4 w-4" /></button>} />
      {isLoading ? (
        <div className="flex justify-center py-16"><Spinner size="lg" /></div>
      ) : rows.length === 0 ? (
        <EmptyState title={t('inventory:stock_emptyTitle')} />
      ) : (
        <div className="overflow-x-auto">
          <table className="data-table">
            <thead>
              <tr>
                <th>{t('inventory:stock_colProduct')}</th>
                <th>{t('inventory:stock_colLocation')}</th>
                <th className="right">{t('inventory:stock_colOnHand')}</th>
                <th className="right">{t('inventory:stock_colReserved')}</th>
                <th className="right">{t('inventory:stock_colAvailable')}</th>
                <th className="right">{t('inventory:stock_colValue')}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r: any, idx: number) => (
                <tr key={idx}>
                  <td className="text-sm font-medium">{r.product_name ?? r.sku}</td>
                  <td className="text-sm text-gray-500">{r.location_name}</td>
                  <td className="right text-sm">{r.qty_on_hand}</td>
                  <td className="right text-sm">{r.qty_reserved}</td>
                  <td className="right text-sm">{r.qty_available}</td>
                  <td className="right text-sm">Rp {formatRupiah(r.stock_value)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}

export default function InventoryPage() {
  const { t } = useTranslation(['inventory', 'common'])
  const [tab, setTab] = useState<Tab>('categories')

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">{t('inventory:pageTitle')}</h1>
        <p className="text-sm text-gray-500 mt-0.5">{t('inventory:pageSubtitle')}</p>
      </div>

      <div className="flex gap-1 border-b border-gray-200">
        {TABS.map((tb) => (
          <button key={tb} onClick={() => setTab(tb)}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
              tab === tb ? 'border-primary-600 text-primary-700' : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}>
            {t(`inventory:tab_${tb}`)}
          </button>
        ))}
      </div>

      {tab === 'categories' && <CategoriesTab />}
      {tab === 'products' && <ProductsTab />}
      {tab === 'receipts' && <ReceiptsTab />}
      {tab === 'stock' && <StockTab />}
    </div>
  )
}
