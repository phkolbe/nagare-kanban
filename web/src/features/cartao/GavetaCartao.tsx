/**
 * O cartao na gaveta lateral: detalhes (edicao no lugar), tarefas, historico, bloqueios e
 * comentarios, mais mover, abrir envelope e arquivar. Cartao de providencia mostra onde
 * resolver; arquivar descarta a providencia (decisao de 16/10/2026).
 *
 * O conteudo (`ConteudoDoCartao`) e o mesmo da pagina propria do cartao (24/11/2026); a gaveta
 * ganha "Abrir em pagina" e "Copiar link", pelo endereco da chave.
 */

import { useState, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Link, useHref, useLocation } from 'react-router-dom'
import { Aviso, Carregando, ErroDeApi, Tag } from '@pampufa/ui'

import { useNagare } from '../../app/contexto'
import { chaves } from '../../api/nagare'
import type { Cartao, CartaoDetalhe, Coluna, Quadro, SituacaoWip } from '../../api/tipos'
import { useAvisos } from '../../componentes/Avisos'
import { Gaveta, Janela } from '../../componentes/Sobreposicao'
import { useT } from '../../i18n'
import { destinosAFrente, providenciaDo, validarDestino } from '../quadro/regras'
import { AbaBloqueios } from './AbaBloqueios'
import { useAcaoNoCartao, useCartao } from './acoes'
import { AbaComentarios } from './AbaComentarios'
import { AbaDetalhes } from './AbaDetalhes'
import { AbaHoras } from './AbaHoras'
import { AbaHistorico } from './AbaHistorico'
import { AbaTarefas } from './AbaTarefas'

type Aba = 'detalhes' | 'tarefas' | 'horas' | 'historico' | 'bloqueios' | 'comentarios'

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
  const { t } = useT()
  const cartao = useCartao(cartaoId)

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
  return (
    <Gaveta titulo={`${c.key} · ${c.title}`} fechar={fechar}>
      <ConteudoDoCartao
        cartao={c}
        quadro={quadro}
        colunas={colunas}
        wip={wip}
        podeEditar={podeEditar}
        mover={mover}
        arquivado={fechar}
        links={<LinksDoCartao cartao={c} />}
      />
    </Gaveta>
  )
}

/**
 * O cartao por inteiro, na gaveta e na pagina propria (24/11/2026): cabecalho com os sinais, as
 * acoes (mover, envelope, arquivar), a providencia e as abas.
 */
export function ConteudoDoCartao({
  cartao: c,
  quadro,
  colunas,
  wip,
  podeEditar,
  mover,
  arquivado,
  links,
  naPagina = false,
}: {
  cartao: CartaoDetalhe
  quadro: Quadro
  colunas: Coluna[]
  wip: SituacaoWip
  podeEditar: boolean
  mover: (cartao: Cartao, destino: string) => void
  /** Depois de arquivar: a gaveta fecha, a pagina volta ao quadro. */
  arquivado: () => void
  /** "Abrir em pagina" e "Copiar link" (na gaveta) ou so o "Copiar link" (na pagina). */
  links?: ReactNode
  naPagina?: boolean
}) {
  const { t, eu, dataHora } = useT()
  const [aba, setAba] = useState<Aba>('detalhes')
  const [menu, setMenu] = useState(false)
  const [arquivar, setArquivar] = useState(false)
  const lacrado = c.envelope_state === 'sealed'
  const providencia = providenciaDo(c)
  const editavel = podeEditar && !c.archived
  const abas: Aba[] = [
    'detalhes',
    'tarefas',
    'horas',
    ...(eu?.flags.card_history !== false ? (['historico'] as const) : []),
    'bloqueios',
    'comentarios',
  ]
  const bloqueio = c.blockers.find((b) => !b.ended_at)

  return (
    <>
      <header className={naPagina ? 'ng-gaveta-topo ng-pagina-cartao-topo' : 'ng-gaveta-topo'}>
        <div className="ng-gaveta-chips">
          <span className="ng-chave">{c.key}</span>
          {eu?.flags.service_classes !== false && <span className={`ng-classe classe-${c.service_class}`}>{t(`classe.${c.service_class}`)}</span>}
          {c.project_key && <Tag>{c.project_key}</Tag>}
          {c.column_name && <Tag cor="azul">{c.column_name}</Tag>}
          {lacrado && <Tag cor="ouro">{t('cartao.lacrado')}</Tag>}
          {bloqueio && <Tag cor="vermelho">{t('cartao.bloqueado')}</Tag>}
          {c.archived && <Tag>{t('cartao.arquivado')}</Tag>}
        </div>
        {naPagina ? (
          <h1 className="ng-gaveta-titulo ng-pagina-cartao-titulo">{lacrado ? t('cartao.lacrado') : c.title}</h1>
        ) : (
          <h2 className="ng-gaveta-titulo">{lacrado ? t('cartao.lacrado') : c.title}</h2>
        )}
        <p className="mini">
          {t('cartao.criado_em', { quando: dataHora(c.created_at) })}
          {c.committed_at && ` · ${t('cartao.compromissado_em', { quando: dataHora(c.committed_at) })}`}
          {c.delivered_at && ` · ${t('cartao.entregue_em', { quando: dataHora(c.delivered_at) })}`}
        </p>
        {(editavel || links) && (
          <div className="acoes">
            {editavel && !lacrado && !c.delivered_at && (
              <button type="button" className="btn sm" onClick={() => setMenu(true)}>
                {t('cartao.mover')}
              </button>
            )}
            {editavel && lacrado && eu?.flags.envelope !== false && <AbrirEnvelope cartao={c} />}
            {editavel && (
              <button type="button" className="btn sm ghost" onClick={() => setArquivar(true)}>
                {t('cartao.arquivar')}
              </button>
            )}
            {links && <span className="ng-espaco" />}
            {links}
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
        {aba === 'horas' && <AbaHoras cartao={c} editavel={editavel} />}
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
      {arquivar && <ConfirmarArquivar cartao={c} comProvidencia={Boolean(providencia)} fechar={() => setArquivar(false)} feito={arquivado} />}
    </>
  )
}

/** Copia um texto: a API da area de transferencia e, sem ela (pagina sem HTTPS), o jeito antigo. */
async function copiar(texto: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(texto)
      return true
    }
  } catch {
    /* tenta o jeito antigo */
  }
  const campo = document.createElement('textarea')
  campo.value = texto
  campo.setAttribute('readonly', '')
  campo.style.position = 'fixed'
  campo.style.opacity = '0'
  document.body.appendChild(campo)
  campo.select()
  try {
    return document.execCommand('copy')
  } catch {
    return false
  } finally {
    campo.remove()
  }
}

/** O endereco do cartao: o caminho do roteador e o completo (com o servidor), para colar num chat. */
export function useEnderecoDoCartao(chave: string): { caminho: string; completo: string } {
  const { rotaDoCartao } = useNagare()
  const caminho = rotaDoCartao(chave)
  const href = useHref(caminho)
  return { caminho, completo: `${window.location.origin}${href}` }
}

export function CopiarLink({ chave }: { chave: string }) {
  const { t } = useT()
  const avisar = useAvisos()
  const { completo } = useEnderecoDoCartao(chave)
  return (
    <button
      type="button"
      className="btn sm sec"
      title={t('cartao.copiar_link_dica')}
      onClick={() =>
        void copiar(completo).then((ok) =>
          avisar(t(ok ? 'cartao.link_copiado' : 'cartao.link_nao_copiado', { url: completo }), ok ? 'ok' : 'info'),
        )
      }
    >
      <span aria-hidden="true">🔗</span> {t('cartao.copiar_link')}
    </button>
  )
}

/** Na gaveta: abrir a pagina do cartao (a volta leva ao quadro como estava) e copiar o link. */
function LinksDoCartao({ cartao }: { cartao: CartaoDetalhe }) {
  const { t } = useT()
  const local = useLocation()
  const { caminho } = useEnderecoDoCartao(cartao.key)
  // A volta da pagina leva ao quadro com os filtros de agora, sem a gaveta aberta.
  const busca = new URLSearchParams(local.search)
  busca.delete('cartao')
  const sufixo = busca.toString()
  const de = `${local.pathname}${sufixo ? `?${sufixo}` : ''}`
  return (
    <>
      <Link className="btn sm sec" to={caminho} state={{ de }} title={t('cartao.abrir_pagina_dica')}>
        <span aria-hidden="true">↗</span> {t('cartao.abrir_pagina')}
      </Link>
      <CopiarLink chave={cartao.key} />
    </>
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
