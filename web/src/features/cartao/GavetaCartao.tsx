/**
 * O cartao na gaveta lateral: detalhes (edicao no lugar), tarefas, historico, bloqueios e
 * comentarios, mais mover, abrir envelope e arquivar. Cartao de providencia mostra onde
 * resolver; arquivar descarta a providencia (decisao de 16/10/2026).
 */

import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Aviso, Carregando, ErroDeApi, Tag } from '@pampufa/ui'

import { useNagare } from '../../app/contexto'
import { chaves } from '../../api/nagare'
import type { Cartao, CartaoDetalhe, Coluna, Quadro, SituacaoWip } from '../../api/tipos'
import { Gaveta, Janela } from '../../componentes/Sobreposicao'
import { useT } from '../../i18n'
import { destinosAFrente, providenciaDo, validarDestino } from '../quadro/regras'
import { AbaBloqueios } from './AbaBloqueios'
import { useAcaoNoCartao, useCartao } from './acoes'
import { AbaComentarios } from './AbaComentarios'
import { AbaDetalhes } from './AbaDetalhes'
import { AbaHistorico } from './AbaHistorico'
import { AbaTarefas } from './AbaTarefas'

type Aba = 'detalhes' | 'tarefas' | 'historico' | 'bloqueios' | 'comentarios'

export function GavetaCartao({
  cartaoId,
  quadro,
  colunas,
  wip,
  podeEditar,
  fechar,
  mover,
}: {
  cartaoId: string
  quadro: Quadro
  colunas: Coluna[]
  wip: SituacaoWip
  podeEditar: boolean
  fechar: () => void
  mover: (cartao: Cartao, destino: string) => void
}) {
  const { t, eu, dataHora } = useT()
  const cartao = useCartao(cartaoId)
  const [aba, setAba] = useState<Aba>('detalhes')
  const [menu, setMenu] = useState(false)
  const [arquivar, setArquivar] = useState(false)

  if (cartao.isPending) {
    return (
      <Gaveta titulo={t('comum.carregando')} fechar={fechar}>
        <Carregando texto={t('comum.carregando')} />
      </Gaveta>
    )
  }
  if (cartao.isError) {
    return (
      <Gaveta titulo={t('cartao.titulo_erro')} fechar={fechar}>
        <ErroDeApi erro={cartao.error} />
      </Gaveta>
    )
  }
  const c = cartao.data
  const lacrado = c.envelope_state === 'sealed'
  const providencia = providenciaDo(c)
  const editavel = podeEditar && !c.archived
  const abas: Aba[] = [
    'detalhes',
    'tarefas',
    ...(eu?.flags.card_history !== false ? (['historico'] as const) : []),
    'bloqueios',
    'comentarios',
  ]
  const bloqueio = c.blockers.find((b) => !b.ended_at)

  return (
    <Gaveta titulo={`${c.key} · ${c.title}`} fechar={fechar}>
      <header className="ng-gaveta-topo">
        <div className="ng-gaveta-chips">
          <span className="ng-chave">{c.key}</span>
          {eu?.flags.service_classes !== false && <span className={`ng-classe classe-${c.service_class}`}>{t(`classe.${c.service_class}`)}</span>}
          {c.project_key && <Tag>{c.project_key}</Tag>}
          {c.column_name && <Tag cor="azul">{c.column_name}</Tag>}
          {lacrado && <Tag cor="ouro">{t('cartao.lacrado')}</Tag>}
          {bloqueio && <Tag cor="vermelho">{t('cartao.bloqueado')}</Tag>}
          {c.archived && <Tag>{t('cartao.arquivado')}</Tag>}
        </div>
        <h2 className="ng-gaveta-titulo">{lacrado ? t('cartao.lacrado') : c.title}</h2>
        <p className="mini">
          {t('cartao.criado_em', { quando: dataHora(c.created_at) })}
          {c.committed_at && ` · ${t('cartao.compromissado_em', { quando: dataHora(c.committed_at) })}`}
          {c.delivered_at && ` · ${t('cartao.entregue_em', { quando: dataHora(c.delivered_at) })}`}
        </p>
        {editavel && (
          <div className="acoes">
            {!lacrado && !c.delivered_at && (
              <button type="button" className="btn sm" onClick={() => setMenu(true)}>
                {t('cartao.mover')}
              </button>
            )}
            {lacrado && eu?.flags.envelope !== false && <AbrirEnvelope cartao={c} />}
            <button type="button" className="btn sm ghost" onClick={() => setArquivar(true)}>
              {t('cartao.arquivar')}
            </button>
          </div>
        )}
      </header>

      {providencia && (
        <Aviso tipo={providencia.resolvidaNaOrigem ? 'alerta' : 'info'}>
          <b>{t('cartao.providencia_titulo')}</b>{' '}
          {providencia.resolvidaNaOrigem ? t('cartao.providencia_resolvida') : t('cartao.providencia_texto')}
          {providencia.url && (
            <>
              {' '}
              <a href={providencia.url}>{t('cartao.resolver_em')}</a>
            </>
          )}
        </Aviso>
      )}

      <div className="abas" role="tablist" aria-label={t('cartao.abas')}>
        {abas.map((nome) => (
          <button key={nome} type="button" role="tab" aria-selected={aba === nome} onClick={() => setAba(nome)}>
            {t(`cartao.aba.${nome}`)}
            {nome === 'tarefas' && c.tasks.length > 0 && ` (${c.tasks.filter((x) => x.is_done).length}/${c.tasks.length})`}
            {nome === 'comentarios' && c.comments.length > 0 && ` (${c.comments.length})`}
          </button>
        ))}
      </div>
      <div className="ng-gaveta-aba" role="tabpanel">
        {aba === 'detalhes' && <AbaDetalhes cartao={c} quadro={quadro} editavel={editavel && !lacrado} />}
        {aba === 'tarefas' && <AbaTarefas cartao={c} editavel={editavel} />}
        {aba === 'historico' && <AbaHistorico cartao={c} colunas={colunas} />}
        {aba === 'bloqueios' && <AbaBloqueios cartao={c} editavel={editavel} />}
        {aba === 'comentarios' && <AbaComentarios cartao={c} podeComentar={Boolean(eu?.capabilities.manage_cards)} />}
      </div>

      {menu && (
        <Janela titulo={t('mover.titulo', { chave: c.key })} fechar={() => setMenu(false)} largura={420}>
          <p className="mini">{t('mover.so_para_frente')}</p>
          <ul className="ng-lista-destinos">
            {destinosAFrente(c, colunas).map((coluna) => {
              const veredito = validarDestino(c, coluna.id, colunas, wip)
              return (
                <li key={coluna.id}>
                  <button
                    type="button"
                    className={`ng-destino ${veredito.estado}`}
                    disabled={veredito.estado === 'invalido'}
                    onClick={() => {
                      setMenu(false)
                      mover(c, coluna.id)
                    }}
                  >
                    <b>{coluna.name}</b>
                    {veredito.motivo && <span className="mini"> — {t(veredito.motivo)}</span>}
                  </button>
                </li>
              )
            })}
          </ul>
        </Janela>
      )}
      {arquivar && <ConfirmarArquivar cartao={c} comProvidencia={Boolean(providencia)} fechar={() => setArquivar(false)} feito={fechar} />}
    </Gaveta>
  )
}

function AbrirEnvelope({ cartao }: { cartao: CartaoDetalhe }) {
  const { t } = useT()
  const queryClient = useQueryClient()
  const produto = useNagare().produto
  const acao = useAcaoNoCartao(cartao, 'cartao.envelope_aberto')
  return (
    <button
      type="button"
      className="btn sm"
      disabled={acao.isPending}
      onClick={() =>
        acao.mutate(
          { caminho: `/cards/${cartao.id}/open-envelope`, metodo: 'POST' },
          { onSuccess: () => void queryClient.invalidateQueries({ queryKey: chaves.cartao(produto, cartao.id) }) },
        )
      }
    >
      {t('cartao.abrir_envelope')}
    </button>
  )
}

function ConfirmarArquivar({
  cartao,
  comProvidencia,
  fechar,
  feito,
}: {
  cartao: CartaoDetalhe
  comProvidencia: boolean
  fechar: () => void
  feito: () => void
}) {
  const { t } = useT()
  const acao = useAcaoNoCartao(cartao, 'cartao.arquivado_ok')
  return (
    <Janela
      titulo={t('cartao.arquivar_titulo', { chave: cartao.key })}
      fechar={fechar}
      largura={460}
      rodape={
        <>
          <button
            className="btn"
            type="button"
            disabled={acao.isPending}
            onClick={() => acao.mutate({ caminho: `/cards/${cartao.id}`, metodo: 'DELETE' }, { onSuccess: feito })}
          >
            {t('cartao.arquivar')}
          </button>
          <button className="btn ghost" type="button" autoFocus onClick={fechar}>
            {t('comum.cancelar')}
          </button>
        </>
      }
    >
      <p>{t('cartao.arquivar_texto')}</p>
      {comProvidencia && <Aviso tipo="alerta">{t('cartao.arquivar_providencia')}</Aviso>}
    </Janela>
  )
}
