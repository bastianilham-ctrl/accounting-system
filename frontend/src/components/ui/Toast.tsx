import { useEffect, useState } from 'react'
import { CheckCircle, XCircle, AlertCircle, X } from 'lucide-react'
import { cn } from '../../lib/utils'

export type ToastType = 'success' | 'error' | 'warning'

interface Toast {
  id: number
  type: ToastType
  message: string
}

let _setToasts: React.Dispatch<React.SetStateAction<Toast[]>> | null = null
let _counter = 0

export function showToast(message: string, type: ToastType = 'success') {
  if (_setToasts) {
    const id = ++_counter
    _setToasts((prev) => [...prev, { id, type, message }])
    setTimeout(() => {
      _setToasts?.((prev) => prev.filter((t) => t.id !== id))
    }, 4000)
  }
}

const ICONS = {
  success: <CheckCircle className="h-5 w-5 text-green-500" />,
  error:   <XCircle className="h-5 w-5 text-red-500" />,
  warning: <AlertCircle className="h-5 w-5 text-amber-500" />,
}
const BG = {
  success: 'border-l-4 border-green-500',
  error:   'border-l-4 border-red-500',
  warning: 'border-l-4 border-amber-500',
}

export function ToastContainer() {
  const [toasts, setToasts] = useState<Toast[]>([])
  _setToasts = setToasts

  return (
    <div className="fixed bottom-4 right-4 z-50 space-y-2 max-w-sm">
      {toasts.map((t) => (
        <div key={t.id}
          className={cn('flex items-start gap-3 bg-white rounded-xl shadow-lg p-4', BG[t.type])}>
          {ICONS[t.type]}
          <p className="text-sm text-gray-700 flex-1">{t.message}</p>
          <button onClick={() => setToasts((prev) => prev.filter((x) => x.id !== t.id))}
            className="text-gray-400 hover:text-gray-600">
            <X className="h-4 w-4" />
          </button>
        </div>
      ))}
    </div>
  )
}
