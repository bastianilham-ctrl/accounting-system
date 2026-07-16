import { useTranslation } from 'react-i18next'
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { LogOut, Building2, User, Languages, ChevronDown, Bell } from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext'
import { changeLanguage } from '../../lib/i18n'
import api from '../../lib/api'

function NotificationBell({ entityId }: { entityId: string }) {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)

  const { data } = useQuery({
    queryKey: ['notifications', entityId],
    queryFn: () => api.get(`/notifications/${entityId}`, { params: { unread_only: false } }).then(r => r.data),
    enabled: !!entityId,
    refetchInterval: 60000,
  })

  const items: any[] = Array.isArray(data) ? data : []
  const unread = items.filter(n => !n.is_read).length

  const readOne = useMutation({
    mutationFn: (id: string) => api.post(`/notifications/${id}/read`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['notifications'] }),
  })
  const readAll = useMutation({
    mutationFn: () => api.post(`/notifications/read-all/${entityId}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['notifications'] }),
  })

  return (
    <div className="relative">
      <button onClick={() => setOpen(o => !o)}
        className="relative p-2 rounded-lg hover:bg-gray-50 transition-colors text-gray-500">
        <Bell className="h-5 w-5" />
        {unread > 0 && (
          <span className="absolute -top-0.5 -right-0.5 h-4 w-4 rounded-full bg-red-500 text-white text-[9px] font-bold flex items-center justify-center">
            {unread > 9 ? '9+' : unread}
          </span>
        )}
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute right-0 mt-1 w-80 bg-white rounded-xl shadow-xl border border-gray-200 z-20 overflow-hidden">
            <div className="flex items-center justify-between px-4 py-2.5 border-b border-gray-100">
              <p className="text-sm font-semibold text-gray-900">Notifikasi</p>
              {unread > 0 && (
                <button onClick={() => readAll.mutate()} className="text-xs text-blue-600 hover:underline">
                  Tandai semua dibaca
                </button>
              )}
            </div>
            <div className="max-h-80 overflow-y-auto divide-y divide-gray-50">
              {items.length === 0 ? (
                <p className="text-sm text-gray-400 text-center py-6">Tidak ada notifikasi</p>
              ) : items.slice(0, 20).map(n => (
                <div key={n.id}
                  className={`px-4 py-3 hover:bg-gray-50 cursor-pointer transition-colors ${!n.is_read ? 'bg-blue-50/60' : ''}`}
                  onClick={() => { if (!n.is_read) readOne.mutate(n.id) }}>
                  <div className="flex items-start gap-2">
                    {!n.is_read && <span className="mt-1.5 h-2 w-2 rounded-full bg-blue-500 flex-shrink-0" />}
                    {n.is_read && <span className="mt-1.5 h-2 w-2 rounded-full bg-transparent flex-shrink-0" />}
                    <div className="min-w-0">
                      <p className={`text-sm ${!n.is_read ? 'font-medium text-gray-900' : 'text-gray-600'} line-clamp-2`}>
                        {n.message ?? n.title ?? '—'}
                      </p>
                      <p className="text-xs text-gray-400 mt-0.5">{n.created_at ? new Date(n.created_at).toLocaleString('id-ID') : ''}</p>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  )
}

export default function Header() {
  const { user, entityId, setEntityId, logout } = useAuth()
  const { t, i18n } = useTranslation()
  const [showUserMenu, setShowUserMenu] = useState(false)

  const isSuperAdmin = user?.role === 'superadmin'

  const { data: myEntities, isLoading: entitiesLoading } = useQuery({
    queryKey: ['my-entities'],
    queryFn: () => api.get('/permissions/me/entities').then((r) => r.data),
    enabled: isSuperAdmin,
  })
  const entityOptions: any[] = Array.isArray(myEntities) ? myEntities : []

  return (
    <header className="fixed top-0 left-60 right-0 h-14 bg-white border-b border-gray-200 z-20 flex items-center justify-between px-6">
      {/* Entity selector */}
      <div className="flex items-center gap-2">
        <Building2 className="h-4 w-4 text-gray-400" />
        {isSuperAdmin ? (
          <select
            value={entityId}
            onChange={(e) => setEntityId(e.target.value)}
            disabled={entitiesLoading}
            className="form-select py-1 text-sm font-medium border-none bg-transparent hover:bg-gray-50 focus:ring-1 max-w-64"
          >
            <option value="">{t('selectEntity')}</option>
            {entityOptions.map((e: any) => (
              <option key={e.entity_id} value={e.entity_id}>{e.entity_name}</option>
            ))}
          </select>
        ) : (
          <span className="text-sm text-gray-600 font-medium">
            {entityId ? entityId.substring(0, 8) + '…' : 'Entity tidak terkonfigurasi'}
          </span>
        )}
      </div>

      {/* Language toggle + Notifications + User menu */}
      <div className="flex items-center gap-3">
        {entityId && <NotificationBell entityId={entityId} />}
        <button
          onClick={() => changeLanguage(i18n.language === 'id' ? 'en' : 'id')}
          title={t('language')}
          className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-sm text-gray-600 hover:bg-gray-50 transition-colors"
        >
          <Languages className="h-4 w-4" />
          <span className={i18n.language === 'id' ? 'font-semibold text-primary-600' : 'text-gray-400'}>ID</span>
          <span className="text-gray-300">·</span>
          <span className={i18n.language === 'en' ? 'font-semibold text-primary-600' : 'text-gray-400'}>EN</span>
        </button>

      <div className="relative">
        <button
          onClick={() => setShowUserMenu(!showUserMenu)}
          className="flex items-center gap-2.5 hover:bg-gray-50 rounded-lg px-3 py-1.5 transition-colors"
        >
          <div className="h-7 w-7 bg-primary-600 rounded-full flex items-center justify-center">
            <User className="h-4 w-4 text-white" />
          </div>
          <div className="text-left hidden sm:block">
            <p className="text-sm font-medium text-gray-700 leading-none">{user?.full_name}</p>
            <p className="text-xs text-gray-400 mt-0.5 capitalize">{user?.role}</p>
          </div>
          <ChevronDown className="h-4 w-4 text-gray-400" />
        </button>

        {showUserMenu && (
          <>
            <div className="fixed inset-0 z-10" onClick={() => setShowUserMenu(false)} />
            <div className="absolute right-0 mt-1 w-48 bg-white rounded-xl shadow-lg border border-gray-200 z-20 py-1">
              <div className="px-4 py-2.5 border-b border-gray-100">
                <p className="text-sm font-medium text-gray-900">{user?.full_name}</p>
                <p className="text-xs text-gray-400">{user?.email}</p>
              </div>
              <button
                onClick={() => { logout(); setShowUserMenu(false) }}
                className="w-full flex items-center gap-2 px-4 py-2 text-sm text-red-600 hover:bg-red-50 transition-colors"
              >
                <LogOut className="h-4 w-4" />
                {t('logout')}
              </button>
            </div>
          </>
        )}
        </div>
      </div>
    </header>
  )
}
