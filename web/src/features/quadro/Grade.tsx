/**
 * A grade do quadro: faixa de segmento, grupos, cabecalho das colunas e os cartoes, com raias
 * por classe de servico (flag `service_classes`). Arrastar mostra onde o cartao pode cair.
 */

import { useMemo, useRef, useState } from 'react'
import {
  DndContext,
  DragOverlay,
  PointerSensor,
  pointerWithin,
  useDroppable,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragOverEvent,
  type DragStartEvent,
} from '@dnd-kit/core'
import { hojeISO } from '@pampufa/ui'

import { CLASSES, type Cartao, type ClasseServico, type Coluna, type Quadro, type VisaoQuadro } from '../../api/tipos'
import { useT } from '../../i18n'
import { CartaoMini } from './CartaoMini'
import { EditorWip, SeloWip } from './EditorWip'
import { faixasDeGrupo, ordenarColunas, validarDestino, type Destino } from './regras'

export interface AcoesDaGrade {
  abrirCartao: (cartao: Cartao) => void
  menuMover: (cartao: Cartao) => void
  mover: (cartao: Cartao, destino: string) => void
  novoCartao: (coluna: Coluna, classe?: ClasseServico) => void
}

export function Grade({
  visao,
  quadro,
  cartoes,
  raias,
  podeMover,
  podePoliticas,
  recolhidas,
  alternarRecolhida,
  acoes,
}: {
  visao: VisaoQuadro
  /** O quadro inteiro (todas as colunas): a ordem do fluxo nao depende do segmento visto. */
  quadro: Quadro
  cartoes: Cartao[]
  raias: boolean
  podeMover: boolean
  podePoliticas: boolean
  recolhidas: Set<string>
  alternarRecolhida: (id: string) => void
  acoes: AcoesDaGrade
}) {
  const { t } = useT()
  const colunas = useMemo(() => ordenarColunas(visao.columns), [visao.columns])
  const todasColunas = useMemo(() => ordenarColunas(quadro.columns ?? visao.columns), [quadro.columns, visao.columns])
  const [arrastado, setArrastado] = useState<Cartao | null>(null)
  const [sobre, setSobre] = useState<string | null>(null)
  const acabouDeArrastar = useRef(false)
  const sensores = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }))
  const hoje = hojeISO()

  const destino: Record<string, Destino> = useMemo(() => {
    if (!arrastado) return {}
    return Object.fromEntries(
      colunas.map((c) => [c.id, validarDestino(arrastado, c.id, todasColunas, visao.wip)]),
    )
  }, [arrastado, colunas, todasColunas, visao.wip])

  const porColuna = useMemo(() => {
    const mapa = new Map<string, Cartao[]>()
    for (const c of cartoes) mapa.set(c.column_id, [...(mapa.get(c.column_id) ?? []), c])
    for (const lista of mapa.values()) lista.sort((a, b) => a.position - b.position || a.key.localeCompare(b.key))
    return mapa
  }, [cartoes])

  const classesDasRaias: (ClasseServico | null)[] = raias ? CLASSES : [null]
  const gruposPorId = new Map(visao.groups.map((g) => [g.id, g]))
  const segmentos = [...new Set(colunas.map((c) => c.segment))]
  const unificado = visao.segment === 'unified' && segmentos.length > 1

  const largura = (c: Coluna) => (recolhidas.has(c.id) ? '44px' : 'minmax(248px, 1fr)')
  const modelo = colunas.map(largura).join(' ')

  const inicio = (e: DragStartEvent) => setArrastado((e.active.data.current?.cartao as Cartao) ?? null)
  const passar = (e: DragOverEvent) => setSobre((e.over?.data.current?.colunaId as string) ?? null)
  const soltar = (e: DragEndEvent) => {
    const cartao = e.active.data.current?.cartao as Cartao | undefined
    const colunaId = e.over?.data.current?.colunaId as string | undefined
    setArrastado(null)
    setSobre(null)
    acabouDeArrastar.current = true
    window.setTimeout(() => (acabouDeArrastar.current = false), 0)
    if (cartao && colunaId && colunaId !== cartao.column_id) acoes.mover(cartao, colunaId)
  }
  const abrir = (cartao: Cartao) => {
    if (!acabouDeArrastar.current) acoes.abrirCartao(cartao)
  }

  return (
    <DndContext
      sensors={sensores}
      collisionDetection={pointerWithin}
      onDragStart={inicio}
      onDragOver={passar}
      onDragEnd={soltar}
      onDragCancel={() => {
        setArrastado(null)
        setSobre(null)
      }}
    >
      <div className="ng-grade-rolagem">
        <div className="ng-grade" style={{ gridTemplateColumns: modelo }}>
          {unificado &&
            segmentos.map((segmento) => {
              const doSegmento = colunas.filter((c) => c.segment === segmento)
              const primeira = colunas.indexOf(doSegmento[0]!) + 1
              return (
                <div
                  key={segmento}
                  className={`ng-faixa ${segmento}`}
                  style={{ gridColumn: `${primeira} / span ${doSegmento.length}`, gridRow: 1 }}
                >
                  {t(`segmento.${segmento}`)}
                </div>
              )
            })}
          {faixasDeGrupo(colunas).map((faixa) => {
            const grupo = faixa.grupoId ? gruposPorId.get(faixa.grupoId) : undefined
            const celula = faixa.grupoId ? visao.wip.groups[faixa.grupoId] : undefined
            return (
              <div
                key={`${faixa.inicio}-${faixa.grupoId ?? 'livre'}`}
                className={`ng-grupo${grupo ? '' : ' vazio'}`}
                style={{
                  gridColumn: `${faixa.inicio + 1} / span ${faixa.tamanho}`,
                  gridRow: 2,
                  borderColor: grupo?.color ?? undefined,
                }}
              >
                {grupo && (
                  <>
                    <span>{grupo.name}</span>
                    {celula && (
                      <EditorWip
                        alvo={{ tipo: 'grupo', id: grupo.id, quadroId: quadro.id }}
                        celula={celula}
                        podeEditar={podePoliticas}
                      />
                    )}
                  </>
                )}
              </div>
            )
          })}
          {colunas.map((coluna, indice) => (
            <CabecalhoColuna
              key={coluna.id}
              coluna={coluna}
              indice={indice}
              quadroId={quadro.id}
              recolhida={recolhidas.has(coluna.id)}
              alternar={() => alternarRecolhida(coluna.id)}
              wip={visao.wip.columns[coluna.id]}
              podePoliticas={podePoliticas}
              destino={destino[coluna.id]}
            />
          ))}
          {classesDasRaias.map((classe, linha) => {
            const daRaia = (c: Cartao) => classe === null || c.service_class === classe
            const total = cartoes.filter(daRaia).length
            return [
              raias ? (
                <div
                  key={`raia-${classe}`}
                  className={`ng-raia classe-${classe}`}
                  style={{ gridRow: 4 + linha * 2, gridColumn: '1 / -1' }}
                >
                  {t(`classe.${classe ?? 'standard'}`)} <span className="mini">({total})</span>
                </div>
              ) : null,
              ...colunas.map((coluna, indice) => (
                <Celula
                  key={`${coluna.id}-${classe ?? 'todas'}`}
                  coluna={coluna}
                  classe={classe}
                  linha={raias ? 5 + linha * 2 : 4}
                  indice={indice}
                  recolhida={recolhidas.has(coluna.id)}
                  destino={sobre === coluna.id ? destino[coluna.id] : arrastado ? destino[coluna.id] : undefined}
                  podeCriar={podeMover && coluna.kind !== 'done'}
                  novo={() => acoes.novoCartao(coluna, classe ?? undefined)}
                >
                  {(porColuna.get(coluna.id) ?? []).filter(daRaia).map((cartao) => (
                    <CartaoMini
                      key={cartao.id}
                      cartao={cartao}
                      sleDias={quadro.sle_days}
                      hoje={hoje}
                      podeArrastar={podeMover}
                      abrir={() => abrir(cartao)}
                      menuMover={() => acoes.menuMover(cartao)}
                    />
                  ))}
                </Celula>
              )),
            ]
          })}
        </div>
      </div>
      <DragOverlay dropAnimation={null}>
        {arrastado ? (
          <div className="ng-sombra">
            <span className="ng-chave">{arrastado.key}</span> {arrastado.title}
          </div>
        ) : null}
      </DragOverlay>
    </DndContext>
  )
}

function CabecalhoColuna({
  coluna,
  indice,
  quadroId,
  recolhida,
  alternar,
  wip,
  podePoliticas,
  destino,
}: {
  coluna: Coluna
  indice: number
  quadroId: string
  recolhida: boolean
  alternar: () => void
  wip: VisaoQuadro['wip']['columns'][string] | undefined
  podePoliticas: boolean
  destino: Destino | undefined
}) {
  const { t } = useT()
  return (
    <div
      className={`ng-cabecalho${recolhida ? ' recolhida' : ''}${destino ? ` alvo-${destino.estado}` : ''}`}
      style={{ gridColumn: indice + 1, gridRow: 3, borderTopColor: coluna.color ?? undefined }}
      title={coluna.description ?? undefined}
    >
      <button
        type="button"
        className="ng-recolher"
        aria-expanded={!recolhida}
        aria-label={recolhida ? t('quadro.expandir_coluna', { nome: coluna.name }) : t('quadro.recolher_coluna', { nome: coluna.name })}
        onClick={alternar}
      >
        {recolhida ? '›' : '‹'}
      </button>
      {!recolhida && (
        <>
          <div className="ng-cabecalho-nome">
            {coluna.is_commitment_point && (
              <span className="ng-ponto" title={t('quadro.ponto_compromisso')}>
                ◆
              </span>
            )}
            {coluna.is_delivery_point && (
              <span className="ng-ponto entrega" title={t('quadro.ponto_entrega')}>
                ✓
              </span>
            )}
            <b>{coluna.name}</b>
          </div>
          <div className="ng-cabecalho-info">
            <span className="ng-tipo" title={t(`tipo_coluna.${coluna.kind}.dica`)}>
              {t(`tipo_coluna.${coluna.kind}`)}
            </span>
            {wip && <EditorWip alvo={{ tipo: 'coluna', id: coluna.id, quadroId }} celula={wip} podeEditar={podePoliticas} />}
          </div>
        </>
      )}
      {recolhida && wip && <SeloWip celula={wip} compacto />}
    </div>
  )
}

function Celula({
  coluna,
  classe,
  linha,
  indice,
  recolhida,
  destino,
  podeCriar,
  novo,
  children,
}: {
  coluna: Coluna
  classe: ClasseServico | null
  linha: number
  indice: number
  recolhida: boolean
  destino: Destino | undefined
  podeCriar: boolean
  novo: () => void
  children: React.ReactNode
}) {
  const { t } = useT()
  const { setNodeRef } = useDroppable({ id: `${coluna.id}:${classe ?? 'todas'}`, data: { colunaId: coluna.id } })
  return (
    <div
      ref={setNodeRef}
      data-coluna={indice}
      data-linha={linha}
      className={`ng-celula${recolhida ? ' recolhida' : ''}${destino ? ` alvo-${destino.estado}` : ''}`}
      style={{ gridColumn: indice + 1, gridRow: linha }}
      title={destino?.motivo ? t(destino.motivo) : undefined}
    >
      {!recolhida && children}
      {!recolhida && podeCriar && (
        <button type="button" className="ng-novo" onClick={novo}>
          + {t('quadro.novo_cartao')}
        </button>
      )}
    </div>
  )
}
