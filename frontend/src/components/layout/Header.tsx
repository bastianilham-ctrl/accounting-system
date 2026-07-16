import { useTranslation } from 'react-i18next'
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { LogOut, Building2, User, Languages, ChevronDown } from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext'
import { changeLanguage } from '../../lib/i18n'
import api from '../../lib/api'

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

      {/* Language toggle + User menu */}
      <div className="flex items-center gap-3">
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
