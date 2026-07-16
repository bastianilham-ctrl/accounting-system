import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, RefreshCw, UserX, UserCheck, Key, X } from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext'
import api from '../../lib/api'
import { Card, CardHeader } from '../../components/ui/Card'
import Spinner from '../../components/ui/Spinner'
import EmptyState from '../../components/ui/EmptyState'
import Badge from '../../components/ui/Badge'
import { formatDate } from '../../lib/utils'
import { showToast } from '../../components/ui/Toast'

const ROLES = ['viewer', 'finance', 'approver', 'admin', 'superadmin']

export default function UsersPage() {
  const { t } = useTranslation(['settings', 'common'])
  const { entityId, user: currentUser } = useAuth()
  const qc = useQueryClient()
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({
    username: '', email: '', full_name: '', password: '', role: 'finance', entity_id: entityId ?? '',
  })

  // ── List users ───────────────────────────────────────────────────────────────
  const { data, isLoading, refetch } = useQuery({
    queryKey: ['users'],
    queryFn: () => api.get('/auth/users').then(r => r.data),
  })
  const users: any[] = Array.isArray(data) ? data : (data?.users ?? [])

  // ── Create user ──────────────────────────────────────────────────────────────
  const createMutation = useMutation({
    mutationFn: () =>
      api.post('/auth/register', {
        username:  form.username,
        email:     form.email,
        full_name: form.full_name,
        password:  form.password,
        role:      form.role,
        entity_id: form.entity_id || undefined,
      }),
    onSuccess: () => {
      showToast(t('settings:users_createSuccess'))
      setShowForm(false)
      setForm({ username: '', email: '', full_name: '', password: '', role: 'finance', entity_id: entityId ?? '' })
      qc.invalidateQueries({ queryKey: ['users'] })
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('settings:users_createFailed'), 'error'),
  })

  // ── Toggle active ────────────────────────────────────────────────────────────
  const toggleMutation = useMutation({
    mutationFn: ({ id, is_active }: { id: string; is_active: boolean }) =>
      api.put(`/auth/users/${id}`, { is_active }),
    onSuccess: () => {
      showToast(t('settings:users_statusUpdated'))
      qc.invalidateQueries({ queryKey: ['users'] })
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('settings:users_genericFailed'), 'error'),
  })

  // ── Update role ──────────────────────────────────────────────────────────────
  const [editingRole, setEditingRole] = useState<{ id: string; role: string } | null>(null)
  const updateRoleMutation = useMutation({
    mutationFn: ({ id, role }: { id: string; role: string }) =>
      api.put(`/auth/users/${id}`, { role }),
    onSuccess: () => {
      showToast(t('settings:users_roleUpdated'))
      setEditingRole(null)
      qc.invalidateQueries({ queryKey: ['users'] })
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('settings:users_genericFailed'), 'error'),
  })

  // ── Grant/Revoke entity permission ──────────────────────────────────────────
  const [permForm, setPermForm] = useState({ user_id: '', role: 'viewer' })
  const grantMutation = useMutation({
    mutationFn: () =>
      api.post('/permissions/grant', {
        user_id: permForm.user_id, entity_id: entityId, role: permForm.role,
      }),
    onSuccess: () => {
      showToast(t('settings:users_accessGranted'))
      setPermForm({ user_id: '', role: 'viewer' })
    },
    onError: (e: any) => showToast(e?.response?.data?.detail ?? t('settings:users_genericFailed'), 'error'),
  })

  const isSuperadmin = currentUser?.role === 'superadmin'

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">{t('settings:users_title')}</h1>
          <p className="text-sm text-gray-500 mt-0.5">{t('settings:users_subtitle')}</p>
        </div>
        <button onClick={() => setShowForm(true)} className="btn-primary">
          <Plus className="h-4 w-4" /> {t('settings:users_createNew')}
        </button>
      </div>

      {/* Create form */}
      {showForm && (
        <Card>
          <div className="flex items-center justify-between mb-4">
            <p className="text-sm font-semibold text-gray-700">{t('settings:users_registerNew')}</p>
            <button onClick={() => setShowForm(false)}><X className="h-4 w-4 text-gray-400" /></button>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
            <div>
              <label className="form-label">{t('settings:users_username')}</label>
              <input value={form.username}
                onChange={e => setForm({ ...form, username: e.target.value })}
                className="form-input" placeholder="john.doe" />
            </div>
            <div>
              <label className="form-label">{t('settings:users_email')}</label>
              <input type="email" value={form.email}
                onChange={e => setForm({ ...form, email: e.target.value })}
                className="form-input" placeholder="john@company.com" />
            </div>
            <div>
              <label className="form-label">{t('settings:users_fullName')}</label>
              <input value={form.full_name}
                onChange={e => setForm({ ...form, full_name: e.target.value })}
                className="form-input" placeholder="John Doe" />
            </div>
            <div>
              <label className="form-label">{t('settings:users_password')}</label>
              <input type="password" value={form.password}
                onChange={e => setForm({ ...form, password: e.target.value })}
                className="form-input" placeholder={t('settings:users_passwordPlaceholder')} />
            </div>
            <div>
              <label className="form-label">{t('settings:users_role')}</label>
              <select value={form.role}
                onChange={e => setForm({ ...form, role: e.target.value })}
                className="form-select">
                {ROLES.filter(r => isSuperadmin || !['admin','superadmin'].includes(r)).map(r => (
                  <option key={r} value={r}>{r}</option>
                ))}
              </select>
            </div>
            {isSuperadmin && (
              <div>
                <label className="form-label">{t('settings:users_entityIdOptional')}</label>
                <input value={form.entity_id}
                  onChange={e => setForm({ ...form, entity_id: e.target.value })}
                  className="form-input font-mono text-sm" placeholder={t('settings:users_entityIdPlaceholder')} />
              </div>
            )}
          </div>
          <div className="flex gap-3 mt-4">
            <button onClick={() => createMutation.mutate()}
              disabled={createMutation.isPending || !form.username || !form.email || !form.password}
              className="btn-primary">
              {createMutation.isPending ? t('settings:users_creating') : t('settings:users_createUser')}
            </button>
            <button onClick={() => setShowForm(false)} className="btn-secondary">{t('common:cancel')}</button>
          </div>
        </Card>
      )}

      {/* Grant permission form */}
      <Card>
        <p className="text-sm font-semibold text-gray-700 mb-3">{t('settings:users_grantAccessToThisEntity')}</p>
        <div className="flex items-end gap-3">
          <div className="flex-1">
            <label className="form-label">{t('settings:users_userId')}</label>
            <input value={permForm.user_id}
              onChange={e => setPermForm({ ...permForm, user_id: e.target.value })}
              className="form-input font-mono text-sm" placeholder={t('settings:users_userIdPlaceholder')} />
          </div>
          <div>
            <label className="form-label">{t('settings:users_role')}</label>
            <select value={permForm.role}
              onChange={e => setPermForm({ ...permForm, role: e.target.value })}
              className="form-select">
              {['viewer','finance','approver','admin'].map(r => (
                <option key={r} value={r}>{r}</option>
              ))}
            </select>
          </div>
          <button onClick={() => grantMutation.mutate()}
            disabled={grantMutation.isPending || !permForm.user_id}
            className="btn-secondary">
            <Key className="h-4 w-4" /> {t('settings:users_grant')}
          </button>
        </div>
      </Card>

      {/* Users table */}
      <Card noPad>
        <CardHeader
          title={t('settings:users_listTitle')}
          subtitle={`${users.length} user`}
          actions={<button onClick={() => refetch()} className="btn-secondary"><RefreshCw className="h-4 w-4" /></button>}
        />
        {isLoading ? (
          <div className="flex justify-center py-16"><Spinner size="lg" /></div>
        ) : users.length === 0 ? (
          <EmptyState title={t('settings:users_noUsers')} />
        ) : (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>{t('settings:users_username')}</th>
                  <th>{t('settings:users_colName')}</th>
                  <th>{t('settings:users_email')}</th>
                  <th>{t('settings:users_role')}</th>
                  <th>{t('common:entity')}</th>
                  <th>{t('settings:users_colLastLogin')}</th>
                  <th>{t('common:status')}</th>
                  <th>{t('common:actions')}</th>
                </tr>
              </thead>
              <tbody>
                {users.map((u: any) => (
                  <tr key={u.id}>
                    <td className="font-mono text-sm font-medium">{u.username}</td>
                    <td className="text-sm">{u.full_name}</td>
                    <td className="text-sm text-gray-500">{u.email}</td>
                    <td>
                      {editingRole?.id === u.id ? (
                        <div className="flex items-center gap-1">
                          <select value={editingRole.role}
                            onChange={e => setEditingRole({ ...editingRole, role: e.target.value })}
                            className="form-select text-xs py-0.5">
                            {ROLES.map(r => <option key={r} value={r}>{r}</option>)}
                          </select>
                          <button onClick={() => updateRoleMutation.mutate({ id: u.id, role: editingRole.role })}
                            className="text-xs text-green-600 hover:underline">{t('common:save')}</button>
                          <button onClick={() => setEditingRole(null)}
                            className="text-xs text-gray-400 hover:underline">{t('common:cancel')}</button>
                        </div>
                      ) : (
                        <button onClick={() => setEditingRole({ id: u.id, role: u.role })}
                          className={`text-xs px-2 py-0.5 rounded-full font-medium cursor-pointer
                            ${u.role === 'superadmin' ? 'bg-red-50 text-red-700' :
                              u.role === 'admin' ? 'bg-orange-50 text-orange-700' :
                              u.role === 'approver' ? 'bg-purple-50 text-purple-700' :
                              u.role === 'finance' ? 'bg-blue-50 text-blue-700' :
                              'bg-gray-100 text-gray-600'}`}>
                          {u.role}
                        </button>
                      )}
                    </td>
                    <td className="text-xs text-gray-400 font-mono">{u.entity_name ?? u.entity_id?.slice(0, 8) ?? '—'}</td>
                    <td className="text-sm text-gray-500">{u.last_login_at ? formatDate(u.last_login_at) : '—'}</td>
                    <td>
                      <span className={`text-xs px-2 py-0.5 rounded-full ${u.is_active ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-600'}`}>
                        {u.is_active ? t('settings:users_active') : t('settings:users_inactive')}
                      </span>
                    </td>
                    <td>
                      {u.id !== currentUser?.id && (
                        <button
                          onClick={() => toggleMutation.mutate({ id: u.id, is_active: !u.is_active })}
                          className={`inline-flex items-center gap-1 text-xs px-2 py-1 rounded-md ${
                            u.is_active
                              ? 'bg-red-50 text-red-600 hover:bg-red-100'
                              : 'bg-green-50 text-green-600 hover:bg-green-100'
                          }`}>
                          {u.is_active ? <><UserX className="h-3 w-3" /> {t('settings:users_deactivate')}</> : <><UserCheck className="h-3 w-3" /> {t('settings:users_activate')}</>}
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
