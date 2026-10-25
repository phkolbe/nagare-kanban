/**
 * Quadro do produto (modulo Tarefas, NG7): `/grupo-sr/<produto>/tarefas/quadro/<quadro>`.
 *
 * Sem quadro na URL abre o que recebe as providencias do produto (NG14). `?cartao=<id>` abre o
 * cartao na gaveta (o link que a caixa de providencias e o sino da Pampufa mostram).
 *
 * Mais espaco para os cartoes (18/10/2026): uma linha so de titulo (com a busca, o WIP do fluxo
 * e o selo de filtro), as ferramentas numa barra recolhivel ou num painel flutuante (a escolha
 * e da pessoa), cartoes compactos e a tela cheia do quadro.
 */

import { useCallback, useEffect, useRef, useMemo, useState, type ReactNode } from 'react'
import { Navigate, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { Aviso, Cabecalho, Carregando, ErroDeApi, Vazio } from '@pampufa/ui'

import { useNagare } from '../../app/contexto'
import type { Cartao, ClasseServico, Coluna, SegmentoVisao } from '../../api/tipos'
import { Janela } from '../../componentes/Sobreposicao'
import { SeletorIdioma } from '../../componentes/SeletorIdioma'
import { useT } from '../../i18n'
import { GavetaCartao } from '../cartao/GavetaCartao'
import { EditorEstrutura } from '../estrutura/EditorEstrutura'
import { useAlterarQuadro, useMover, useQuadro, useQuadros, useVinculo, useVisao } from './consultas'
import { EditorWip } from './EditorWip'
import { BotaoDeFiltros, BuscaDeCartoes, SeloFiltrado } from './Filtros'
import { Grade } from './Grade'
import { NovoCartao } from './NovoCartao'
import { destinosAFrente, FILTROS_VAZIOS, filtrarNaTela, gravarFiltros, lerFiltros, ordenarColunas, validarDestino } from './regras'
import { useFormatoFerramentas, useLigadoNoNavegador, useTeclasDaVisualizacao, useTelaCheia } from './visualizacao'

export function TelaQuadro() {
  const { quadroId } = useParams()
  const quadros = useQuadros()
  const vinculo = useVinculo()
  const { t } = useT()
  const [busca] = useSearchParams()

  if (quadroId) return <QuadroDoProduto key={quadroId} quadroId={quadroId} />
  if (quadros.isPending || vinculo.isPending) return <Carregando texto={t('comum.carregando')} />
  if (quadros.isError) return <ErroDeApi erro={quadros.error} />
  const ativos = quadros.data.items.filter((q) => !q.archived)
  const padrao = vinculo.data?.link?.board_id ?? ativos.find((q) => !q.read_only)?.id ?? ativos[0]?.id
  if (!padrao) {
    return (
      <>
        <Cabecalho kicker={t('quadro.kicker')} titulo={t('quadro.titulo')} acoes={<SeletorIdioma />} />
        <section className="card">
          <Vazio titulo={t('quadro.sem_quadro')}>{t('quadro.sem_quadro_dica')}</Vazio>
        </section>
      </>
    )
  }
  const sufixo = busca.toString()
  return <Navigate to={`${padrao}${sufixo ? `?${sufixo}` : ''}`} replace />
}

function chaveRecolhidas(quadroId: string) {
  return `nagare.recolhidas.${quadroId}`
}

function lerRecolhidas(quadroId: string): Set<string> {
  try {
    return new Set(JSON.parse(window.localStorage.getItem(chaveRecolhidas(quadroId)) ?? '[]') as string[])
  } catch {
    return new Set()
  }
}

function QuadroDoProduto({ quadroId }: { quadroId: string }) {
  const { rota } = useNagare()
  const { t, eu } = useT()
  const navegar = useNavigate()
  const [busca, setBusca] = useSearchParams()
  const filtros = lerFiltros(busca)
  const flags = eu?.flags ?? {}
  const segmentosLigados: SegmentoVisao[] = [
    ...(flags.unified_flow !== false ? (['unified'] as const) : []),
    ...(flags.upstream_board !== false ? (['upstream'] as const) : []),
    ...(flags.downstream_board !== false ? (['downstream'] as const) : []),
  ]
  const segmento = (busca.get('segmento') as SegmentoVisao | null) ?? segmentosLigados[0] ?? 'unified'
  const raias = flags.service_classes !== false && busca.get('raias') === 'classe'

  const quadros = useQuadros()
  const quadro = useQuadro(quadroId)
  const visao = useVisao(quadroId, segmento, filtros)
  const mover = useMover(quadroId)
  const alterarQuadro = useAlterarQuadro(quadroId)

  const [recolhidas, setRecolhidas] = useState(() => lerRecolhidas(quadroId))
  const [novo, setNovo] = useState<{ coluna: Coluna; classe?: ClasseServico } | null>(null)
  const [menu, setMenu] = useState<Cartao | null>(null)
  const [confirmar, setConfirmar] = useState<{ cartao: Cartao; destino: Coluna } | null>(null)
  const [estrutura, setEstrutura] = useState(false)
  const [avisoMotivo, setAvisoMotivo] = useState<string | null>(null)
  const [formato, trocarFormato] = useFormatoFerramentas()
  const [barraAberta, setBarraAberta] = useLigadoNoNavegador('nagare.ferramentas.aberta', false)
  const [compactos, setCompactos] = useLigadoNoNavegador('nagare.cartoes.compactos', false)
  const [painelAberto, setPainelAberto] = useState(false)
  const [telaCheia, setTelaCheia] = useTelaCheia()
  const ferramentasAbertas = formato === 'bar' ? barraAberta : painelAberto
  const alternarFerramentas = useCallback(
    () => (formato === 'bar' ? setBarraAberta(!barraAberta) : setPainelAberto((aberto) => !aberto)),
    [formato, barraAberta, setBarraAberta],
  )
  const alternarTelaCheia = useCallback(() => setTelaCheia(!telaCheia), [telaCheia, setTelaCheia])
  const sairDaTelaCheia = useCallback(() => setTelaCheia(false), [setTelaCheia])
  useTeclasDaVisualizacao({ alternarTelaCheia, sairDaTelaCheia, alternarFerramentas })

  const alternarRecolhida = useCallback(
    (id: string) =>
      setRecolhidas((atual) => {
        const nova = new Set(atual)
        if (nova.has(id)) nova.delete(id)
        else nova.add(id)
        try {
          window.localStorage.setItem(chaveRecolhidas(quadroId), JSON.stringify([...nova]))
        } catch {
          /* sem armazenamento: vale so nesta pagina */
        }
        return nova
      }),
    [quadroId],
  )

  const cartoes = useMemo(() => filtrarNaTela(visao.data?.cards ?? [], filtros), [visao.data?.cards, filtros])
  const cartaoAberto = busca.get('cartao')

  const trocarBusca = (alterar: (b: URLSearchParams) => void) => {
    const nova = new URLSearchParams(busca)
    alterar(nova)
    setBusca(nova, { replace: true })
  }
  const abrirCartao = (cartao: Cartao) => trocarBusca((b) => b.set('cartao', cartao.id))
  const fecharCartao = () => trocarBusca((b) => b.delete('cartao'))

  if (quadro.isPending || (visao.isPending && !visao.data)) return <Carregando texto={t('comum.carregando')} />
  if (quadro.isError) return <ErroDeApi erro={quadro.error} />
  if (visao.isError) return <ErroDeApi erro={visao.error} />
  const q = quadro.data
  const v = visao.data!
  const capacidades = eu?.capabilities ?? {}
  const podeMover = Boolean(capacidades.manage_cards) && !q.read_only && q.kind === 'operational'
  const podePoliticas = Boolean(capacidades.manage_boards) && !q.read_only
  const todasColunas = ordenarColunas(q.columns ?? v.columns)

  const pedirMovimento = (cartao: Cartao, destinoId: string) => {
    const destino = todasColunas.find((c) => c.id === destinoId)
    if (!destino) return
    const veredito = validarDestino(cartao, destinoId, todasColunas, v.wip)
    // Para tras, lacrado, mesma coluna: a regra e clara, nem vai ao servidor. WIP cheio vai:
    // o servidor tem a contagem atual (outra pessoa pode ter liberado a vaga).
    const doWip = veredito.motivo === 'mover.coluna_cheia' || veredito.motivo === 'mover.grupo_cheio'
    if (veredito.estado === 'invalido' && veredito.motivo && !doWip) {
      setAvisoMotivo(t(veredito.motivo))
      return
    }
    setAvisoMotivo(null)
    if (q.confirm_moves) setConfirmar({ cartao, destino })
    else mover.mutate({ cartao, destino: destinoId })
  }

  const segmentado = segmento !== (segmentosLigados[0] ?? 'unified')
  const limparTudo = () =>
    trocarBusca((b) => {
      const limpa = gravarFiltros(b, FILTROS_VAZIOS)
      for (const chave of [...b.keys()]) b.delete(chave)
      limpa.forEach((valor, chave) => b.set(chave, valor))
      b.delete('segmento')
    })
  const recolherTodas = () => {
    const todas = recolhidas.size === 0
    const nova = new Set(todas ? v.columns.map((c) => c.id) : [])
    try {
      window.localStorage.setItem(chaveRecolhidas(quadroId), JSON.stringify([...nova]))
    } catch {
      /* sem armazenamento */
    }
    setRecolhidas(nova)
  }
  const ferramentas = (
    <Ferramentas
      emPainel={formato === 'panel'}
      ver={
        <>
          {segmentosLigados.length > 1 && (
            <div className="abas" role="tablist" aria-label={t('quadro.segmento')}>
              {segmentosLigados.map((s) => (
                <button
                  key={s}
                  type="button"
                  role="tab"
                  aria-selected={segmento === s}
                  onClick={() => trocarBusca((b) => (s === segmentosLigados[0] ? b.delete('segmento') : b.set('segmento', s)))}
                >
                  {t(`segmento.${s}`)}
                </button>
              ))}
            </div>
          )}
          {flags.service_classes !== false && (
            <label className="ng-alternar">
              <input
                type="checkbox"
                checked={raias}
                onChange={(e) => trocarBusca((b) => (e.target.checked ? b.set('raias', 'classe') : b.delete('raias')))}
              />
              {t('quadro.raias')}
            </label>
          )}
          <label className="ng-alternar">
            <input type="checkbox" checked={compactos} onChange={(e) => setCompactos(e.target.checked)} />
            {t('quadro.cartoes_compactos')}
          </label>
        </>
      }
      filtrar={<BotaoDeFiltros filtros={filtros} cartoes={v.cards} />}
      politicas={
        <SleDoQuadro sle={q.sle_days} percentil={q.sle_percentile} podeEditar={podePoliticas} gravar={(dias) => alterarQuadro.mutate({ sle_days: dias })} />
      }
      estrutura={
        <>
          <button type="button" className="btn sm ghost" onClick={recolherTodas}>
            {recolhidas.size === 0 ? t('quadro.recolher_todas') : t('quadro.expandir_todas')}
          </button>
          {Boolean(capacidades.manage_boards) && (
            <button type="button" className="btn sm sec" onClick={() => setEstrutura(true)}>
              {t('quadro.editar_estrutura')}
            </button>
          )}
        </>
      }
      formato={
        <div className="abas ng-abas-mini" role="radiogroup" aria-label={t('quadro.formato_ferramentas')}>
          {(['bar', 'panel'] as const).map((f) => (
            <button
              key={f}
              type="button"
              role="radio"
              aria-checked={formato === f}
              aria-selected={formato === f}
              onClick={() => {
                if (f === formato) return
                setPainelAberto(f === 'panel')
                if (f === 'bar') setBarraAberta(true)
                trocarFormato(f)
              }}
            >
              {t(f === 'bar' ? 'quadro.formato_barra' : 'quadro.formato_painel')}
            </button>
          ))}
        </div>
      }
    />
  )

  return (
    <>
      <div className="ng-quadro-topo">
        <h1 className="ng-quadro-titulo" title={q.description ?? undefined}>
          {q.name}
          {q.description && (
            <span className="ng-quadro-info" aria-hidden="true">
              ⓘ
            </span>
          )}
        </h1>
        {q.description && <p className="visualmente-oculto">{q.description}</p>}
        <BuscaDeCartoes filtros={filtros} />
        {flags.wip_flow !== false && (
          <span className="ng-pilula">
            {t('quadro.wip_fluxo')} <EditorWip alvo={{ tipo: 'fluxo', quadroId }} celula={v.wip.flow} podeEditar={podePoliticas} />
          </span>
        )}
        {!(formato === 'bar' && barraAberta) && (
          <SeloFiltrado filtros={filtros} total={cartoes.length} segmentado={segmentado} limpar={limparTudo} />
        )}
        <span className="ng-espaco" />
        <PainelFlutuante aberto={formato === 'panel' && painelAberto} fechar={() => setPainelAberto(false)} conteudo={ferramentas}>
          <button
            type="button"
            className={`btn sm ${ferramentasAbertas ? '' : 'sec'}`}
            aria-expanded={ferramentasAbertas}
            title={t('quadro.visualizacao_dica')}
            onClick={alternarFerramentas}
          >
            ⚙ {t('quadro.visualizacao')} {ferramentasAbertas ? '▴' : '▾'}
          </button>
        </PainelFlutuante>
        <button
          type="button"
          className="btn sm ghost"
          aria-label={t('quadro.tela_cheia')}
          title={t('quadro.tela_cheia_dica')}
          onClick={() => setTelaCheia(true)}
        >
          ⛶
        </button>
        <label className="ng-seletor-quadro">
          <span className="visualmente-oculto">{t('quadro.trocar')}</span>
          <select value={quadroId} aria-label={t('quadro.trocar')} onChange={(e) => navegar(rota(`quadro/${e.target.value}`))}>
            {(quadros.data?.items ?? [])
              .filter((x) => !x.archived)
              .map((x) => (
                <option key={x.id} value={x.id}>
                  {x.name}
                  {x.read_only ? ` (${t('quadro.so_leitura')})` : ''}
                </option>
              ))}
          </select>
        </label>
        <SeletorIdioma />
      </div>
      {formato === 'bar' && barraAberta && <div className="ng-barra ng-barra-ferramentas">{ferramentas}</div>}
      {telaCheia && (
        <button type="button" className="btn sm ng-sair-tela-cheia" onClick={sairDaTelaCheia} title={t('quadro.sair_tela_cheia_dica')}>
          ✕ {t('quadro.sair_tela_cheia')}
        </button>
      )}
      {q.read_only && <Aviso tipo="alerta">{t('quadro.aviso_so_leitura')}</Aviso>}
      {!q.read_only && q.product_board && q.flow_locked && podePoliticas && (
        <Aviso tipo="info">{t('quadro.aviso_travado')}</Aviso>
      )}
      {avisoMotivo && (
        <Aviso tipo="alerta">
          {avisoMotivo}{' '}
          <button type="button" className="link" onClick={() => setAvisoMotivo(null)}>
            {t('comum.ok')}
          </button>
        </Aviso>
      )}

      {v.columns.length === 0 ? (
        <section className="card">
          <Vazio titulo={t('quadro.sem_colunas')}>{t('quadro.sem_colunas_dica')}</Vazio>
        </section>
      ) : (
        <Grade
          visao={v}
          quadro={q}
          cartoes={cartoes}
          raias={raias}
          podeMover={podeMover}
          podePoliticas={podePoliticas}
          recolhidas={recolhidas}
          alternarRecolhida={alternarRecolhida}
          compactos={compactos}
          acoes={{
            abrirCartao,
            menuMover: setMenu,
            mover: pedirMovimento,
            novoCartao: (coluna, classe) => setNovo({ coluna, classe }),
          }}
        />
      )}

      {novo && (
        <NovoCartao
          quadroId={quadroId}
          coluna={novo.coluna}
          classe={novo.classe}
          fechar={() => setNovo(null)}
          criado={(cartao) => {
            setNovo(null)
            abrirCartao(cartao)
          }}
        />
      )}

      {menu && (
        <Janela titulo={t('mover.titulo', { chave: menu.key })} fechar={() => setMenu(null)} largura={420}>
          <p className="mini">{t('mover.so_para_frente')}</p>
          <ul className="ng-lista-destinos">
            {destinosAFrente(menu, todasColunas).map((coluna) => {
              const veredito = validarDestino(menu, coluna.id, todasColunas, v.wip)
              return (
                <li key={coluna.id}>
                  <button
                    type="button"
                    className={`ng-destino ${veredito.estado}`}
                    disabled={veredito.estado === 'invalido'}
                    onClick={() => {
                      const cartao = menu
                      setMenu(null)
                      pedirMovimento(cartao, coluna.id)
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

      {confirmar && (
        <ConfirmarMovimento
          cartao={confirmar.cartao}
          destino={confirmar.destino}
          fechar={() => setConfirmar(null)}
          confirmar={(naoPerguntar) => {
            const { cartao, destino } = confirmar
            setConfirmar(null)
            if (naoPerguntar) alterarQuadro.mutate({ confirm_moves: false })
            mover.mutate({ cartao, destino: destino.id })
          }}
        />
      )}

      {estrutura && <EditorEstrutura quadro={q} fechar={() => setEstrutura(false)} />}

      {cartaoAberto && (
        <GavetaCartao
          cartaoId={cartaoAberto}
          quadro={q}
          colunas={todasColunas}
          wip={v.wip}
          podeEditar={podeMover}
          fechar={fecharCartao}
          mover={(cartao, destino) => pedirMovimento(cartao, destino)}
        />
      )}
    </>
  )
}

function ConfirmarMovimento({
  cartao,
  destino,
  fechar,
  confirmar,
}: {
  cartao: Cartao
  destino: Coluna
  fechar: () => void
  confirmar: (naoPerguntar: boolean) => void
}) {
  const { t } = useT()
  const [naoPerguntar, setNaoPerguntar] = useState(false)
  return (
    <Janela
      titulo={t('mover.confirmar_titulo')}
      fechar={fechar}
      largura={440}
      rodape={
        <>
          <button className="btn" type="button" autoFocus onClick={() => confirmar(naoPerguntar)}>
            {t('mover.confirmar')}
          </button>
          <button className="btn ghost" type="button" onClick={fechar}>
            {t('comum.cancelar')}
          </button>
        </>
      }
    >
      <p>{t('mover.confirmar_texto', { chave: cartao.key, coluna: destino.name })}</p>
      <label className="ng-alternar">
        <input type="checkbox" checked={naoPerguntar} onChange={(e) => setNaoPerguntar(e.target.checked)} />
        {t('mover.nao_perguntar')}
      </label>
    </Janela>
  )
}

function SleDoQuadro({
  sle,
  percentil,
  podeEditar,
  gravar,
}: {
  sle: number | null
  percentil: number | null
  podeEditar: boolean
  gravar: (dias: number | null) => void
}) {
  const { t } = useT()
  const [editando, setEditando] = useState(false)
  const [dias, setDias] = useState(sle?.toString() ?? '')
  const texto = sle ? t('quadro.sle', { dias: sle, percentil: percentil ?? 85 }) : t('quadro.sem_sle')
  if (!podeEditar) return <span className="ng-pilula">{texto}</span>
  if (!editando) {
    return (
      <button type="button" className="ng-pilula" onClick={() => setEditando(true)} title={t('quadro.sle_dica')}>
        {texto}
      </button>
    )
  }
  return (
    <form
      className="ng-pilula"
      onSubmit={(e) => {
        e.preventDefault()
        gravar(dias === '' ? null : Math.max(1, Number(dias)))
        setEditando(false)
      }}
    >
      <label>
        {t('quadro.sle_rotulo')}{' '}
        <input type="number" min={1} value={dias} autoFocus onChange={(e) => setDias(e.target.value)} style={{ width: 60 }} />
      </label>
      <button className="btn sm" type="submit">
        {t('comum.salvar')}
      </button>
    </form>
  )
}

/**
 * As ferramentas do quadro, as mesmas nos dois formatos (18/10/2026): em linha na barra
 * recolhivel, ou em grupos com titulo no painel flutuante.
 */
function Ferramentas({
  emPainel,
  ver,
  filtrar,
  politicas,
  estrutura,
  formato,
}: {
  emPainel: boolean
  ver: ReactNode
  filtrar: ReactNode
  politicas: ReactNode
  estrutura: ReactNode
  formato: ReactNode
}) {
  const { t } = useT()
  const grupos: [string, ReactNode][] = [
    [t('quadro.ferramentas_ver'), ver],
    [t('quadro.ferramentas_filtrar'), filtrar],
    [t('quadro.ferramentas_politicas'), politicas],
    [t('quadro.ferramentas_estrutura'), estrutura],
    [t('quadro.formato_ferramentas'), formato],
  ]
  return (
    <div className={emPainel ? 'ng-ferramentas painel' : 'ng-ferramentas'}>
      {grupos.map(([titulo, conteudo]) => (
        <div key={titulo} className="ng-ferramentas-grupo" role="group" aria-label={titulo}>
          {emPainel && <div className="ng-ferramentas-titulo">{titulo}</div>}
          <div className="ng-ferramentas-itens">{conteudo}</div>
        </div>
      ))}
    </div>
  )
}

/** O painel flutuante das ferramentas: abre sob o botao e fecha ao clicar fora ou com Esc. */
function PainelFlutuante({
  aberto,
  fechar,
  conteudo,
  children,
}: {
  aberto: boolean
  fechar: () => void
  conteudo: ReactNode
  children: ReactNode
}) {
  const { t } = useT()
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!aberto) return
    const clique = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && fechar()
    const tecla = (e: KeyboardEvent) => e.key === 'Escape' && fechar()
    document.addEventListener('mousedown', clique)
    document.addEventListener('keydown', tecla)
    return () => {
      document.removeEventListener('mousedown', clique)
      document.removeEventListener('keydown', tecla)
    }
  }, [aberto, fechar])
  return (
    <div className="ng-ferramentas-ancora" ref={ref}>
      {children}
      {aberto && (
        <div className="popover ng-ferramentas-painel" role="dialog" aria-label={t('quadro.visualizacao')}>
          {conteudo}
        </div>
      )}
    </div>
  )
}
