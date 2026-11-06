/** Horas (GJ-4), em funcoes puras chamadas por `checks/telas.check.ts`. */

/** 90 -> "1h30"; 60 -> "1h"; 45 -> "45min". */
export function horasEmTexto(minutos: number): string {
  const h = Math.floor(minutos / 60)
  const m = minutos % 60
  if (h === 0) return `${m}min`
  return m === 0 ? `${h}h` : `${h}h${String(m).padStart(2, '0')}`
}

/** O que a pessoa digita -> minutos: "1:30", "1h30", "1,5", "1.5" (horas) ou "45min". Invalido: null. */
export function minutosDeTexto(texto: string): number | null {
  const t = texto.trim().toLowerCase().replace(/\s+/g, '')
  if (!t) return null
  let minutos: number | null = null
  let m = /^(\d{1,2})[:h](\d{1,2})?$/.exec(t)
  if (m) minutos = Number(m[1]) * 60 + Number(m[2] ?? 0)
  else if ((m = /^(\d{1,4})min$/.exec(t))) minutos = Number(m[1])
  else if ((m = /^(\d{1,2})([.,]\d{1,2})?$/.exec(t))) minutos = Math.round(Number(`${m[1]}${(m[2] ?? '').replace(',', '.')}`) * 60)
  return minutos !== null && minutos > 0 && minutos <= 1440 ? minutos : null
}
