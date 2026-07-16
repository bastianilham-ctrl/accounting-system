import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery, useMutation } from '@tanstack/react-query'
import { Plus, RefreshCw } from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext'
import api from '../../lib/api'
import { Card, CardHeader } from '../../components/ui/Card'
import Spinner from '../../components/ui/Spinner'
import EmptyState from '../../components/ui/EmptyState'
import { showToast } from '../../components/ui/Toast'

const TABS = ['groups', 'items', 'mapping'] as const
type Tab = typeof TABS[number]

function MaterialGroupsTab() {
  const { t } = useTranslation(['itemMaster', 'common'])
  const { entityId } = useAuth()
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({ group_code: '', group_name: '' })

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['material-groups', entityId],
    queryFn: () => api.get('/procurement/material-groups', { params: { entity_id: entityId } }).then((r) => r.data),
    enabled: !!entityId,
  })
  const rows: any[] = Array.isArray(data) ? data : []

  const createMutation = useMutation({
    mutationFn: () => api.post('/procurement/material-groups', { entity_id: entityId, ...form }),
    onSuccess: () => {
      showToast(t('itemMaster:group_createSuccess'))
      setShowForm(false)
      setForm({ group_code: '', group_name: '' })
      refetch()
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('itemMaster:group_createFailed'), 'error'),
  })

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-end">
        <button onClick={() => setShowForm((s) => !s)} className="btn-primary">
          <Plus className="h-4 w-4" /> {t('itemMaster:group_newBtn')}
        </button>
      </div>

      {showForm && (
        <Card>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
            <div>
              <label className="form-label">{t('itemMaster:group_codeLabel')}</label>
              <input value={form.group_code} onChange={(e) => setForm({ ...form, group_code: e.target.value })} className="form-input" placeholder="ATK" />
            </div>
            <div>
              <label className="form-label">{t('itemMaster:group_nameLabel')}</label>
              <input value={form.group_name} onChange={(e) => setForm({ ...form, group_name: e.target.value })} className="form-input" placeholder="Alat Tulis Kantor" />
            </div>
          </div>
          <div className="flex justify-end gap-3">
            <button onClick={() => setShowForm(false)} className="btn-secondary">{t('common:cancel')}</button>
            <button onClick={() => createMutation.mutate()} disabled={createMutation.isPending || !form.group_code || !form.group_name} className="btn-primary">
              {createMutation.isPending ? t('common:saving') : t('common:save')}
            </button>
          </div>
        </Card>
      )}

      <Card noPad>
        <CardHeader title={t('itemMaster:group_listTitle')} subtitle={t('itemMaster:group_listSubtitle', { count: rows.length })}
          actions={<button onClick={() => refetch()} className="btn-secondary"><RefreshCw className="h-4 w-4" /></button>} />
        {isLoading ? (
          <div className="flex justify-center py-16"><Spinner size="lg" /></div>
        ) : rows.length === 0 ? (
          <EmptyState title={t('itemMaster:group_emptyTitle')} />
        ) : (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>{t('itemMaster:colGroupCode')}</th>
                  <th>{t('itemMaster:colGroupName')}</th>
                  <th>{t('itemMaster:colMappedCoa')}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r: any) => (
                  <tr key={r.id}>
                    <td className="text-sm font-medium">{r.group_code}</td>
                    <td className="text-sm text-gray-500">{r.group_name}</td>
                    <td className="text-sm">
                      {r.account_code ? (
                        <span className="text-gray-700">{r.account_code}</span>
                      ) : (
                        <span className="text-amber-600">{t('itemMaster:group_unmapped')}</span>
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

function ItemsTab() {
  const { t } = useTranslation(['itemMaster', 'common'])
  const { entityId } = useAuth()
  const [showForm, setShowForm] = useState(false)
  const [search, setSearch] = useState('')
  const [form, setForm] = useState({ sku_code: '', item_name: '', item_type: 'expense', material_group_id: '', uom: 'unit' })

  const { data: groupsData } = useQuery({
    queryKey: ['material-groups', entityId],
    queryFn: () => api.get('/procurement/material-groups', { params: { entity_id: entityId } }).then((r) => r.data),
    enabled: !!entityId,
  })
  const groups: any[] = Array.isArray(groupsData) ? groupsData : []

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['procurement-items', entityId, search],
    queryFn: () => api.get('/procurement/items', { params: { entity_id: entityId, search: search || undefined } }).then((r) => r.data),
    enabled: !!entityId,
  })
  const rows: any[] = Array.isArray(data) ? data : []

  const createMutation = useMutation({
    mutationFn: () => api.post('/procurement/items', { entity_id: entityId, ...form }),
    onSuccess: () => {
      showToast(t('itemMaster:item_createSuccess'))
      setShowForm(false)
      setForm({ sku_code: '', item_name: '', item_type: 'expense', material_group_id: '', uom: 'unit' })
      refetch()
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('itemMaster:item_createFailed'), 'error'),
  })

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <input value={search} onChange={(e) => setSearch(e.target.value)} className="form-input w-64" placeholder={t('itemMaster:item_searchPlaceholder')} />
        <button onClick={() => setShowForm((s) => !s)} className="btn-primary">
          <Plus className="h-4 w-4" /> {t('itemMaster:item_newBtn')}
        </button>
      </div>

      {showForm && (
        <Card>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
            <div>
              <label className="form-label">{t('itemMaster:item_skuLabel')}</label>
              <input value={form.sku_code} onChange={(e) => setForm({ ...form, sku_code: e.target.value })} className="form-input" />
            </div>
            <div>
              <label className="form-label">{t('itemMaster:item_nameLabel')}</label>
              <input value={form.item_name} onChange={(e) => setForm({ ...form, item_name: e.target.value })} className="form-input" />
            </div>
            <div>
              <label className="form-label">{t('itemMaster:item_typeLabel')}</label>
              <select value={form.item_type} onChange={(e) => setForm({ ...form, item_type: e.target.value })} className="form-select">
                {['goods', 'services', 'asset', 'expense'].map((tp) => (
                  <option key={tp} value={tp}>{t(`itemMaster:itemType_${tp}`)}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="form-label">{t('itemMaster:item_uomLabel')}</label>
              <input value={form.uom} onChange={(e) => setForm({ ...form, uom: e.target.value })} className="form-input" placeholder="unit" />
            </div>
            <div className="md:col-span-2">
              <label className="form-label">{t('itemMaster:item_groupLabel')}</label>
              <select value={form.material_group_id} onChange={(e) => setForm({ ...form, material_group_id: e.target.value })} className="form-select">
                <option value="">{t('itemMaster:item_selectGroup')}</option>
                {groups.map((g: any) => <option key={g.id} value={g.id}>{g.group_code} — {g.group_name}</option>)}
              </select>
            </div>
          </div>
          <div className="flex justify-end gap-3">
            <button onClick={() => setShowForm(false)} className="btn-secondary">{t('common:cancel')}</button>
            <button onClick={() => createMutation.mutate()}
              disabled={createMutation.isPending || !form.sku_code || !form.item_name || !form.material_group_id}
              className="btn-primary">
              {createMutation.isPending ? t('common:saving') : t('common:save')}
            </button>
          </div>
        </Card>
      )}

      <Card noPad>
        <CardHeader title={t('itemMaster:item_listTitle')} subtitle={t('itemMaster:item_listSubtitle', { count: rows.length })}
          actions={<button onClick={() => refetch()} className="btn-secondary"><RefreshCw className="h-4 w-4" /></button>} />
        {isLoading ? (
          <div className="flex justify-center py-16"><Spinner size="lg" /></div>
        ) : rows.length === 0 ? (
          <EmptyState title={t('itemMaster:item_emptyTitle')} />
        ) : (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>{t('itemMaster:colSku')}</th>
                  <th>{t('itemMaster:colItemName')}</th>
                  <th>{t('itemMaster:colType')}</th>
                  <th>{t('itemMaster:colGroup')}</th>
                  <th>{t('itemMaster:colMappedCoa')}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r: any) => (
                  <tr key={r.id}>
                    <td className="text-sm font-medium">{r.sku_code}</td>
                    <td className="text-sm text-gray-500">{r.item_name}</td>
                    <td className="text-sm text-gray-500">{t(`itemMaster:itemType_${r.item_type}`)}</td>
                    <td className="text-sm text-gray-500">{r.group_code}</td>
                    <td className="text-sm">
                      {r.account_code ? (
                        <span className="text-gray-700">{r.account_code}</span>
                      ) : (
                        <span className="text-amber-600">{t('itemMaster:group_unmapped')}</span>
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

function AccountMappingTab() {
  const { t } = useTranslation(['itemMaster', 'common'])
  const { entityId } = useAuth()
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({ material_group_id: '', account_code: '' })

  const { data: groupsData } = useQuery({
    queryKey: ['material-groups', entityId],
    queryFn: () => api.get('/procurement/material-groups', { params: { entity_id: entityId } }).then((r) => r.data),
    enabled: !!entityId,
  })
  const groups: any[] = Array.isArray(groupsData) ? groupsData : []

  const { data: coaData } = useQuery({
    queryKey: ['coa-expense', entityId],
    queryFn: () => api.get('/coa/', { params: { entity_id: entityId, account_type: 'expense', limit: 1000 } }).then((r) => r.data),
    enabled: !!entityId && showForm,
  })
  const coaOptions: any[] = (Array.isArray(coaData) ? coaData : []).filter((c: any) => !c.is_header)

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['account-mapping', entityId],
    queryFn: () => api.get('/procurement/account-mapping', { params: { entity_id: entityId } }).then((r) => r.data),
    enabled: !!entityId,
  })
  const rows: any[] = Array.isArray(data) ? data : []

  const saveMutation = useMutation({
    mutationFn: () => api.post('/procurement/account-mapping', { entity_id: entityId, ...form }),
    onSuccess: () => {
      showToast(t('itemMaster:mapping_saveSuccess'))
      setShowForm(false)
      setForm({ material_group_id: '', account_code: '' })
      refetch()
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('itemMaster:mapping_saveFailed'), 'error'),
  })

  return (
    <div className="space-y-4">
      <p className="text-xs text-gray-500">{t('itemMaster:mapping_hint')}</p>
      <div className="flex items-center justify-end">
        <button onClick={() => setShowForm((s) => !s)} className="btn-primary">
          <Plus className="h-4 w-4" /> {t('itemMaster:mapping_newBtn')}
        </button>
      </div>

      {showForm && (
        <Card>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
            <div>
              <label className="form-label">{t('itemMaster:item_groupLabel')}</label>
              <select value={form.material_group_id} onChange={(e) => setForm({ ...form, material_group_id: e.target.value })} className="form-select">
                <option value="">{t('itemMaster:item_selectGroup')}</option>
                {groups.map((g: any) => <option key={g.id} value={g.id}>{g.group_code} — {g.group_name}</option>)}
              </select>
            </div>
            <div>
              <label className="form-label">{t('itemMaster:mapping_coaLabel')}</label>
              <select value={form.account_code} onChange={(e) => setForm({ ...form, account_code: e.target.value })} className="form-select">
                <option value="">{t('itemMaster:mapping_selectCoa')}</option>
                {coaOptions.map((c: any) => <option key={c.account_code} value={c.account_code}>{c.account_code} — {c.account_name}</option>)}
              </select>
            </div>
          </div>
          <div className="flex justify-end gap-3">
            <button onClick={() => setShowForm(false)} className="btn-secondary">{t('common:cancel')}</button>
            <button onClick={() => saveMutation.mutate()}
              disabled={saveMutation.isPending || !form.material_group_id || !form.account_code}
              className="btn-primary">
              {saveMutation.isPending ? t('common:saving') : t('common:save')}
            </button>
          </div>
        </Card>
      )}

      <Card noPad>
        <CardHeader title={t('itemMaster:mapping_listTitle')} subtitle={t('itemMaster:mapping_listSubtitle', { count: rows.length })}
          actions={<button onClick={() => refetch()} className="btn-secondary"><RefreshCw className="h-4 w-4" /></button>} />
        {isLoading ? (
          <div className="flex justify-center py-16"><Spinner size="lg" /></div>
        ) : rows.length === 0 ? (
          <EmptyState title={t('itemMaster:mapping_emptyTitle')} />
        ) : (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>{t('itemMaster:colGroupCode')}</th>
                  <th>{t('itemMaster:colGroupName')}</th>
                  <th>{t('itemMaster:mapping_colCoa')}</th>
                  <th>{t('itemMaster:mapping_colCoaName')}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r: any) => (
                  <tr key={r.id}>
                    <td className="text-sm font-medium">{r.group_code}</td>
                    <td className="text-sm text-gray-500">{r.group_name}</td>
                    <td className="text-sm text-gray-700">{r.account_code}</td>
                    <td className="text-sm text-gray-500">{r.account_name ?? '—'}</td>
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

export default function ItemMasterPage() {
  const { t } = useTranslation(['itemMaster', 'common'])
  const [tab, setTab] = useState<Tab>('groups')

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">{t('itemMaster:pageTitle')}</h1>
        <p className="text-sm text-gray-500 mt-0.5">{t('itemMaster:pageSubtitle')}</p>
      </div>

      <div className="flex gap-1 border-b border-gray-200">
        {TABS.map((tb) => (
          <button key={tb} onClick={() => setTab(tb)}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
              tab === tb ? 'border-primary-600 text-primary-700' : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}>
            {t(`itemMaster:tab_${tb}`)}
          </button>
        ))}
      </div>

      {tab === 'groups' && <MaterialGroupsTab />}
      {tab === 'items' && <ItemsTab />}
      {tab === 'mapping' && <AccountMappingTab />}
    </div>
  )
}
