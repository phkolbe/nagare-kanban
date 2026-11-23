/**
 * O quadro se atualiza sozinho (23/11/2026). A tela pergunta ao servidor a cada ~10 s, com a
 * aba a vista, so "mudou algo neste quadro?" (`GET /boards/{id}/version`, uma resposta
 * pequena) e, se mudou, recarrega calada o quadro, o cartao aberto e as metricas. Ao voltar
 * para a aba, confere na hora. Durante um arrasto, espera soltar: recarregar no meio tiraria
 * o cartao da mao. As regras sao funcoes puras, chamadas por `checks/telas.check.ts`.
 */

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'

import { useNagare } from '../../app/contexto'
import { chaves, nagare } from '../../api/nagare'

/** De quanto em quanto tempo a tela confere a versao do quadro. */
export const INTERVALO_DE_CONFERENCIA_MS = 10_000

export interface VersaoDoQuadro {
  version: string
  checked_at: string
}

/** So recarrega quando ja havia uma versao e ela mudou (a primeira resposta so guarda). */
export function deveRecarregar(anterior: string | null | undefined, atual: string | null | undefined): boolean {
  return Boolean(anterior && atual && anterior !== atual)
}

/** Ha quantos segundos foi a ultima conferencia, arredondado para baixo em passos de 5 s. */
export function segundosDesde(conferidoEm: number, agora: number): number {
  const segundos = Math.max(0, Math.floor((agora - conferidoEm) / 1000))
  return segundos < 5 ? 0 : segundos - (segundos % 5)
}

// ------------------------------------------------------------------ arrasto em andamento

let arrastando = false
let pendente: (() => void) | null = null

/** A grade avisa o inicio e o fim do arrasto; a recarga que chegou no meio sai ao soltar. */
export function marcarArrasto(ligado: boolean): void {
  arrastando = ligado
  if (!ligado && pendente) {
    const recarregar = pendente
    pendente = null
    recarregar()
  }
}

/** Recarrega agora, ou ao soltar se houver um arrasto em andamento (a ultima pedida vale). */
export function quandoPuder(recarregar: () => void): void {
  if (arrastando) pendente = recarregar
  else recarregar()
}

// ------------------------------------------------------------------ o hook

/**
 * Confere a versao do quadro e recarrega o que mudou. Devolve quando foi a ultima conferencia
 * que deu certo (para o "Atualizado ha 5 s"), ou `null` antes da primeira.
 */
export function useAtualizacaoAutomatica(quadroId: string | null | undefined): number | null {
  const produto = useNagare().produto
  const queryClient = useQueryClient()
  const versao = useQuery({
    queryKey: chaves.versao(produto, quadroId ?? ''),
    queryFn: () => nagare<VersaoDoQuadro>(produto, `/boards/${encodeURIComponent(quadroId!)}/version`),
    enabled: Boolean(quadroId),
    staleTime: 0,
    refetchInterval: INTERVALO_DE_CONFERENCIA_MS,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: 'always',
    retry: false,
  })
  const ultima = useRef<string | null>(null)

  useEffect(() => {
    ultima.current = null
  }, [quadroId])

  useEffect(() => {
    const atual = versao.data?.version
    if (!atual || !quadroId) return
    if (deveRecarregar(ultima.current, atual)) {
      quandoPuder(() => {
        void queryClient.invalidateQueries({ queryKey: chaves.visoes(produto, quadroId) })
        void queryClient.invalidateQueries({ queryKey: chaves.quadro(produto, quadroId) })
        void queryClient.invalidateQueries({ queryKey: chaves.cartoes(produto) })
        void queryClient.invalidateQueries({ queryKey: chaves.metricas(produto, quadroId) })
        void queryClient.invalidateQueries({ queryKey: ['nagare', produto, 'previsao', quadroId] })
      })
    }
    ultima.current = atual
  }, [versao.data?.version, quadroId, produto, queryClient])

  return versao.isSuccess ? versao.dataUpdatedAt : null
}

/** Os segundos desde a ultima conferencia, atualizados a cada 5 s na tela. */
export function useSegundosDesde(conferidoEm: number | null): number | null {
  const [agora, setAgora] = useState(() => Date.now())
  useEffect(() => {
    const relogio = window.setInterval(() => setAgora(Date.now()), 5_000)
    return () => window.clearInterval(relogio)
  }, [])
  return conferidoEm === null ? null : segundosDesde(conferidoEm, Math.max(agora, conferidoEm))
}
