/**
 * Sessao do Nagare fora da plataforma: quem esta logado (`GET /auth/me`), entrar e sair.
 * Sem token valido, a tela de entrada; ao entrar, volta para onde a pessoa estava. A sessao que
 * acaba (token vencido) leva a entrada sozinha, sem pagina de erro (24/11/2026).
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'

import { BASE_SOZINHO } from '../app/contexto'
import { guardarToken, quandoPerderSessaoLocal, requisitarLocal, tokenLocal, tokenVencido, vencimentoDoToken } from '../api/sessaoLocal'
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
  const [estado, setEstado] = useState<Estado>(() => {
    // Token vencido guardado de outro dia: direto para a entrada, sem pedir nada ao servidor.
    if (tokenVencido(tokenLocal())) guardarToken(null)
    return tokenLocal() ? { situacao: 'carregando' } : { situacao: 'anonimo' }
  })

  const anonimo = useCallback(() => {
    guardarToken(null)
    queryClient.clear()
    try {
      // O caminho para o roteador, sem a base `/nagare` (ele a poe de volta).
      const caminho = window.location.pathname.slice(BASE_SOZINHO.length) || '/'
      const aqui = `${caminho}${window.location.search}`
      if (!caminho.endsWith('/sign-in')) window.sessionStorage.setItem(CHAVE_VOLTA, aqui)
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

  // Quando a sessao acaba (o token vence), a tela vai sozinha para a entrada, mesmo parada; ao
  // voltar para a aba, confere na hora (24/11/2026). Ao entrar de novo, volta para onde estava.
  const autenticado = estado.situacao === 'autenticado'
  useEffect(() => {
    if (!autenticado) return
    const vencimento = vencimentoDoToken(tokenLocal())
    if (vencimento === null) return
    const conferir = () => {
      if (tokenVencido(tokenLocal())) anonimo()
    }
    // O setTimeout aceita no maximo ~24,8 dias; o token vive horas.
    const relogio = window.setTimeout(conferir, Math.min(Math.max(0, vencimento - Date.now()) + 500, 2_000_000_000))
    document.addEventListener('visibilitychange', conferir)
    window.addEventListener('focus', conferir)
    return () => {
      window.clearTimeout(relogio)
      document.removeEventListener('visibilitychange', conferir)
      window.removeEventListener('focus', conferir)
    }
  }, [autenticado, anonimo])

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
