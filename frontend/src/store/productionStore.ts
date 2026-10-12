import { create } from 'zustand'
import type { Production } from '../studioTypes'
import { reconcileProductionEdit } from '../ui/studio/productionLogic'

type ProductionState = {
  production: Production | null
  setProduction: (production: Production | null) => void
  updateProduction: (patch: Partial<Production> | ((production: Production) => Production)) => void
}

export const useProductionStore = create<ProductionState>((set) => ({
  production: null,
  setProduction: (production) => set({ production }),
  updateProduction: (patch) => set((state) => {
    if (!state.production) return state
    const next = typeof patch === 'function' ? patch(state.production) : { ...state.production, ...patch }
    return { production: reconcileProductionEdit(state.production, next) }
  }),
}))

export const productionStore = useProductionStore
