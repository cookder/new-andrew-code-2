import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { api } from './api'
import type { Metadata } from './types'

const MetaContext = createContext<Metadata | null>(null)

/**
 * Units, enums, the panel grid, and the flag vocabulary all come from the
 * backend rather than being restated here.
 *
 * Spec section 2: "Store units explicitly in the schema or in a single
 * constants module. Never let a unit be implied by context." That constants
 * module is `app/constants.py`; this provider is how the UI reads it, so
 * "apex is in feet" is stated in exactly one place in the whole system.
 */
export function MetaProvider({ children }: { children: ReactNode }) {
  const [meta, setMeta] = useState<Metadata | null>(null)

  useEffect(() => {
    api.metadata().then(setMeta).catch(() => setMeta(null))
  }, [])

  return <MetaContext.Provider value={meta}>{children}</MetaContext.Provider>
}

export function useMeta(): Metadata | null {
  return useContext(MetaContext)
}
