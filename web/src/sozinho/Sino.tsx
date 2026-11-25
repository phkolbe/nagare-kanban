/**
 * O sino do Nagare sozinho (24/11/2026, decisao R23 do plano de Reports): os avisos da pessoa,
 * com o mesmo icone e o mesmo jeito do sino da Pampufa (classes `sino`, `contador`,
 * `sino-lista`). Geral: o primeiro tipo de aviso (relatorio agendado emitido) chega na onda 3;
 * ate la a lista fica vazia. Cada novo tipo entra por pedido.
 */

import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Icone } from '@pampufa/ui'

import { PRODUTO_SOZINHO } from '../app/contexto'
import { nagare } from '../api/nagare'
import { useT } from '../i18n'

export interface AvisoDoSino {
  id: string
  product_id: string
  kind: string
  title: string
  body: string | null
  link: string | null
  created_at: string
  read_at: string | null
}

export const CHAVE_SINO = ['nagare', 'sino'] as const

/** Os avisos da pessoa; o Shell tambem le daqui o contador do item Reports. */
export function useAvisosDoSino() {
  return useQuery({
    queryKey: CHAVE_SINO,
    queryFn: () => nagare<{ items: AvisoDoSino[]; unread: number }>(PRODUTO_SOZINHO, '/notifications'),
    staleTime: 60_000,
    refetchInterval: 120_000,
  })
}

/** Quantos avisos de relatorio ainda nao foram lidos (o contador do item Reports do menu). */
export function relatoriosNaoLidos(itens: Pick<AvisoDoSino, 'kind' | 'read_at'>[]): number {
  return itens.filter((a) => a.kind.startsWith('report') && !a.read_at).length
}

export function Sino() {
  const { t, dataHora } = useT()
  const queryClient = useQueryClient()
  const avisos = useAvisosDoSino()
  const [aberto, setAberto] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!aberto) return
    const clique = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setAberto(false)
    document.addEventListener('mousedown', clique)
    return () => document.removeEventListener('mousedown', clique)
  }, [aberto])

  const atualizar = () => queryClient.invalidateQueries({ queryKey: CHAVE_SINO })
  const lerUm = useMutation({
    mutationFn: (id: string) => nagare(PRODUTO_SOZINHO, `/notifications/${encodeURIComponent(id)}/read`, { metodo: 'POST' }),
    onSettled: atualizar,
  })
  const lerTodos = useMutation({
    mutationFn: () => nagare(PRODUTO_SOZINHO, '/notifications/read-all', { metodo: 'POST' }),
    onSettled: atualizar,
  })

  const naoLidos = avisos.data?.unread ?? 0
  const itens = avisos.data?.items ?? []
  return (
    <div className="ng-sino" ref={ref}>
      <button
        type="button"
        className="sino"
        aria-label={t('sino.rotulo', { quantos: naoLidos })}
        title={t('sino.titulo')}
        aria-expanded={aberto}
        onClick={() => setAberto(!aberto)}
      >
        <Icone nome="sino" />
        {naoLidos > 0 && <span className="contador">{naoLidos > 99 ? '99+' : naoLidos}</span>}
      </button>
      {aberto && (
        <div className="popover sino-lista">
          <div className="popover-titulo">{t('sino.titulo')}</div>
          {itens.length === 0 ? (
            <p className="mini" style={{ padding: '6px 14px 12px' }}>
              {t('sino.vazio')}
            </p>
          ) : (
            <ul>
              {itens.slice(0, 10).map((a) => (
                <li key={a.id} className={a.read_at ? 'lido' : undefined}>
                  <div>
                    {a.link ? (
                      <Link
                        to={a.link}
                        onClick={() => {
                          if (!a.read_at) lerUm.mutate(a.id)
                          setAberto(false)
                        }}
                      >
                        {a.title}
                      </Link>
                    ) : (
                      <span>{a.title}</span>
                    )}
                    <div className="mini">
                      {a.body ? `${a.body} · ` : ''}
                      {dataHora(a.created_at)}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}
          {naoLidos > 0 && (
            <div className="popover-pe">
              <button type="button" className="btn sm ghost" onClick={() => lerTodos.mutate()}>
                {t('sino.marcar_todos')}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
