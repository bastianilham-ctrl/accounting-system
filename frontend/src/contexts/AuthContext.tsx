import React, { createContext, useContext, useState, useCallback, useEffect } from 'react'
import api from '../lib/api'

interface User {
  id: string
  username: string
  email: string
  full_name: string
  role: string
  entity_id: string | null
}

interface AuthState {
  user: User | null
  entityId: string
  isAuthenticated: boolean
  isLoading: boolean
}

interface AuthContextType extends AuthState {
  login: (username: string, password: string) => Promise<void>
  logout: () => void
  setEntityId: (id: string) => void
}

const AuthContext = createContext<AuthContextType | null>(null)

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<AuthState>({
    user: null,
    entityId: '',
    isAuthenticated: false,
    isLoading: true,
  })

  useEffect(() => {
    const token = localStorage.getItem('access_token')
    const stored = localStorage.getItem('user')
    const storedEntityId = localStorage.getItem('entity_id') || ''
    if (token && stored) {
      try {
        const user: User = JSON.parse(stored)
        setState({
          user,
          entityId: storedEntityId || user.entity_id || '',
          isAuthenticated: true,
          isLoading: false,
        })
      } catch {
        setState((s) => ({ ...s, isLoading: false }))
      }
    } else {
      setState((s) => ({ ...s, isLoading: false }))
    }
  }, [])

  const login = useCallback(async (username: string, password: string) => {
    const form = new URLSearchParams()
    form.append('username', username)
    form.append('password', password)
    const res = await api.post('/auth/login', form, {
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    })
    const { access_token, user } = res.data
    localStorage.setItem('access_token', access_token)
    localStorage.setItem('user', JSON.stringify(user))
    const eid = user.entity_id || ''
    localStorage.setItem('entity_id', eid)
    setState({ user, entityId: eid, isAuthenticated: true, isLoading: false })
  }, [])

  const logout = useCallback(() => {
    localStorage.removeItem('access_token')
    localStorage.removeItem('user')
    localStorage.removeItem('entity_id')
    setState({ user: null, entityId: '', isAuthenticated: false, isLoading: false })
  }, [])

  const setEntityId = useCallback((id: string) => {
    localStorage.setItem('entity_id', id)
    setState((s) => ({ ...s, entityId: id }))
  }, [])

  return (
    <AuthContext.Provider value={{ ...state, login, logout, setEntityId }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider')
  return ctx
}
