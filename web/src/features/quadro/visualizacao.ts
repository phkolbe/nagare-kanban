/**
 * Como a pessoa ve o quadro (decisao de 18/10/2026): o formato das ferramentas (barra recolhivel
 * ou painel flutuante) e da pessoa, guardado no usuario do Nagare (`preferences.board_toolbar`,
 * vale em qualquer aparelho); a barra aberta ou fechada e os cartoes compactos ficam neste
 * navegador; a tela cheia esconde tudo acima do minimapa (tecla `f`, `Esc` volta).
 */

import { useCallback, useEffect, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import { useNagare } from '../../app/contexto'
import { chaves, nagare } from '../../api/nagare'
import type { Eu, FormatoFerramentas } from '../../api/tipos'
import { useT } from '../../i18n'

export function useFormatoFerramentas(): [FormatoFerramentas, (novo: FormatoFerramentas) => void] {
  const { eu } = useT()
  const { produto } = useNagare()
  const queryClient = useQueryClient()
  const formato = eu?.preferences?.board_toolbar ?? 'bar'
  const gravar = useMutation({
    mutationFn: (novo: FormatoFerramentas) =>
      nagare<Eu>(produto, '/auth/me', { metodo: 'PATCH', corpo: { preferences: { board_toolbar: novo } } }),
    onMutate: (novo) => {
      // Troca na hora; a resposta confirma (ou desfaz, se falhar).
      const antes = queryClient.getQueryData<Eu>(chaves.eu(produto))
      if (antes) queryClient.setQueryData(chaves.eu(produto), { ...antes, preferences: { ...antes.preferences, board_toolbar: novo } })
      return { antes }
    },
    onError: (_erro, _novo, contexto) => {
      if (contexto?.antes) queryClient.setQueryData(chaves.eu(produto), contexto.antes)
    },
    onSuccess: (atualizado) => {
      queryClient.setQueryData(chaves.eu(produto), atualizado)
      // A preferencia e da pessoa: vale nos outros produtos tambem.
      void queryClient.invalidateQueries({ predicate: (q) => q.queryKey[2] === 'eu' && q.queryKey[1] !== produto })
    },
  })
  return [formato, (novo) => gravar.mutate(novo)]
}

/** Um liga/desliga lembrado neste navegador. */
export function useLigadoNoNavegador(chave: string, padrao: boolean): [boolean, (valor: boolean) => void] {
  const [valor, setValor] = useState(() => {
    try {
      const guardado = window.localStorage.getItem(chave)
      return guardado === null ? padrao : guardado === '1'
    } catch {
      return padrao
    }
  })
  const trocar = useCallback(
    (novo: boolean) => {
      setValor(novo)
      try {
        window.localStorage.setItem(chave, novo ? '1' : '0')
      } catch {
        /* sem armazenamento: vale nesta pagina */
      }
    },
    [chave],
  )
  return [valor, trocar]
}

function digitando(alvo: EventTarget | null): boolean {
  const el = alvo as HTMLElement | null
  return Boolean(el && (el.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName)))
}

/**
 * Atalhos de tecla da visualizacao, fora de campos: `f` alterna a tela cheia, `v` as
 * ferramentas, `Esc` sai da tela cheia (sem fechar uma janela aberta por cima).
 */
export function useTeclasDaVisualizacao({ alternarTelaCheia, sairDaTelaCheia, alternarFerramentas }: {
  alternarTelaCheia: () => void
  sairDaTelaCheia: () => void
  alternarFerramentas: () => void
}) {
  useEffect(() => {
    const tecla = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey || digitando(e.target)) return
      if (e.key === 'f') {
        e.preventDefault()
        alternarTelaCheia()
      } else if (e.key === 'v') {
        e.preventDefault()
        alternarFerramentas()
      } else if (e.key === 'Escape' && !document.querySelector('[role="dialog"]')) {
        sairDaTelaCheia()
      }
    }
    window.addEventListener('keydown', tecla)
    return () => window.removeEventListener('keydown', tecla)
  }, [alternarTelaCheia, sairDaTelaCheia, alternarFerramentas])
}

/** A tela cheia marca o documento (`html.ng-tela-cheia`); o CSS esconde o resto. */
export function useTelaCheia(): [boolean, (valor: boolean) => void] {
  const [cheia, setCheia] = useState(false)
  useEffect(() => {
    document.documentElement.classList.toggle('ng-tela-cheia', cheia)
    return () => document.documentElement.classList.remove('ng-tela-cheia')
  }, [cheia])
  return [cheia, setCheia]
}
