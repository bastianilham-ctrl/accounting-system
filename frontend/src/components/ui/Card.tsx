import { cn } from '../../lib/utils'

interface CardProps {
  children: React.ReactNode
  className?: string
  noPad?: boolean
}

export function Card({ children, className, noPad }: CardProps) {
  return (
    <div className={cn('card', className)}>
      {noPad ? children : <div className="card-body">{children}</div>}
    </div>
  )
}

export function CardHeader({ title, subtitle, actions }: {
  title: string
  subtitle?: string
  actions?: React.ReactNode
}) {
  return (
    <div className="card-header flex items-center justify-between">
      <div>
        <h3 className="text-base font-semibold text-gray-900">{title}</h3>
        {subtitle && <p className="text-sm text-gray-500 mt-0.5">{subtitle}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  )
}
