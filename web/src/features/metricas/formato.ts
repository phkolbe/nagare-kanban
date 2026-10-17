/** Formatos das metricas (puro: conferido em `npm run check`). */

/** O valor de um indicador pronto para o quadrinho: numero e unidade. */
export function formatarIndicador(
  valor: number | null | undefined,
  unidade: string,
  numero: (v: number, casas?: number) => string,
  rotulos: { dias: string; porSemana: string } = { dias: 'd', porSemana: '/sem' },
): { valor: string; unidade: string } {
  if (valor === null || valor === undefined || !Number.isFinite(valor)) return { valor: '—', unidade: '' }
  switch (unidade) {
    case 'percent':
      return { valor: `${numero(valor, 0)}%`, unidade: '' }
    case 'days':
      return { valor: numero(valor, valor < 10 ? 1 : 0), unidade: rotulos.dias }
    case 'cards/week':
      return { valor: numero(valor, valor < 10 ? 1 : 0), unidade: rotulos.porSemana }
    case 'ratio':
      return { valor: numero(valor, 2), unidade: '×' }
    case 'count':
      return { valor: numero(valor, 0), unidade: '' }
    default:
      return { valor: numero(valor, 1), unidade }
  }
}

/** O valor de uma metrica do KMM em texto (percentis, mix, contagens, objetos nomeados). */
export function valorKmm(valor: unknown, numero: (v: number, casas?: number) => string): string {
  if (valor === null || valor === undefined) return '—'
  if (typeof valor === 'number') return numero(valor, Number.isInteger(valor) ? 0 : 1)
  if (typeof valor === 'boolean') return valor ? '✓' : '✗'
  if (typeof valor === 'string') return valor
  if (Array.isArray(valor)) {
    return valor
      .slice(0, 4)
      .map((item) => {
        if (item && typeof item === 'object') {
          const o = item as Record<string, unknown>
          const nome = o.label ?? o.name ?? o.key ?? ''
          const quanto = o.pct ?? o.count ?? o.value
          return `${String(nome)}${quanto != null ? ` ${typeof quanto === 'number' ? numero(quanto, 0) : String(quanto)}${o.pct != null ? '%' : ''}` : ''}`
        }
        return String(item)
      })
      .join(' · ')
  }
  const o = valor as Record<string, unknown>
  const percentis = ['p50', 'p70', 'p85', 'p95'].filter((k) => typeof o[k] === 'number')
  if (percentis.length > 0) return percentis.map((k) => `P${k.slice(1)} ${numero(o[k] as number, 1)}`).join(' · ')
  if (typeof o.value === 'number') return numero(o.value, 1)
  if (typeof o.count === 'number') return numero(o.count, 0)
  if (o.name || o.label) return String(o.name ?? o.label)
  return Object.entries(o)
    .slice(0, 3)
    .map(([k, v]) => `${k} ${typeof v === 'number' ? numero(v, 1) : String(v)}`)
    .join(' · ')
}
