/**
 * Tipos de cartao do produto (PPLAT-312, 12/11/2026), na tela de Regras de tarefa: quem gere
 * quadros no produto (administrador de Tarefas) acrescenta, renomeia, ordena e arquiva; os outros
 * so veem. Um tipo que algum cartao ja tem e arquivado, nao excluido (o servidor recusa).
 */

import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Aviso, Carregando, ErroDeApi, Tag } from '@pampufa/ui'

import { useNagare } from '../../app/contexto'
import { chaves, nagare } from '../../api/nagare'
import type { TipoDeCartao, TiposDeCartao as Lista } from '../../api/tipos'
import { mensagemDeErro, useT } from '../../i18n'
import { moverTipo, rotuloDoTipo } from '../cartao/tiposDeCartao'
import { useTiposDeCartao } from '../quadro/consultas'

type Pedido = { caminho: string; metodo: 'POST' | 'PATCH' | 'DELETE'; corpo?: unknown }

export function TiposDeCartao() {
  const { t } = useT()
  const { produto } = useNagare()
  const queryClient = useQueryClient()
  const tipos = useTiposDeCartao()
  const [novo, setNovo] = useState('')
  const [renomeando, setRenomeando] = useState<{ chave: string; nome: string } | null>(null)
  const pedir = useMutation({
    mutationFn: ({ caminho, metodo, corpo }: Pedido) => nagare<Lista>(produto, caminho, { metodo, corpo }),
    onSuccess: (lista) => {
      queryClient.setQueryData(chaves.tipos(produto), lista)
      setRenomeando(null)
    },
  })

  if (tipos.isPending) return <Carregando texto={t('comum.carregando')} />
  if (tipos.isError) return <ErroDeApi erro={tipos.error} />
  const { items, can_edit: pode } = tipos.data
  const ordem = [...items].sort((a, b) => a.position - b.position)
  const chaves_ = ordem.map((x) => x.key)
  const nome = (x: TipoDeCartao) => rotuloDoTipo(x.key, items, t)

  return (
    <section className="card ng-tipos">
      <h2>{t('tipos.titulo')}</h2>
      <p className="mini">{t('tipos.descricao')}</p>
      {ordem.some((x) => x.builtin) && <p className="mini">{t('tipos.padrao')}</p>}
      {!pode && <p className="mini">{t('tipos.so_leitura')}</p>}
      <ol className="ng-lista-tipos">
        {ordem.map((x, i) => (
          <li key={x.key} className={x.archived ? 'arquivado' : undefined}>
            {renomeando?.chave === x.key ? (
              <form
                className="ng-par"
                onSubmit={(e) => {
                  e.preventDefault()
                  pedir.mutate({ caminho: `/card-types/${x.key}`, metodo: 'PATCH', corpo: { name: renomeando.nome.trim() || null } })
                }}
              >
                <input
                  aria-label={t('tipos.nome')}
                  value={renomeando.nome}
                  maxLength={80}
                  autoFocus
                  onChange={(e) => setRenomeando({ chave: x.key, nome: e.target.value })}
                />
                <button className="btn sm" type="submit" disabled={pedir.isPending}>
                  {t('comum.salvar')}
                </button>
                <button className="btn sm ghost" type="button" onClick={() => setRenomeando(null)}>
                  {t('comum.cancelar')}
                </button>
              </form>
            ) : (
              <>
                <span>{nome(x)}</span>
                {x.archived && (
                  <>
                    {' '}
                    <Tag>{t('tipos.arquivado')}</Tag>
                  </>
                )}
                {pode && (
                  <span className="ng-acoes-tipo">
                    <button
                      type="button"
                      className="btn sm ghost"
                      aria-label={t('tipos.subir', { nome: nome(x) })}
                      title={t('tipos.subir', { nome: nome(x) })}
                      disabled={i === 0 || pedir.isPending}
                      onClick={() => pedir.mutate({ caminho: '/card-types/order', metodo: 'POST', corpo: { keys: moverTipo(chaves_, x.key, -1) } })}
                    >
                      ↑
                    </button>
                    <button
                      type="button"
                      className="btn sm ghost"
                      aria-label={t('tipos.descer', { nome: nome(x) })}
                      title={t('tipos.descer', { nome: nome(x) })}
                      disabled={i === ordem.length - 1 || pedir.isPending}
                      onClick={() => pedir.mutate({ caminho: '/card-types/order', metodo: 'POST', corpo: { keys: moverTipo(chaves_, x.key, 1) } })}
                    >
                      ↓
                    </button>
                    <button type="button" className="link" onClick={() => setRenomeando({ chave: x.key, nome: x.name ?? nome(x) })}>
                      {t('tipos.renomear')}
                    </button>
                    <button
                      type="button"
                      className="link"
                      disabled={pedir.isPending}
                      onClick={() => pedir.mutate({ caminho: `/card-types/${x.key}`, metodo: 'PATCH', corpo: { archived: !x.archived } })}
                    >
                      {x.archived ? t('tipos.reativar') : t('tipos.arquivar')}
                    </button>
                    <button
                      type="button"
                      className="link"
                      disabled={pedir.isPending}
                      onClick={() => pedir.mutate({ caminho: `/card-types/${x.key}`, metodo: 'DELETE' })}
                    >
                      {t('tipos.excluir')}
                    </button>
                  </span>
                )}
              </>
            )}
          </li>
        ))}
      </ol>
      {pode && (
        <form
          className="ng-par"
          onSubmit={(e) => {
            e.preventDefault()
            if (!novo.trim()) return
            pedir.mutate(
              { caminho: '/card-types', metodo: 'POST', corpo: { name: novo.trim() } },
              { onSuccess: () => setNovo('') },
            )
          }}
        >
          <input placeholder={t('tipos.novo')} aria-label={t('tipos.novo')} value={novo} maxLength={80} onChange={(e) => setNovo(e.target.value)} />
          <button className="btn sm sec" type="submit" disabled={!novo.trim() || pedir.isPending}>
            {t('tipos.adicionar')}
          </button>
        </form>
      )}
      {pedir.isError && <Aviso tipo="erro">{mensagemDeErro(pedir.error, t)}</Aviso>}
    </section>
  )
}
