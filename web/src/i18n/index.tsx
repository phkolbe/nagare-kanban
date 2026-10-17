/**
 * Internacionalizacao das telas do Nagare (decisao de 17/10/2026): portugues e ingles.
 *
 * A lingua e da pessoa: o Nagare guarda a escolha (`PATCH /auth/me`) e, sem escolha, usa o
 * padrao do acesso (portugues pela Pampufa, ingles no login local). O menu e o sino sao do
 * `@pampufa/ui` e seguem em portugues; o que e do Nagare segue a lingua escolhida.
 */

import { createContext, useCallback, useContext, useMemo, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ErroApi, useProduto } from '@pampufa/ui'

import { chaves, nagare } from '../api/nagare'
import type { Eu, Idioma } from '../api/tipos'
import { chaveDoErro, traducao, type Traducao } from './texto'

export type { Chave } from './mensagens'
export { interpolar, traducao, type Traducao } from './texto'

interface ContextoIdioma extends Traducao {
  eu: Eu | null
  escolher: (idioma: Idioma | null) => void
}

const Contexto = createContext<ContextoIdioma | null>(null)

/** O Nagare desta pessoa neste produto: papel, capacidades, flags e a lingua. */
export function useEuNagare() {
  const produto = useProduto().id
  return useQuery({
    queryKey: chaves.eu(produto),
    queryFn: () => nagare<Eu>(produto, '/auth/me'),
    staleTime: 60_000,
  })
}

export function ProvedorIdioma({ children }: { children: ReactNode }) {
  const produto = useProduto().id
  const queryClient = useQueryClient()
  const eu = useEuNagare()
  const idioma: Idioma = eu.data?.locale ?? 'pt-BR'
  const escolha = useMutation({
    mutationFn: (novo: Idioma | null) => nagare<Eu>(produto, '/auth/me', { metodo: 'PATCH', corpo: { locale: novo } }),
    onSuccess: (atualizado) => {
      // A escolha vale em todo produto: o cache de cada um e refeito.
      void queryClient.invalidateQueries({ queryKey: ['nagare'] })
      queryClient.setQueryData(chaves.eu(produto), atualizado)
    },
  })
  const escolher = useCallback((novo: Idioma | null) => escolha.mutate(novo), [escolha])
  const valor = useMemo(
    () => ({ ...traducao(idioma), eu: eu.data ?? null, escolher }),
    [idioma, eu.data, escolher],
  )
  return <Contexto.Provider value={valor}>{children}</Contexto.Provider>
}

export function useT(): ContextoIdioma {
  const contexto = useContext(Contexto)
  if (!contexto) throw new Error('useT fora do ProvedorIdioma')
  return contexto
}

/**
 * A mensagem de um erro de API na lingua da tela, pelo codigo (`flow_locked`, `wip_exceeded`...).
 * Codigo sem traducao: a mensagem do servidor (em ingles, do Nagare) ou a generica.
 */
export function mensagemDeErro(erro: unknown, t: Traducao['t']): string {
  if (erro instanceof ErroApi) {
    const chave = chaveDoErro(erro.codigo, erro.status)
    return chave ? t(chave) : erro.message || t('erro.generico')
  }
  return t('erro.rede')
}
