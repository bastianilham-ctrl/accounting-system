import { useTranslation } from 'react-i18next'
import { FileSearch } from 'lucide-react'

interface EmptyStateProps {
  title?: string
  description?: string
  icon?: React.ReactNode
}

export default function EmptyState({
  title,
  description,
  icon,
}: EmptyStateProps) {
  const { t } = useTranslation('common')
  title ??= t('noData')
  description ??= t('noDataDescription')
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center">
      <div className="text-gray-300 mb-4">
        {icon ?? <FileSearch className="h-12 w-12" />}
      </div>
      <p className="text-base font-medium text-gray-500">{title}</p>
      <p className="text-sm text-gray-400 mt-1 max-w-xs">{description}</p>
    </div>
  )
}
