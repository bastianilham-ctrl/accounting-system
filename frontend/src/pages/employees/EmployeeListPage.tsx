import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Search, RefreshCw, UserPlus, Users } from 'lucide-react'
import { useAuth } from '../../contexts/AuthContext'
import api from '../../lib/api'
import { Card, CardHeader } from '../../components/ui/Card'
import Spinner from '../../components/ui/Spinner'
import EmptyState from '../../components/ui/EmptyState'
import Badge from '../../components/ui/Badge'
import { formatDate } from '../../lib/utils'

export default function EmployeeListPage() {
  const { entityId } = useAuth()
  const { t } = useTranslation(['employees', 'common'])
  const [search, setSearch] = useState('')
  const [deptFilter, setDeptFilter] = useState('')

  const EMP_TYPE_LABELS: Record<string, string> = {
    permanent: t('employees:type_permanent'),
    contract:  t('employees:type_contract'),
    parttime:  t('employees:type_parttime'),
    intern:    t('employees:type_intern'),
  }

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['employees', entityId],
    queryFn: () =>
      api.get('/employees', { params: { entity_id: entityId, size: 500 } }).then((r) => r.data),
    enabled: !!entityId,
  })

  const employees: any[] = Array.isArray(data) ? data : (data?.items ?? data?.employees ?? [])

  // Departments for filter
  const departments = [...new Set(employees.map((e) => e.department).filter(Boolean))]

  const filtered = employees.filter((e) => {
    const matchSearch =
      !search ||
      e.full_name?.toLowerCase().includes(search.toLowerCase()) ||
      e.employee_code?.toLowerCase().includes(search.toLowerCase()) ||
      e.email?.toLowerCase().includes(search.toLowerCase())
    const matchDept = !deptFilter || e.department === deptFilter
    return matchSearch && matchDept
  })

  // Stats
  const statsByType: Record<string, number> = {}
  employees.forEach((e) => {
    const t = e.employment_type ?? 'permanent'
    statsByType[t] = (statsByType[t] ?? 0) + 1
  })

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">{t('employees:title')}</h1>
          <p className="text-sm text-gray-500 mt-0.5">{t('employees:subtitle')}</p>
        </div>
        <button className="btn-primary">
          <UserPlus className="h-4 w-4" /> {t('employees:addEmployee')}
        </button>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="card p-4 flex items-center gap-3">
          <div className="h-10 w-10 bg-primary-50 rounded-xl flex items-center justify-center">
            <Users className="h-5 w-5 text-primary-600" />
          </div>
          <div>
            <p className="text-2xl font-bold text-gray-900">{employees.length}</p>
            <p className="text-xs text-gray-500">{t('employees:totalEmployees')}</p>
          </div>
        </div>
        {Object.entries(statsByType).map(([type, count]) => (
          <div key={type} className="card p-4">
            <p className="text-2xl font-bold text-gray-900">{count}</p>
            <p className="text-xs text-gray-500">{EMP_TYPE_LABELS[type] ?? type}</p>
          </div>
        ))}
      </div>

      <Card noPad>
        <CardHeader
          title={t('employees:listTitle')}
          subtitle={t('employees:listSubtitle', { count: filtered.length })}
          actions={
            <button onClick={() => refetch()} className="btn-secondary">
              <RefreshCw className="h-4 w-4" />
            </button>
          }
        />

        {/* Filters */}
        <div className="px-6 py-3 border-b border-gray-100 flex items-center gap-3 flex-wrap">
          <div className="relative flex-1 min-w-48">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
            <input value={search} onChange={(e) => setSearch(e.target.value)}
              placeholder={t('employees:searchPlaceholder')} className="form-input pl-9" />
          </div>
          {departments.length > 0 && (
            <select value={deptFilter} onChange={(e) => setDeptFilter(e.target.value)}
              className="form-select w-44">
              <option value="">{t('employees:allDepartments')}</option>
              {departments.map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
          )}
        </div>

        {isLoading ? (
          <div className="flex justify-center py-16"><Spinner size="lg" /></div>
        ) : filtered.length === 0 ? (
          <EmptyState title={t('employees:emptyTitle')} description={t('employees:emptyDescription')} />
        ) : (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>{t('employees:colCode')}</th>
                  <th>{t('employees:colFullName')}</th>
                  <th>{t('employees:colEmail')}</th>
                  <th>{t('employees:colDepartment')}</th>
                  <th>{t('employees:colPosition')}</th>
                  <th>{t('employees:colType')}</th>
                  <th>{t('employees:colJoinDate')}</th>
                  <th>{t('employees:colPtkp')}</th>
                  <th>{t('employees:colStatus')}</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((e) => (
                  <tr key={e.id}>
                    <td className="font-mono text-xs text-gray-500">{e.employee_code ?? '-'}</td>
                    <td className="font-medium text-sm">{e.full_name}</td>
                    <td className="text-sm text-gray-500">{e.email}</td>
                    <td className="text-sm">{e.department ?? '-'}</td>
                    <td className="text-sm">{e.position ?? '-'}</td>
                    <td>
                      <span className="text-xs bg-blue-50 text-blue-700 px-2 py-0.5 rounded-full">
                        {EMP_TYPE_LABELS[e.employment_type ?? 'permanent'] ?? e.employment_type}
                      </span>
                    </td>
                    <td className="text-sm text-gray-500">{formatDate(e.join_date)}</td>
                    <td className="text-xs font-mono text-gray-500">{e.ptkp_status ?? 'TK/0'}</td>
                    <td>
                      <Badge
                        status={e.status ?? 'active'}
                        label={e.status === 'active' ? t('employees:active') : (e.status ?? 'active')}
                      />
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
