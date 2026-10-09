'use client'

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react'
import { INITIAL_DEMO_TRIP } from './travelV2Data'
import { applyDemoTripEdit, undoDemoTripEdit, type DemoTripEdit, type DemoTripState } from './travelV2State'

interface DemoTripContextValue {
  state: DemoTripState
  edit: (action: DemoTripEdit) => void
  undo: () => void
}

const DemoTripContext = createContext<DemoTripContextValue | null>(null)

export function DemoTripProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<DemoTripState>(INITIAL_DEMO_TRIP)
  const edit = useCallback((action: DemoTripEdit) => {
    setState((current) => applyDemoTripEdit(current, action))
  }, [])
  const undo = useCallback(() => setState((current) => undoDemoTripEdit(current)), [])
  const value = useMemo(() => ({ state, edit, undo }), [state, edit, undo])
  return <DemoTripContext.Provider value={value}>{children}</DemoTripContext.Provider>
}

export function useDemoTrip(): DemoTripContextValue {
  const value = useContext(DemoTripContext)
  if (!value) throw new Error('useDemoTrip 必须在 DemoTripProvider 中使用')
  return value
}
