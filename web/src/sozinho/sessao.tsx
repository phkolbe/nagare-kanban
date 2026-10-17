/**
 * Sessao do Nagare fora da plataforma: quem esta logado (`GET /auth/me`), entrar e sair.
 * Sem token valido, a tela de entrada; ao entrar, volta para onde a pessoa estava.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'

import { guardarToken, quandoPerderSessaoLocal, requisitarLocal, tokenLocal } from '../api/sessaoLocal'
import type { Eu } from '../api/tipos'

type Estado = { situacao: 'carregando' } | { situacao: 'anonimo' } | { situacao: 'autenticado'; eu: Eu }

interface ContextoSessao {
  estado: Estado
  entrar: (email: string, senha: string) => Promise<void>
  sair: () => void
}

const Contexto = createContext<ContextoSessao | null>(null)

export const CHAVE_VOLTA = 'nagare.volta'

export function ProvedorSessao({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const [estado, setEstado] = useState<Estado>(() => (tokenLocal() ? { situacao: 'carregando' } : { situacao: 'anonimo' }))

  const anonimo = useCallback(() => {
    guardarToken(null)
    queryClient.clear()
    try {
      const aqui = `${window.location.pathname}${window.location.search}`
      if (!aqui.endsWith('/entrar')) window.sessionStorage.setItem(CHAVE_VOLTA, aqui)
    } catch {
      /* sem armazenamento */
    }
    setEstado({ situacao: 'anonimo' })
  }, [queryClient])

  useEffect(() => {
    quandoPerderSessaoLocal(anonimo)
    if (!tokenLocal()) return
    requisitarLocal<Eu>('/auth/me')
      .then((eu) => setEstado({ situacao: 'autenticado', eu }))
      .catch(anonimo)
  }, [anonimo])

  const entrar = useCallback(async (email: string, senha: string) => {
    const resposta = await requisitarLocal<{ access_token: string }>('/auth/login', {
      metodo: 'POST',
      corpo: { email, password: senha },
    })
    guardarToken(resposta.access_token)
    const eu = await requisitarLocal<Eu>('/auth/me')
    setEstado({ situacao: 'autenticado', eu })
  }, [])

  const sair = useCallback(() => {
    guardarToken(null)
    queryClient.clear()
    setEstado({ situacao: 'anonimo' })
  }, [queryClient])

  const valor = useMemo(() => ({ estado, entrar, sair }), [estado, entrar, sair])
  return <Contexto.Provider value={valor}>{children}</Contexto.Provider>
}

export function useSessao(): ContextoSessao {
  const contexto = useContext(Contexto)
  if (!contexto) throw new Error('useSessao fora do ProvedorSessao')
  return contexto
}
