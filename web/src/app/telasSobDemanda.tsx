/** Telas pesadas (graficos, simulador, administracao) carregadas so quando abertas. */

import { lazy, Suspense, type ComponentType } from 'react'
import { Carregando } from '@pampufa/ui'

function sobDemanda(carregar: () => Promise<{ default: ComponentType }>) {
  const Tela = lazy(carregar)
  return function TelaSobDemanda() {
    return (
      <Suspense fallback={<Carregando texto="…" />}>
        <Tela />
      </Suspense>
    )
  }
}

export const TelaMetricas = sobDemanda(() => import('../features/metricas/TelaMetricas').then((m) => ({ default: m.TelaMetricas })))
export const TelaSimulador = sobDemanda(() => import('../features/simulador/TelaSimulador').then((m) => ({ default: m.TelaSimulador })))
export const TelaAdmin = sobDemanda(() => import('../features/admin/TelaAdmin').then((m) => ({ default: m.TelaAdmin })))
