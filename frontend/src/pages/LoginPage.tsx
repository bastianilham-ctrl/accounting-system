import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useAuth } from '../contexts/AuthContext'
import { Calculator, Eye, EyeOff, LogIn } from 'lucide-react'
import Spinner from '../components/ui/Spinner'

export default function LoginPage() {
  const { login } = useAuth()
  const { t } = useTranslation(['auth', 'common'])
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [showPwd, setShowPwd] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      await login(username, password)
    } catch (err: any) {
      setError(
        err?.response?.data?.detail ||
        t('auth:loginFailed')
      )
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-gray-900 via-gray-800 to-primary-900 flex items-center justify-center p-4">
      <div className="w-full max-w-sm">
        {/* Logo */}
        <div className="flex flex-col items-center mb-8">
          <div className="h-14 w-14 bg-primary-600 rounded-2xl flex items-center justify-center shadow-lg mb-4">
            <Calculator className="h-8 w-8 text-white" />
          </div>
          <h1 className="text-2xl font-bold text-white">Accounting System</h1>
          <p className="text-gray-400 text-sm mt-1">{t('auth:subtitle')}</p>
        </div>

        {/* Form */}
        <div className="bg-white rounded-2xl shadow-2xl p-8">
          {error && (
            <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
              {error}
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-5">
            <div>
              <label className="form-label">{t('auth:username')}</label>
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className="form-input"
                placeholder={t('auth:usernamePlaceholder')}
                autoComplete="username"
                required
              />
            </div>

            <div>
              <label className="form-label">{t('auth:password')}</label>
              <div className="relative">
                <input
                  type={showPwd ? 'text' : 'password'}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="form-input pr-10"
                  placeholder={t('auth:passwordPlaceholder')}
                  autoComplete="current-password"
                  required
                />
                <button
                  type="button"
                  onClick={() => setShowPwd(!showPwd)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
                >
                  {showPwd ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
            </div>

            <button
              type="submit"
              disabled={loading}
              className="btn-primary w-full justify-center py-2.5"
            >
              {loading ? (
                <><Spinner size="sm" /><span>{t('auth:loggingIn')}</span></>
              ) : (
                <><LogIn className="h-4 w-4" /><span>{t('auth:login')}</span></>
              )}
            </button>
          </form>
        </div>

        <p className="text-center text-xs text-gray-500 mt-6">
          {t('auth:copyright')}
        </p>
      </div>
    </div>
  )
}
