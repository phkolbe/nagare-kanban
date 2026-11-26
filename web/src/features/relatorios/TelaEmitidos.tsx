/**
 * Relatórios emitidos (Reports, onda 2, 25/11/2026; plano no cofre, §4.3):
 *
 * - `/nagare/reports/issued`: o histórico dos emitidos do produto, mais novos primeiro.
 * - `/nagare/reports/issued/<id>`: o emitido congelado, com a faixa "Emitido em … por …",
 *   imprimir, a planilha do emitido e, para quem pode (`share_reports`), os links de cliente:
 *   criar (o endereço aparece uma vez só), ver acessos e revogar.
 *
 * O emitido é o mesmo Documento do relatório ao vivo, guardado inteiro no dia da emissão.
 */

import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Aviso, Cabecalho, Carregando, ErroDeApi, Icone } from '@pampufa/ui'

import { BASE_SOZINHO, useNagare } from '../../app/contexto'
import { consulta, nagare } from '../../api/nagare'
import { baixarLocal } from '../../api/sessaoLocal'
import { copiar } from '../../componentes/copiar'
import { mensagemDeErro, useT, type Chave } from '../../i18n'
import { Documento } from './Documento'
import {
  enderecoDoLink,
  OPCOES_DE_VALIDADE,
  VALIDADE_PADRAO,
  type Emitido,
  type LinkDeCliente,
  type SituacaoDoLink,
} from './emitidos'

const ROTULO_DA_SITUACAO: Record<SituacaoDoLink, Chave> = {
  active: 'emitido.active',
  expired: 'emitido.expired',
  revoked: 'emitido.revoked',
}

function useEmitidos(limite: number) {
  const { produto } = useNagare()
  return useQuery({
    queryKey: ['nagare', produto, 'emitidos', 'lista', limite],
    queryFn: () => nagare<{ items: Emitido[]; total: number }>(produto, `/reports/issued${consulta({ limit: limite })}`),
    staleTime: 30_000,
  })
}

function useEmitido(id: string) {
  const { produto } = useNagare()
  return useQuery({
    queryKey: ['nagare', produto, 'emitidos', id],
    queryFn: () => nagare<Emitido>(produto, `/reports/issued/${encodeURIComponent(id)}`),
  })
}

export function TelaEmitidos() {
  const { emitidoId } = useParams()
  return emitidoId ? <TelaEmitido key={emitidoId} id={emitidoId} /> : <ListaDeEmitidos />
}

/** No catálogo: os últimos emitidos e o caminho para o histórico. */
export function UltimosEmitidos() {
  const { t, dataHora } = useT()
  const { rota } = useNagare()
  const emitidos = useEmitidos(5)
  const itens = emitidos.data?.items ?? []
  return (
    <section className="ng-emitidos-ultimos" aria-label={t('relatorios.ultimos_emitidos')}>
      {itens.length > 0 && (
        <>
          <h2 className="ng-subtitulo">{t('relatorios.ultimos_emitidos')}</h2>
          <ul>
            {itens.map((e) => (
              <li key={e.id}>
                <Link to={rota(`relatorios/emitidos/${e.id}`)}>
                  <b>{e.code}</b> {e.title}
                  {e.subtitle ? ` · ${e.subtitle}` : ''}
                </Link>{' '}
                <span className="mini">
                  {e.period.label} · {dataHora(e.issued_at)}
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
      <Link className="mini" to={rota('relatorios/emitidos')}>
        {t('relatorios.ver_emitidos')} ›
      </Link>
    </section>
  )
}

function ListaDeEmitidos() {
  const { t, dataHora } = useT()
  const { rota } = useNagare()
  const emitidos = useEmitidos(200)
  return (
    <div className="ng-relatorios">
      <Link className="ng-relatorio-voltar mini" to={rota('relatorios')}>
        ‹ {t('relatorios.voltar')}
      </Link>
      <Cabecalho kicker={t('relatorios.titulo')} titulo={t('relatorios.emitidos')} />
      <p className="ng-relatorios-descricao">{t('emitido.descricao')}</p>
      {emitidos.isPending ? (
        <Carregando texto={t('comum.carregando')} />
      ) : emitidos.isError ? (
        <ErroDeApi erro={emitidos.error} />
      ) : emitidos.data.items.length === 0 ? (
        <Aviso tipo="info">{t('emitido.vazio')}</Aviso>
      ) : (
        <div className="ng-rolagem-x">
          <table className="ng-tabela ng-emitidos">
            <thead>
              <tr>
                <th>{t('emitido.col_quando')}</th>
                <th>{t('emitido.col_relatorio')}</th>
                <th>{t('emitido.col_periodo')}</th>
                <th>{t('emitido.col_por')}</th>
                <th className="num">{t('emitido.col_links')}</th>
              </tr>
            </thead>
            <tbody>
              {emitidos.data.items.map((e) => (
                <tr key={e.id}>
                  <td>{dataHora(e.issued_at)}</td>
                  <td>
                    <Link to={rota(`relatorios/emitidos/${e.id}`)}>
                      <b>{e.code}</b> {e.title}
                    </Link>
                    {e.subtitle && <span className="mini"> · {e.subtitle}</span>}
                  </td>
                  <td>{e.period.label ?? `${e.period.from} – ${e.period.to}`}</td>
                  <td>{e.issued_by?.name ?? '—'}</td>
                  <td className="num">{e.active_links ?? 0}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

/** A faixa que diferencia o emitido do ao vivo (também na impressão e na página do cliente). */
export function FaixaDoEmitido({ quando, quem }: { quando: string; quem: string | null | undefined }) {
  const { t, dataHora } = useT()
  return (
    <p className="ng-emitido-faixa">
      {quem ? t('emitido.faixa', { quando: dataHora(quando), quem }) : t('emitido.faixa_sem_autor', { quando: dataHora(quando) })}
    </p>
  )
}

function TelaEmitido({ id }: { id: string }) {
  const { t, eu } = useT()
  const { rota } = useNagare()
  const emitido = useEmitido(id)
  if (emitido.isPending) return <Carregando texto={t('comum.carregando')} />
  if (emitido.isError) return <ErroDeApi erro={emitido.error} />
  const e = emitido.data
  return (
    <div className="ng-relatorios">
      <div className="ng-relatorio-controles">
        <Link className="ng-relatorio-voltar mini" to={rota('relatorios/emitidos')}>
          ‹ {t('emitido.voltar')}
        </Link>
        <Cabecalho kicker={t('relatorios.emitidos')} titulo={e.title} acoes={<AcoesDoEmitido emitido={e} />} />
      </div>
      <FaixaDoEmitido quando={e.issued_at} quem={e.issued_by?.name} />
      {eu?.capabilities.share_reports && <PainelDeLinks emitido={e} />}
      {e.document && <Documento doc={e.document} />}
    </div>
  )
}

function AcoesDoEmitido({ emitido }: { emitido: Emitido }) {
  const { t } = useT()
  const { produto } = useNagare()
  const [baixando, setBaixando] = useState(false)
  const [erro, setErro] = useState(false)
  const baixar = async (formato: 'xlsx' | 'csv') => {
    setBaixando(true)
    setErro(false)
    try {
      const { arquivo, nome } = await baixarLocal(`/reports/issued/${encodeURIComponent(emitido.id)}/export${consulta({ format: formato })}`, {
        'X-Produto': produto,
      })
      const url = URL.createObjectURL(arquivo)
      const a = document.createElement('a')
      a.href = url
      a.download = nome
      document.body.appendChild(a)
      a.click()
      a.remove()
      setTimeout(() => URL.revokeObjectURL(url), 10_000)
    } catch {
      setErro(true)
    } finally {
      setBaixando(false)
    }
  }
  return (
    <div className="ng-relatorio-acoes">
      <button type="button" className="btn sm" title={t('relatorios.dica_imprimir')} onClick={() => window.print()}>
        <Icone nome="documento" tamanho={16} /> {t('relatorios.imprimir')}
      </button>
      <button type="button" className="btn sm ghost" disabled={baixando} onClick={() => baixar('xlsx')}>
        {baixando ? t('relatorios.baixando') : t('relatorios.excel')}
      </button>
      <button type="button" className="btn sm ghost" disabled={baixando} onClick={() => baixar('csv')}>
        {t('relatorios.csv')}
      </button>
      {erro && (
        <span className="ng-ruim mini" role="alert">
          {t('relatorios.erro_planilha')}
        </span>
      )}
    </div>
  )
}

function PainelDeLinks({ emitido }: { emitido: Emitido }) {
  const { t, dataHora } = useT()
  const { produto } = useNagare()
  const queryClient = useQueryClient()
  const [dias, setDias] = useState(VALIDADE_PADRAO)
  const [novo, setNovo] = useState<LinkDeCliente | null>(null)
  const [copiado, setCopiado] = useState(false)
  const recarregar = () => queryClient.invalidateQueries({ queryKey: ['nagare', produto, 'emitidos'] })
  const criar = useMutation({
    mutationFn: () =>
      nagare<LinkDeCliente>(produto, `/reports/issued/${encodeURIComponent(emitido.id)}/links`, {
        metodo: 'POST',
        corpo: { days: dias },
      }),
    onSuccess: (link) => {
      setNovo(link)
      setCopiado(false)
      void recarregar()
    },
  })
  const revogar = useMutation({
    mutationFn: (linkId: string) =>
      nagare<LinkDeCliente>(produto, `/reports/issued/${encodeURIComponent(emitido.id)}/links/${encodeURIComponent(linkId)}`, {
        metodo: 'DELETE',
      }),
    onSuccess: () => void recarregar(),
  })
  const endereco = novo?.token ? enderecoDoLink(window.location.origin, BASE_SOZINHO, novo.token) : null
  const links = emitido.links ?? []

  return (
    <section className="card ng-links-cliente ng-relatorio-controles" aria-label={t('emitido.links_titulo')}>
      <h3>{t('emitido.links_titulo')}</h3>
      {!emitido.shareable ? (
        <p className="mini">{t('emitido.nao_compartilhavel')}</p>
      ) : (
        <>
          <p className="mini">{t('emitido.links_nota')}</p>
          <form
            className="ng-linha-campos"
            onSubmit={(ev) => {
              ev.preventDefault()
              criar.mutate()
            }}
          >
            <label className="campo ng-campo-curto">
              <span>{t('emitido.validade')}</span>
              <select value={dias} onChange={(ev) => setDias(Number(ev.target.value))}>
                {OPCOES_DE_VALIDADE.map((n) => (
                  <option key={n} value={n}>
                    {n === 1 ? t('emitido.um_dia') : t('emitido.dias', { n })}
                  </option>
                ))}
              </select>
            </label>
            <button type="submit" className="btn sm ng-alinhar-baixo" disabled={criar.isPending}>
              {criar.isPending ? t('emitido.criando') : t('emitido.criar_link')}
            </button>
          </form>
          {criar.isError && (
            <p className="ng-ruim mini" role="alert">
              {mensagemDeErro(criar.error, t)}
            </p>
          )}
          {endereco && (
            <div className="ng-link-novo" role="status">
              <p>{t('emitido.link_criado')}</p>
              <div className="ng-link-novo-linha">
                <input readOnly value={endereco} aria-label={t('emitido.links_titulo')} onFocus={(ev) => ev.currentTarget.select()} />
                <button
                  type="button"
                  className="btn sm"
                  onClick={() => {
                    void copiar(endereco).then(setCopiado)
                  }}
                >
                  {copiado ? t('emitido.copiado') : t('emitido.copiar')}
                </button>
              </div>
            </div>
          )}
        </>
      )}
      {links.length === 0 ? (
        emitido.shareable && <p className="mini">{t('emitido.links_vazio')}</p>
      ) : (
        <div className="ng-rolagem-x">
          <table className="ng-tabela">
            <thead>
              <tr>
                <th>{t('emitido.col_criado')}</th>
                <th>{t('emitido.col_projetos')}</th>
                <th>{t('emitido.col_vale_ate')}</th>
                <th>{t('emitido.col_situacao')}</th>
                <th className="num">{t('emitido.col_acessos')}</th>
                <th>{t('emitido.col_ultimo')}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {links.map((l) => (
                <tr key={l.id}>
                  <td>
                    {dataHora(l.created_at)}
                    {l.created_by && <span className="mini"> · {l.created_by.name}</span>}
                  </td>
                  <td>{l.projects.map((p) => p.name).join(', ')}</td>
                  <td>{dataHora(l.expires_at)}</td>
                  <td className={l.state === 'active' ? 'ng-bom' : 'ng-ruim'}>{t(ROTULO_DA_SITUACAO[l.state])}</td>
                  <td className="num">{l.access_count}</td>
                  <td>{l.last_access_at ? dataHora(l.last_access_at) : '—'}</td>
                  <td>
                    {l.state === 'active' && (
                      <button
                        type="button"
                        className="btn sm ghost"
                        disabled={revogar.isPending}
                        onClick={() => {
                          if (window.confirm(t('emitido.revogar_confirma'))) revogar.mutate(l.id)
                        }}
                      >
                        {t('emitido.revogar')}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {revogar.isError && (
        <p className="ng-ruim mini" role="alert">
          {mensagemDeErro(revogar.error, t)}
        </p>
      )}
    </section>
  )
}
