import { useTranslation } from 'react-i18next'
import { cn, getStatusColor } from '../../lib/utils'

interface BadgeProps {
  status: string
  label?: string
  className?: string
}

export default function Badge({ status, label, className }: BadgeProps) {
  const { t } = useTranslation('common')
  const translated = t(status, { defaultValue: '' })

  return (
    <span className={cn(
      'inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium capitalize',
      getStatusColor(status),
      className,
    )}>
      {label ?? (translated || status)}
    </span>
  )
}
