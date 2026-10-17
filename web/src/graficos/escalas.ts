/** Escalas e marcas dos graficos (puro: sem React, conferido em `npm run check`). */

export interface Escala {
  (valor: number): number
  dominio: [number, number]
  faixa: [number, number]
  inversa: (pixel: number) => number
}

export function linear(dominio: [number, number], faixa: [number, number]): Escala {
  const [d0, d1] = dominio
  const [r0, r1] = faixa
  const largura = d1 - d0 || 1
  const escala = ((v: number) => r0 + ((v - d0) / largura) * (r1 - r0)) as Escala
  escala.dominio = dominio
  escala.faixa = faixa
  escala.inversa = (p: number) => d0 + ((p - r0) / (r1 - r0 || 1)) * largura
  return escala
}

/** Passo "redondo" (1, 2, 2,5 ou 5 vezes uma potencia de 10) para cerca de `quantos` marcas. */
export function passoRedondo(amplitude: number, quantos = 5): number {
  if (amplitude <= 0 || !Number.isFinite(amplitude)) return 1
  const bruto = amplitude / Math.max(1, quantos)
  const potencia = 10 ** Math.floor(Math.log10(bruto))
  const fracao = bruto / potencia
  const fator = fracao <= 1 ? 1 : fracao <= 2 ? 2 : fracao <= 2.5 ? 2.5 : fracao <= 5 ? 5 : 10
  return fator * potencia
}

/** Dominio de 0 (ou do minimo) ate um teto redondo, e as marcas do eixo. */
export function eixoRedondo(minimo: number, maximo: number, quantos = 5): { dominio: [number, number]; marcas: number[] } {
  const baixo = Math.min(0, minimo)
  const alto = maximo <= baixo ? baixo + 1 : maximo
  const passo = passoRedondo(alto - baixo, quantos)
  const inicio = Math.floor(baixo / passo) * passo
  const fim = Math.ceil(alto / passo) * passo
  const marcas: number[] = []
  for (let v = inicio; v <= fim + passo / 1e6; v += passo) marcas.push(Number(v.toFixed(10)))
  return { dominio: [inicio, fim], marcas }
}

/** Faixas iguais para categorias (barras): posicao e largura de cada uma. */
export function faixas(quantidade: number, faixa: [number, number], folga = 0.2): { inicio: (i: number) => number; largura: number } {
  const [r0, r1] = faixa
  const passo = (r1 - r0) / Math.max(1, quantidade)
  const largura = passo * (1 - folga)
  return { inicio: (i) => r0 + i * passo + (passo - largura) / 2, largura }
}

/** Instante ISO ou data `AAAA-MM-DD` em milissegundos. */
export function ms(valor: string | number): number {
  if (typeof valor === 'number') return valor
  return Date.parse(/^\d{4}-\d{2}-\d{2}$/.test(valor) ? `${valor}T00:00:00Z` : valor)
}

/** Marcas de tempo: ate `quantos` datas espalhadas no dominio (inicio e fim inclusos). */
export function marcasDeTempo(inicio: number, fim: number, quantos = 6): number[] {
  if (fim <= inicio) return [inicio]
  const passo = (fim - inicio) / Math.max(1, quantos - 1)
  return Array.from({ length: quantos }, (_, i) => inicio + i * passo)
}

/** Caminho SVG de uma linha pelos pontos (pula os nulos). */
export function caminhoDeLinha(pontos: ({ x: number; y: number } | null)[]): string {
  let d = ''
  let novo = true
  for (const p of pontos) {
    if (!p) {
      novo = true
      continue
    }
    d += `${novo ? 'M' : 'L'}${p.x.toFixed(1)},${p.y.toFixed(1)}`
    novo = false
  }
  return d
}

/** Area entre a linha de cima e a de baixo (empilhamento). */
export function caminhoDeArea(cima: { x: number; y: number }[], baixo: { x: number; y: number }[]): string {
  if (cima.length === 0) return ''
  const ida = cima.map((p, i) => `${i ? 'L' : 'M'}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join('')
  const volta = [...baixo].reverse().map((p) => `L${p.x.toFixed(1)},${p.y.toFixed(1)}`).join('')
  return `${ida}${volta}Z`
}

/** Arco de rosca de `de` a `ate` (fracoes de 0 a 1). */
export function arco(cx: number, cy: number, raio: number, espessura: number, de: number, ate: number): string {
  const angulo = (f: number) => f * 2 * Math.PI - Math.PI / 2
  const ponto = (r: number, f: number) => [cx + r * Math.cos(angulo(f)), cy + r * Math.sin(angulo(f))] as const
  const fim = Math.min(ate, de + 0.99999)
  const grande = fim - de > 0.5 ? 1 : 0
  const interno = raio - espessura
  const [x0, y0] = ponto(raio, de)
  const [x1, y1] = ponto(raio, fim)
  const [x2, y2] = ponto(interno, fim)
  const [x3, y3] = ponto(interno, de)
  return `M${x0},${y0}A${raio},${raio} 0 ${grande} 1 ${x1},${y1}L${x2},${y2}A${interno},${interno} 0 ${grande} 0 ${x3},${y3}Z`
}
