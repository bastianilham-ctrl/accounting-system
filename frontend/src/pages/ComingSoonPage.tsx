import { Construction } from 'lucide-react'
import { useLocation } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

export default function ComingSoonPage() {
  const loc = useLocation()
  const { t } = useTranslation(['comingsoon', 'common'])
  return (
    <div className="flex flex-col items-center justify-center h-64 text-center">
      <Construction className="h-12 w-12 text-gray-300 mb-4" />
      <p className="text-lg font-medium text-gray-600">{t('comingsoon:title')}</p>
      <p className="text-sm text-gray-400 mt-1">
        <code className="bg-gray-100 px-1.5 py-0.5 rounded text-xs">{loc.pathname}</code>
      </p>
      <p className="text-sm text-gray-400 mt-1">{t('comingsoon:description')}</p>
    </div>
  )
}
