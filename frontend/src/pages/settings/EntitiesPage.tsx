import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { RefreshCw, Users, Shield, X } from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext'
import api from '../../lib/api'
import { Card, CardHeader } from '../../components/ui/Card'
import Spinner from '../../components/ui/Spinner'
import EmptyState from '../../components/ui/EmptyState'
import { showToast } from '../../components/ui/Toast'

const ROLE_COLORS: Record<string, string> = {
  superadmin: 'bg-red-50 text-red-700',
  admin:      'bg-orange-50 text-orange-700',
  approver:   'bg-purple-50 text-purple-700',
  finance:    'bg-blue-50 text-blue-700',
  viewer:     'bg-gray-100 text-gray-600',
}

export default function EntitiesPage() {
  const { t } = useTranslation(['settings', 'common'])
  const { entityId, user: currentUser } = useAuth()
  const qc = useQueryClient()

  // ── My entities list ─────────────────────────────────────────────────────────
  const { data: myEntities = [], isLoading } = useQuery({
    queryKey: ['my-entities'],
    queryFn: () => api.get('/permissions/me/entities').then(r => r.data),
  })

  // ── Entity users (for selected entity) ──────────────────────────────────────
  const [selectedEntityId, setSelectedEntityId] = useState<string | null>(entityId ?? null)
  const { data: entityUsers = [], isLoading: usersLoading, refetch: refetchUsers } = useQuery({
    queryKey: ['entity-users', selectedEntityId],
    queryFn: () =>
      api.get(`/permissions/entities/${selectedEntityId}/users`, {
        params: { include_inactive: false },
      }).then(r => r.data),
    enabled: !!selectedEntityId,
  })

  // ── Permission history ────────────────────────────────────────────────────────
  const { data: history = [], isLoading: histLoading } = useQuery({
    queryKey: ['perm-history', selectedEntityId],
    queryFn: () =>
      api.get('/permissions/history', { params: { entity_id: selectedEntityId, limit: 30 } })
        .then(r => r.data),
    enabled: !!selectedEntityId,
  })

  // ── Grant mutation ────────────────────────────────────────────────────────────
  const [grantForm, setGrantForm] = useState({ user_id: '', role: 'viewer' })
  const grantMutation = useMutation({
    mutationFn: () =>
      api.post('/permissions/grant', {
        user_id: grantForm.user_id,
        entity_id: selectedEntityId,
        role: grantForm.role,
        granted_by: currentUser?.username ?? '',
      }),
    onSuccess: () => {
      showToast(t('settings:entities_accessGranted'))
      setGrantForm({ user_id: '', role: 'viewer' })
      qc.invalidateQueries({ queryKey: ['entity-users', selectedEntityId] })
      qc.invalidateQueries({ queryKey: ['perm-history', selectedEntityId] })
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('settings:entities_genericFailed'), 'error'),
  })

  // ── Revoke mutation ───────────────────────────────────────────────────────────
  const revokeMutation = useMutation({
    mutationFn: ({ user_id }: { user_id: string }) =>
      api.post('/permissions/revoke', {
        user_id,
        entity_id: selectedEntityId,
        revoked_by: currentUser?.username ?? '',
        reason: 'Revoked via Settings UI',
      }),
    onSuccess: () => {
      showToast(t('settings:entities_accessRevoked'))
      qc.invalidateQueries({ queryKey: ['entity-users', selectedEntityId] })
      qc.invalidateQueries({ queryKey: ['perm-history', selectedEntityId] })
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('settings:entities_genericFailed'), 'error'),
  })

  // ── Update role ───────────────────────────────────────────────────────────────
  const updateRoleMutation = useMutation({
    mutationFn: ({ user_id, new_role }: { user_id: string; new_role: string }) =>
      api.put('/permissions/update-role', {
        user_id,
        entity_id: selectedEntityId,
        new_role,
        updated_by: currentUser?.username ?? '',
      }),
    onSuccess: () => {
      showToast(t('settings:entities_roleUpdated'))
      qc.invalidateQueries({ queryKey: ['entity-users', selectedEntityId] })
      qc.invalidateQueries({ queryKey: ['perm-history', selectedEntityId] })
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('settings:entities_genericFailed'), 'error'),
  })

  const [editingUserId, setEditingUserId] = useState<string | null>(null)
  const [editRole, setEditRole] = useState('')

  const selectedEntity = myEntities.find((e: any) => e.entity_id === selectedEntityId)

  // ── Base currency ─────────────────────────────────────────────────────────────
  const { data: entityDetail } = useQuery({
    queryKey: ['entity-detail', selectedEntityId],
    queryFn: () => api.get(`/entities/${selectedEntityId}`).then(r => r.data),
    enabled: !!selectedEntityId,
  })
  const { data: lockStatus } = useQuery({
    queryKey: ['entity-currency-lock', selectedEntityId],
    queryFn: () => api.get(`/entities/${selectedEntityId}/currency-lock-status`).then(r => r.data),
    enabled: !!selectedEntityId,
  })
  const { data: currencies = [] } = useQuery({
    queryKey: ['currencies-list'],
    queryFn: () => api.get('/multicurrency/currencies').then(r => r.data),
  })
  const [baseCurrency, setBaseCurrency] = useState('')
  const setCurrencyMutation = useMutation({
    mutationFn: () => api.patch(`/entities/${selectedEntityId}/currency`, { currency: baseCurrency }),
    onSuccess: () => {
      showToast(t('settings:entities_baseCurrencyUpdated'))
      qc.invalidateQueries({ queryKey: ['entity-detail', selectedEntityId] })
      qc.invalidateQueries({ queryKey: ['entity-currency-lock', selectedEntityId] })
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('settings:entities_genericFailed'), 'error'),
  })

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">{t('settings:entities_title')}</h1>
        <p className="text-sm text-gray-500 mt-0.5">{t('settings:entities_subtitle')}</p>
      </div>

      <div className="grid grid-cols-3 gap-6">
        {/* Entity list sidebar */}
        <div className="col-span-1">
          <Card noPad>
            <CardHeader title={t('settings:entities_myEntities')} subtitle={`${myEntities.length} entity`} />
            {isLoading ? (
              <div className="flex justify-center py-8"><Spinner /></div>
            ) : myEntities.length === 0 ? (
              <EmptyState title={t('settings:entities_noEntities')} />
            ) : (
              <ul className="divide-y divide-gray-100">
                {myEntities.map((e: any) => (
                  <li key={e.entity_id}>
                    <button
                      onClick={() => setSelectedEntityId(e.entity_id)}
                      className={`w-full text-left px-4 py-3 hover:bg-gray-50 transition-colors ${
                        selectedEntityId === e.entity_id ? 'bg-blue-50 border-l-2 border-blue-600' : ''
                      }`}>
                      <p className="text-sm font-medium text-gray-800">{e.entity_name}</p>
                      <span className={`text-xs px-1.5 py-0.5 rounded-full ${ROLE_COLORS[e.role] ?? ROLE_COLORS.viewer}`}>
                        {e.role}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>

        {/* Main content */}
        <div className="col-span-2 space-y-4">
          {!selectedEntityId ? (
            <Card>
              <EmptyState title={t('settings:entities_selectEntityPrompt')} />
            </Card>
          ) : (
            <>
              {/* Header */}
              <Card>
                <div className="flex items-center gap-3">
                  <div className="h-10 w-10 rounded-lg bg-blue-100 flex items-center justify-center">
                    <Shield className="h-5 w-5 text-blue-600" />
                  </div>
                  <div>
                    <p className="font-semibold text-gray-800">{selectedEntity?.entity_name ?? selectedEntityId}</p>
                    <p className="text-xs text-gray-400 font-mono">{selectedEntityId}</p>
                  </div>
                </div>
              </Card>

              {/* Base currency */}
              <Card>
                <p className="text-sm font-semibold text-gray-700 mb-1">{t('settings:entities_baseCurrency')}</p>
                <p className="text-xs text-gray-500 mb-3">{t('settings:entities_baseCurrencyDesc')}</p>
                <div className="flex items-end gap-3">
                  <div>
                    <label className="form-label">{t('settings:entities_baseCurrencyCurrent')}</label>
                    <div className="font-mono text-sm font-semibold text-gray-800 px-3 py-2 bg-gray-50 rounded-md border border-gray-200">
                      {entityDetail?.currency ?? '—'}
                    </div>
                  </div>
                  {lockStatus && !lockStatus.locked && (
                    <>
                      <div>
                        <label className="form-label">{t('settings:entities_baseCurrencySave')}</label>
                        <select
                          value={baseCurrency || entityDetail?.currency || ''}
                          onChange={e => setBaseCurrency(e.target.value)}
                          className="form-select">
                          {currencies.map((c: any) => (
                            <option key={c.currency_code} value={c.currency_code}>
                              {c.currency_code} — {c.currency_name}
                            </option>
                          ))}
                        </select>
                      </div>
                      <button
                        onClick={() => setCurrencyMutation.mutate()}
                        disabled={setCurrencyMutation.isPending || !baseCurrency || baseCurrency === entityDetail?.currency}
                        className="btn-primary">
                        {t('settings:entities_baseCurrencySave')}
                      </button>
                    </>
                  )}
                </div>
                {lockStatus && (
                  <p className={`text-xs mt-2 ${lockStatus.locked ? 'text-amber-600' : 'text-green-600'}`}>
                    {lockStatus.locked
                      ? t('settings:entities_baseCurrencyLocked', { count: lockStatus.journal_count })
                      : t('settings:entities_baseCurrencyUnlocked')}
                  </p>
                )}
              </Card>

              {/* Grant access form */}
              <Card>
                <p className="text-sm font-semibold text-gray-700 mb-3 flex items-center gap-2">
                  <Users className="h-4 w-4 text-gray-400" /> {t('settings:entities_addUserToThisEntity')}
                </p>
                <div className="flex items-end gap-3">
                  <div className="flex-1">
                    <label className="form-label">{t('settings:entities_userId')}</label>
                    <input
                      value={grantForm.user_id}
                      onChange={e => setGrantForm({ ...grantForm, user_id: e.target.value })}
                      className="form-input font-mono text-sm"
                      placeholder={t('settings:entities_userIdPlaceholder')} />
                  </div>
                  <div>
                    <label className="form-label">{t('settings:entities_role')}</label>
                    <select
                      value={grantForm.role}
                      onChange={e => setGrantForm({ ...grantForm, role: e.target.value })}
                      className="form-select">
                      {['viewer', 'finance', 'approver', 'admin'].map(r => (
                        <option key={r} value={r}>{r}</option>
                      ))}
                    </select>
                  </div>
                  <button
                    onClick={() => grantMutation.mutate()}
                    disabled={grantMutation.isPending || !grantForm.user_id}
                    className="btn-primary">
                    {t('settings:entities_grantAccess')}
                  </button>
                </div>
              </Card>

              {/* Entity users table */}
              <Card noPad>
                <CardHeader
                  title={t('settings:entities_usersWithAccess')}
                  subtitle={`${Array.isArray(entityUsers) ? entityUsers.length : 0} user`}
                  actions={<button onClick={() => refetchUsers()} className="btn-secondary"><RefreshCw className="h-4 w-4" /></button>}
                />
                {usersLoading ? (
                  <div className="flex justify-center py-8"><Spinner /></div>
                ) : !Array.isArray(entityUsers) || entityUsers.length === 0 ? (
                  <EmptyState title={t('settings:entities_noUsersYet')} />
                ) : (
                  <div className="overflow-x-auto">
                    <table className="data-table">
                      <thead>
                        <tr>
                          <th>{t('settings:entities_colUsername')}</th>
                          <th>{t('settings:entities_colName')}</th>
                          <th>{t('settings:entities_colRole')}</th>
                          <th>{t('settings:entities_colStatus')}</th>
                          <th>{t('settings:entities_colAction')}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {entityUsers.map((u: any) => (
                          <tr key={u.user_id ?? u.id}>
                            <td className="font-mono text-sm font-medium">{u.username}</td>
                            <td className="text-sm">{u.full_name}</td>
                            <td>
                              {editingUserId === (u.user_id ?? u.id) ? (
                                <div className="flex items-center gap-1">
                                  <select
                                    value={editRole}
                                    onChange={e => setEditRole(e.target.value)}
                                    className="form-select text-xs py-0.5">
                                    {['viewer', 'finance', 'approver', 'admin'].map(r => (
                                      <option key={r} value={r}>{r}</option>
                                    ))}
                                  </select>
                                  <button
                                    onClick={() => {
                                      updateRoleMutation.mutate({ user_id: u.user_id ?? u.id, new_role: editRole })
                                      setEditingUserId(null)
                                    }}
                                    className="text-xs text-green-600 hover:underline">{t('common:save')}</button>
                                  <button onClick={() => setEditingUserId(null)} className="text-xs text-gray-400 hover:underline">{t('common:cancel')}</button>
                                </div>
                              ) : (
                                <button
                                  onClick={() => { setEditingUserId(u.user_id ?? u.id); setEditRole(u.role) }}
                                  className={`text-xs px-2 py-0.5 rounded-full font-medium cursor-pointer ${ROLE_COLORS[u.role] ?? ROLE_COLORS.viewer}`}>
                                  {u.role}
                                </button>
                              )}
                            </td>
                            <td>
                              <span className={`text-xs px-2 py-0.5 rounded-full ${u.is_active ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-600'}`}>
                                {u.is_active ? t('settings:entities_active') : t('settings:entities_inactive')}
                              </span>
                            </td>
                            <td>
                              {(u.user_id ?? u.id) !== currentUser?.id && (
                                <button
                                  onClick={() => {
                                    if (confirm(t('settings:entities_revokeConfirm', { username: u.username }))) {
                                      revokeMutation.mutate({ user_id: u.user_id ?? u.id })
                                    }
                                  }}
                                  className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-md bg-red-50 text-red-600 hover:bg-red-100">
                                  <X className="h-3 w-3" /> {t('settings:entities_revoke')}
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

              {/* Permission history */}
              <Card noPad>
                <CardHeader title={t('settings:entities_changeHistory')} subtitle={t('settings:entities_latestEntries')} />
                {histLoading ? (
                  <div className="flex justify-center py-6"><Spinner /></div>
                ) : !Array.isArray(history) || history.length === 0 ? (
                  <EmptyState title={t('settings:entities_noHistory')} />
                ) : (
                  <div className="overflow-x-auto">
                    <table className="data-table">
                      <thead>
                        <tr>
                          <th>{t('settings:entities_colHistoryAction')}</th>
                          <th>{t('settings:entities_colUser')}</th>
                          <th>{t('settings:entities_colOldRole')}</th>
                          <th>{t('settings:entities_colNewRole')}</th>
                          <th>{t('settings:entities_colBy')}</th>
                          <th>{t('settings:entities_colTime')}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {history.map((h: any, i: number) => (
                          <tr key={i}>
                            <td>
                              <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                                h.action === 'grant' ? 'bg-green-50 text-green-700' :
                                h.action === 'revoke' ? 'bg-red-50 text-red-600' :
                                'bg-blue-50 text-blue-700'}`}>
                                {h.action}
                              </span>
                            </td>
                            <td className="text-sm font-mono">{h.user_id?.slice(0, 8) ?? '—'}</td>
                            <td className="text-sm text-gray-500">{h.old_role ?? '—'}</td>
                            <td className="text-sm">{h.new_role ?? '—'}</td>
                            <td className="text-sm text-gray-500">{h.performed_by ?? '—'}</td>
                            <td className="text-xs text-gray-400">{h.created_at ? new Date(h.created_at).toLocaleString('id-ID') : '—'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Card>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
