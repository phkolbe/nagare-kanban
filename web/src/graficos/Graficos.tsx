/**
 * Os graficos das metricas e do simulador, em SVG proprio (decisao de 17/10/2026, sem
 * biblioteca): area empilhada (CFD), dispersao com percentis, barras com linha, histograma,
 * pontos por coluna (idade do WIP), barras horizontais, rosca, medidor, anel e mini linha.
 * Cores por variavel CSS (o tema troca sem redesenhar); classe de servico tem cor E forma.
 */

import type { ReactNode } from 'react'

import { arco, caminhoDeArea, caminhoDeLinha, eixoRedondo, faixas, linear, marcasDeTempo } from './escalas'
import { Figura, Marca, type Forma, type ItemLegenda, type Tabela } from './Figura'

const M = { esq: 46, dir: 14, topo: 12, base: 30 }

export const COR_CLASSE: Record<string, string> = {
  expedite: 'var(--ng-expedite)',
  fixed_date: 'var(--ng-fixed_date)',
  standard: 'var(--ng-standard)',
  intangible: 'var(--ng-intangible)',
}
export const FORMA_CLASSE: Record<string, Forma> = {
  expedite: 'triangulo',
  fixed_date: 'losango',
  standard: 'circulo',
  intangible: 'quadrado',
}
export const TOM: Record<string, string> = {
  info: 'var(--navy)',
  sucesso: 'var(--green)',
  alerta: 'var(--amber)',
  perigo: 'var(--red)',
}

/** Rampa ordinal (das colunas de montante as de jusante), da cor clara a escura. */
export function corDaRampa(indice: number, total: number): string {
  const fracao = total <= 1 ? 0.5 : indice / (total - 1)
  return `color-mix(in srgb, var(--ng-rampa-b) ${Math.round(fracao * 100)}%, var(--ng-rampa-a))`
}

interface Comum {
  titulo: string
  nota?: ReactNode
  altura?: number
  tabela?: Tabela
  textoVazio?: string
  acoes?: ReactNode
}

function EixoY({ marcas, y, largura, formatar }: { marcas: number[]; y: (v: number) => number; largura: number; formatar: (v: number) => string }) {
  return (
    <g className="ng-eixo" aria-hidden="true">
      {marcas.map((m) => (
        <g key={m}>
          <line x1={M.esq} x2={largura - M.dir} y1={y(m)} y2={y(m)} className="ng-grade-linha" />
          <text x={M.esq - 6} y={y(m)} dy="0.32em" textAnchor="end">
            {formatar(m)}
          </text>
        </g>
      ))}
    </g>
  )
}

function RotuloEixo({ x, y, texto, vertical = false }: { x: number; y: number; texto?: string; vertical?: boolean }) {
  if (!texto) return null
  return (
    <text className="ng-eixo-rotulo" x={x} y={y} textAnchor="middle" transform={vertical ? `rotate(-90 ${x} ${y})` : undefined} aria-hidden="true">
      {texto}
    </text>
  )
}

// ------------------------------------------------------------------ area empilhada (CFD)

export function AreaEmpilhada({
  x,
  series,
  formatarX,
  rotuloY,
  altura = 260,
  ...comum
}: Comum & {
  x: number[]
  /** Da mais a jusante (embaixo) a mais a montante (em cima). */
  series: { chave: string; rotulo: string; valores: number[]; cor: string }[]
  formatarX: (v: number) => string
  rotuloY?: string
}) {
  const totais = x.map((_, i) => series.reduce((s, serie) => s + (serie.valores[i] ?? 0), 0))
  const { dominio, marcas } = eixoRedondo(0, Math.max(0, ...totais))
  const itens = x.map((v, i) => `${formatarX(v)}: ${series.map((s) => `${s.rotulo} ${s.valores[i] ?? 0}`).join(', ')}`)
  return (
    <Figura
      {...comum}
      itens={itens}
      vazio={x.length === 0}
      legenda={series.map((s) => ({ chave: s.chave, rotulo: s.rotulo, cor: s.cor, forma: 'quadrado' as const }))}
    >
      {({ largura, foco, focar }) => {
        const sx = linear([x[0] ?? 0, x[x.length - 1] ?? 1], [M.esq, largura - M.dir])
        const sy = linear(dominio, [altura - M.base, M.topo])
        let base = x.map((v) => ({ x: sx(v), y: sy(0) }))
        const camadas = series.map((s) => {
          const acumulado = x.map((v, i) => ({ x: sx(v), y: sy(sy.inversa(base[i]!.y) + (s.valores[i] ?? 0)) }))
          const d = caminhoDeArea(acumulado, base)
          base = acumulado
          return { chave: s.chave, d, cor: s.cor }
        })
        return (
          <svg width={largura} height={altura} role="img" aria-label={comum.titulo}>
            <EixoY marcas={marcas} y={sy} largura={largura} formatar={(v) => String(v)} />
            {camadas.map((c) => (
              <path key={c.chave} d={c.d} fill={c.cor} stroke="var(--card)" strokeWidth={0.5} />
            ))}
            {marcasDeTempo(x[0] ?? 0, x[x.length - 1] ?? 1).map((m) => (
              <text key={m} className="ng-eixo" x={sx(m)} y={altura - 8} textAnchor="middle" aria-hidden="true">
                {formatarX(m)}
              </text>
            ))}
            <RotuloEixo x={12} y={altura / 2} texto={rotuloY} vertical />
            <rect
              x={M.esq}
              y={M.topo}
              width={Math.max(0, largura - M.esq - M.dir)}
              height={altura - M.topo - M.base}
              fill="transparent"
              onMouseMove={(e) => {
                const caixa = (e.currentTarget as SVGRectElement).getBoundingClientRect()
                const valor = sx.inversa(e.clientX - caixa.left + M.esq)
                let melhor = 0
                x.forEach((v, i) => Math.abs(v - valor) < Math.abs((x[melhor] ?? 0) - valor) && (melhor = i))
                focar(melhor)
              }}
              onMouseLeave={() => focar(null)}
            />
            {foco !== null && x[foco] !== undefined && (
              <line className="ng-mira" x1={sx(x[foco]!)} x2={sx(x[foco]!)} y1={M.topo} y2={altura - M.base} />
            )}
          </svg>
        )
      }}
    </Figura>
  )
}

// ------------------------------------------------------------------ dispersao

export interface Percentil {
  rotulo: string
  valor: number
  tom: keyof typeof TOM
}

export function Dispersao({
  pontos,
  grupos,
  percentis = [],
  formatarX,
  formatarY,
  rotuloY,
  altura = 280,
  ...comum
}: Comum & {
  pontos: { x: number; y: number; grupo: string; rotulo: string }[]
  grupos: ItemLegenda[]
  percentis?: Percentil[]
  formatarX: (v: number) => string
  formatarY: (v: number) => string
  rotuloY?: string
}) {
  const xs = pontos.map((p) => p.x)
  const { dominio, marcas } = eixoRedondo(0, Math.max(0, ...pontos.map((p) => p.y), ...percentis.map((p) => p.valor)))
  const porGrupo = new Map(grupos.map((g) => [g.chave, g]))
  return (
    <Figura {...comum} itens={pontos.map((p) => `${p.rotulo}: ${formatarY(p.y)} · ${formatarX(p.x)}`)} vazio={pontos.length === 0} legenda={grupos}>
      {({ largura, foco, focar }) => {
        const sx = linear([Math.min(...xs), Math.max(...xs) || 1], [M.esq + 6, largura - M.dir - 40])
        const sy = linear(dominio, [altura - M.base, M.topo])
        return (
          <svg width={largura} height={altura} role="img" aria-label={comum.titulo}>
            <EixoY marcas={marcas} y={sy} largura={largura} formatar={formatarY} />
            {percentis.map((p) => (
              <g key={p.rotulo}>
                <line className="ng-referencia" x1={M.esq} x2={largura - M.dir - 40} y1={sy(p.valor)} y2={sy(p.valor)} stroke={TOM[p.tom]} />
                <text x={largura - M.dir - 36} y={sy(p.valor)} dy="0.32em" className="ng-eixo" fill={TOM[p.tom]}>
                  {p.rotulo}
                </text>
              </g>
            ))}
            {pontos.map((p, i) => {
              const g = porGrupo.get(p.grupo)
              return (
                <g key={i} onMouseEnter={() => focar(i)} onMouseLeave={() => focar(null)} opacity={foco === null || foco === i ? 0.9 : 0.35}>
                  <Marca forma={g?.forma} cor={g?.cor ?? 'var(--navy)'} x={sx(p.x)} y={sy(p.y)} r={foco === i ? 6 : 4} />
                </g>
              )
            })}
            {marcasDeTempo(Math.min(...xs), Math.max(...xs), 5).map((m) => (
              <text key={m} className="ng-eixo" x={sx(m)} y={altura - 8} textAnchor="middle" aria-hidden="true">
                {formatarX(m)}
              </text>
            ))}
            <RotuloEixo x={12} y={altura / 2} texto={rotuloY} vertical />
          </svg>
        )
      }}
    </Figura>
  )
}

// ------------------------------------------------------------------ barras (com linha)

export function Barras({
  itens,
  linha,
  formatarValor = (v) => String(v),
  cor = 'var(--ng-rampa-b)',
  rotuloY,
  altura = 240,
  legendaBarras,
  ...comum
}: Comum & {
  itens: { rotulo: string; valor: number; sub?: string }[]
  linha?: { rotulo: string; valores: (number | null)[]; cor?: string }
  formatarValor?: (v: number) => string
  cor?: string
  rotuloY?: string
  legendaBarras?: string
}) {
  const maximo = Math.max(0, ...itens.map((i) => i.valor), ...(linha?.valores.filter((v): v is number => v != null) ?? []))
  const { dominio, marcas } = eixoRedondo(0, maximo)
  const legenda = linha
    ? [
        { chave: 'barras', rotulo: legendaBarras ?? comum.titulo, cor, forma: 'quadrado' as const },
        { chave: 'linha', rotulo: linha.rotulo, cor: linha.cor ?? 'var(--gold)', forma: 'circulo' as const },
      ]
    : undefined
  const intervalo = Math.max(1, Math.ceil(itens.length / 10))
  return (
    <Figura
      {...comum}
      legenda={legenda}
      vazio={itens.length === 0}
      itens={itens.map((it, i) => `${it.rotulo}: ${formatarValor(it.valor)}${linha?.valores[i] != null ? ` · ${linha.rotulo} ${formatarValor(linha.valores[i]!)}` : ''}`)}
    >
      {({ largura, foco, focar }) => {
        const sy = linear(dominio, [altura - M.base, M.topo])
        const fx = faixas(itens.length, [M.esq, largura - M.dir])
        const centro = (i: number) => fx.inicio(i) + fx.largura / 2
        return (
          <svg width={largura} height={altura} role="img" aria-label={comum.titulo}>
            <EixoY marcas={marcas} y={sy} largura={largura} formatar={formatarValor} />
            {itens.map((it, i) => (
              <g key={i} onMouseEnter={() => focar(i)} onMouseLeave={() => focar(null)}>
                <rect
                  x={fx.inicio(i)}
                  y={sy(it.valor)}
                  width={fx.largura}
                  height={Math.max(0, sy(0) - sy(it.valor))}
                  fill={cor}
                  opacity={foco === null || foco === i ? 1 : 0.45}
                  rx={2}
                />
                {i % intervalo === 0 && (
                  <text className="ng-eixo" x={centro(i)} y={altura - 8} textAnchor="middle" aria-hidden="true">
                    {it.rotulo}
                  </text>
                )}
              </g>
            ))}
            {linha && (
              <path
                d={caminhoDeLinha(linha.valores.map((v, i) => (v == null ? null : { x: centro(i), y: sy(v) })))}
                fill="none"
                stroke={linha.cor ?? 'var(--gold)'}
                strokeWidth={2}
              />
            )}
            <RotuloEixo x={12} y={altura / 2} texto={rotuloY} vertical />
          </svg>
        )
      }}
    </Figura>
  )
}

// ------------------------------------------------------------------ histograma

export function Histograma({
  faixasDeValor,
  marcadores = [],
  formatarX,
  rotuloY,
  altura = 240,
  cor = 'var(--ng-rampa-b)',
  ...comum
}: Comum & {
  faixasDeValor: { de: number; ate: number; contagem: number }[]
  marcadores?: { rotulo: string; valor: number; destaque?: boolean }[]
  formatarX: (v: number) => string
  rotuloY?: string
  cor?: string
}) {
  const { dominio, marcas } = eixoRedondo(0, Math.max(0, ...faixasDeValor.map((f) => f.contagem)))
  const inicio = faixasDeValor[0]?.de ?? 0
  const fim = faixasDeValor[faixasDeValor.length - 1]?.ate ?? 1
  return (
    <Figura
      {...comum}
      vazio={faixasDeValor.length === 0}
      itens={faixasDeValor.map((f) => `${formatarX(f.de)}–${formatarX(f.ate)}: ${f.contagem}`)}
    >
      {({ largura, foco, focar }) => {
        const sx = linear([inicio, fim], [M.esq, largura - M.dir])
        const sy = linear(dominio, [altura - M.base, M.topo + 14])
        return (
          <svg width={largura} height={altura} role="img" aria-label={comum.titulo}>
            <EixoY marcas={marcas} y={sy} largura={largura} formatar={(v) => String(v)} />
            {faixasDeValor.map((f, i) => (
              <rect
                key={i}
                x={sx(f.de) + 1}
                y={sy(f.contagem)}
                width={Math.max(1, sx(f.ate) - sx(f.de) - 2)}
                height={Math.max(0, sy(0) - sy(f.contagem))}
                fill={cor}
                opacity={foco === null || foco === i ? 1 : 0.45}
                onMouseEnter={() => focar(i)}
                onMouseLeave={() => focar(null)}
              />
            ))}
            {marcadores.map((m) => (
              <g key={m.rotulo}>
                <line
                  className="ng-referencia"
                  x1={sx(m.valor)}
                  x2={sx(m.valor)}
                  y1={M.topo + 10}
                  y2={altura - M.base}
                  stroke={m.destaque ? 'var(--amber)' : 'var(--navy)'}
                  strokeWidth={m.destaque ? 2 : 1}
                />
                <text x={sx(m.valor)} y={M.topo + 6} textAnchor="middle" className="ng-eixo" fontWeight={m.destaque ? 700 : 400}>
                  {m.rotulo}
                </text>
              </g>
            ))}
            {marcasDeTempo(inicio, fim, 6).map((m) => (
              <text key={m} className="ng-eixo" x={sx(m)} y={altura - 8} textAnchor="middle" aria-hidden="true">
                {formatarX(m)}
              </text>
            ))}
            <RotuloEixo x={12} y={altura / 2} texto={rotuloY} vertical />
          </svg>
        )
      }}
    </Figura>
  )
}

// ------------------------------------------------------------------ pontos por coluna (idade do WIP)

export function PontosPorColuna({
  colunas,
  pontos,
  referencias = [],
  grupos,
  formatarY,
  rotuloY,
  altura = 300,
  ...comum
}: Comum & {
  colunas: { id: string; nome: string; sub?: string }[]
  pontos: { coluna: string; y: number; grupo: string; rotulo: string }[]
  referencias?: Percentil[]
  grupos: ItemLegenda[]
  formatarY: (v: number) => string
  rotuloY?: string
}) {
  const { dominio, marcas } = eixoRedondo(0, Math.max(0, ...pontos.map((p) => p.y), ...referencias.map((r) => r.valor)))
  const porGrupo = new Map(grupos.map((g) => [g.chave, g]))
  const ordem = new Map(colunas.map((c, i) => [c.id, i]))
  const ordenados = [...pontos].sort((a, b) => (ordem.get(a.coluna) ?? 0) - (ordem.get(b.coluna) ?? 0) || b.y - a.y)
  return (
    <Figura
      {...comum}
      legenda={grupos}
      vazio={pontos.length === 0}
      itens={ordenados.map((p) => `${colunas[ordem.get(p.coluna) ?? 0]?.nome ?? ''} · ${p.rotulo}: ${formatarY(p.y)}`)}
    >
      {({ largura, foco, focar }) => {
        const sy = linear(dominio, [altura - M.base - 14, M.topo])
        const fx = faixas(colunas.length, [M.esq, largura - M.dir - 36], 0.1)
        const espalhar = (i: number, coluna: string) => {
          const mesmos = ordenados.filter((p) => p.coluna === coluna)
          const posicao = mesmos.indexOf(ordenados[i]!)
          return ((posicao % 7) - 3) * Math.min(6, fx.largura / 9)
        }
        return (
          <svg width={largura} height={altura} role="img" aria-label={comum.titulo}>
            <EixoY marcas={marcas} y={sy} largura={largura - 36} formatar={formatarY} />
            {referencias.map((r) => (
              <g key={r.rotulo}>
                <line className="ng-referencia" x1={M.esq} x2={largura - M.dir - 36} y1={sy(r.valor)} y2={sy(r.valor)} stroke={TOM[r.tom]} />
                <text x={largura - M.dir - 32} y={sy(r.valor)} dy="0.32em" className="ng-eixo" fill={TOM[r.tom]}>
                  {r.rotulo}
                </text>
              </g>
            ))}
            {colunas.map((c, i) => (
              <g key={c.id} aria-hidden="true">
                <text className="ng-eixo" x={fx.inicio(i) + fx.largura / 2} y={altura - 22} textAnchor="middle">
                  {c.nome.length > 14 ? `${c.nome.slice(0, 13)}…` : c.nome}
                </text>
                {c.sub && (
                  <text className="ng-eixo" x={fx.inicio(i) + fx.largura / 2} y={altura - 8} textAnchor="middle">
                    {c.sub}
                  </text>
                )}
              </g>
            ))}
            {ordenados.map((p, i) => {
              const indice = ordem.get(p.coluna) ?? 0
              const g = porGrupo.get(p.grupo)
              return (
                <g key={i} onMouseEnter={() => focar(i)} onMouseLeave={() => focar(null)} opacity={foco === null || foco === i ? 0.9 : 0.35}>
                  <Marca forma={g?.forma} cor={g?.cor ?? 'var(--navy)'} x={fx.inicio(indice) + fx.largura / 2 + espalhar(i, p.coluna)} y={sy(p.y)} r={foco === i ? 6 : 4} />
                </g>
              )
            })}
            <RotuloEixo x={12} y={altura / 2} texto={rotuloY} vertical />
          </svg>
        )
      }}
    </Figura>
  )
}

// ------------------------------------------------------------------ barras horizontais

export function BarrasHorizontais({
  linhas,
  series,
  formatarValor = (v) => String(v),
  larguraRotulo = 150,
  larguraNota = 90,
  alturaLinha = 26,
  ...comum
}: Comum & {
  linhas: { rotulo: string; segmentos: { chave: string; valor: number }[]; nota?: string }[]
  series: { chave: string; rotulo: string; cor: string }[]
  formatarValor?: (v: number) => string
  larguraRotulo?: number
  larguraNota?: number
  alturaLinha?: number
}) {
  const total = (l: (typeof linhas)[number]) => l.segmentos.reduce((s, x) => s + x.valor, 0)
  const maximo = Math.max(0, ...linhas.map(total))
  const cores = new Map(series.map((s) => [s.chave, s]))
  const altura = linhas.length * alturaLinha + 8
  return (
    <Figura
      {...comum}
      legenda={series.map((s) => ({ chave: s.chave, rotulo: s.rotulo, cor: s.cor, forma: 'quadrado' as const }))}
      vazio={linhas.length === 0}
      itens={linhas.map((l) => `${l.rotulo}: ${l.segmentos.map((s) => `${cores.get(s.chave)?.rotulo ?? s.chave} ${formatarValor(s.valor)}`).join(', ')}${l.nota ? ` (${l.nota})` : ''}`)}
    >
      {({ largura, foco, focar }) => {
        const sx = linear([0, maximo || 1], [larguraRotulo, largura - larguraNota])
        return (
          <svg width={largura} height={altura} role="img" aria-label={comum.titulo}>
            {linhas.map((l, i) => {
              let x = larguraRotulo
              const y = i * alturaLinha + 4
              return (
                <g key={i} onMouseEnter={() => focar(i)} onMouseLeave={() => focar(null)} opacity={foco === null || foco === i ? 1 : 0.5}>
                  <text className="ng-eixo" x={larguraRotulo - 8} y={y + alturaLinha / 2 - 2} dy="0.32em" textAnchor="end">
                    {l.rotulo.length > 22 ? `${l.rotulo.slice(0, 21)}…` : l.rotulo}
                  </text>
                  {l.segmentos.map((s) => {
                    const w = Math.max(0, sx(s.valor) - larguraRotulo)
                    const ret = <rect key={s.chave} x={x} y={y} width={w} height={alturaLinha - 8} fill={cores.get(s.chave)?.cor ?? 'var(--navy)'} />
                    x += w
                    return ret
                  })}
                  {l.nota && (
                    <text className="ng-eixo" x={x + 6} y={y + alturaLinha / 2 - 2} dy="0.32em">
                      {l.nota}
                    </text>
                  )}
                </g>
              )
            })}
          </svg>
        )
      }}
    </Figura>
  )
}

// ------------------------------------------------------------------ rosca

export function Rosca({
  fatias,
  valorCentro,
  rotuloCentro,
  formatarValor = (v) => String(v),
  ...comum
}: Comum & {
  fatias: { chave: string; rotulo: string; valor: number; cor: string; forma?: Forma }[]
  valorCentro: string
  rotuloCentro: string
  formatarValor?: (v: number) => string
}) {
  const total = fatias.reduce((s, f) => s + f.valor, 0)
  return (
    <Figura
      {...comum}
      legenda={fatias.map((f) => ({ chave: f.chave, rotulo: f.rotulo, cor: f.cor, forma: f.forma }))}
      vazio={total === 0}
      itens={fatias.map((f) => `${f.rotulo}: ${formatarValor(f.valor)} (${Math.round((f.valor / (total || 1)) * 100)}%)`)}
    >
      {({ foco, focar }) => {
        const tamanho = 200
        let inicio = 0
        return (
          <svg width={tamanho} height={tamanho} role="img" aria-label={comum.titulo} className="ng-centralizado">
            {fatias.map((f, i) => {
              const fim = inicio + f.valor / (total || 1)
              const d = arco(tamanho / 2, tamanho / 2, foco === i ? 94 : 88, 30, inicio, fim)
              inicio = fim
              return <path key={f.chave} d={d} fill={f.cor} stroke="var(--card)" strokeWidth={1.5} onMouseEnter={() => focar(i)} onMouseLeave={() => focar(null)} />
            })}
            <text x={tamanho / 2} y={tamanho / 2 - 4} textAnchor="middle" className="ng-centro-valor">
              {valorCentro}
            </text>
            <text x={tamanho / 2} y={tamanho / 2 + 16} textAnchor="middle" className="ng-eixo">
              {rotuloCentro}
            </text>
          </svg>
        )
      }}
    </Figura>
  )
}

// ------------------------------------------------------------------ medidor

export function Medidor({
  valor,
  min = 0,
  max = 2,
  alvo,
  rotuloValor,
  tom,
  rodape,
  ...comum
}: Comum & {
  valor: number
  min?: number
  max?: number
  alvo?: number
  rotuloValor: string
  tom: keyof typeof TOM
  rodape?: ReactNode
}) {
  const fracao = Math.max(0, Math.min(1, (valor - min) / (max - min || 1)))
  const angulo = (f: number) => Math.PI * (1 - f)
  const cx = 110
  const cy = 108
  const r = 90
  const ponto = (f: number, raio = r) => [cx + raio * Math.cos(angulo(f)), cy - raio * Math.sin(angulo(f))] as const
  const [x1, y1] = ponto(fracao)
  return (
    <Figura {...comum} itens={[rotuloValor]}>
      {() => (
        <div className="ng-centralizado">
          <svg width={220} height={130} role="img" aria-label={`${comum.titulo}: ${rotuloValor}`}>
            <path d={`M${cx - r},${cy}A${r},${r} 0 0 1 ${cx + r},${cy}`} fill="none" stroke="var(--line)" strokeWidth={16} />
            <path d={`M${cx - r},${cy}A${r},${r} 0 0 1 ${x1},${y1}`} fill="none" stroke={TOM[tom]} strokeWidth={16} />
            {alvo !== undefined && (() => {
              const f = (alvo - min) / (max - min || 1)
              const [ax, ay] = ponto(f, r + 12)
              const [bx, by] = ponto(f, r - 12)
              return <line x1={ax} y1={ay} x2={bx} y2={by} stroke="var(--text)" strokeWidth={2} />
            })()}
            <text x={cx} y={cy - 16} textAnchor="middle" className="ng-centro-valor">
              {rotuloValor}
            </text>
          </svg>
          {rodape}
        </div>
      )}
    </Figura>
  )
}

// ------------------------------------------------------------------ anel e mini linha (sem moldura)

export function Anel({ fracao, tamanho = 40, cor = 'var(--green)', rotulo }: { fracao: number; tamanho?: number; cor?: string; rotulo: string }) {
  const f = Math.max(0, Math.min(1, fracao))
  return (
    <svg width={tamanho} height={tamanho} role="img" aria-label={rotulo}>
      <circle cx={tamanho / 2} cy={tamanho / 2} r={tamanho / 2 - 4} fill="none" stroke="var(--line)" strokeWidth={4} />
      {f > 0 && <path d={arco(tamanho / 2, tamanho / 2, tamanho / 2 - 2, 4, 0, f)} fill={cor} />}
      <text x={tamanho / 2} y={tamanho / 2} dy="0.35em" textAnchor="middle" className="ng-anel-texto">
        {Math.round(f * 100)}
      </text>
    </svg>
  )
}

export function MiniLinha({ valores, largura = 120, altura = 28, rotulo }: { valores: number[]; largura?: number; altura?: number; rotulo: string }) {
  if (valores.length < 2) return null
  const sx = linear([0, valores.length - 1], [2, largura - 2])
  const sy = linear([0, Math.max(1, ...valores)], [altura - 2, 2])
  return (
    <svg width={largura} height={altura} role="img" aria-label={rotulo}>
      <path d={caminhoDeLinha(valores.map((v, i) => ({ x: sx(i), y: sy(v) })))} fill="none" stroke="var(--ng-rampa-b)" strokeWidth={1.6} />
    </svg>
  )
}
