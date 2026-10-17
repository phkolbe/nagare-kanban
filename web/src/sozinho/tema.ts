/**
 * Tema do Nagare fora da plataforma (decisao de 17/10/2026): claro, escuro ou seguir o sistema,
 * guardado no navegador (`nagare.theme`, como a tela antiga). Dentro da Pampufa vale o visual
 * claro da plataforma. O `index.html` aplica o tema antes da primeira pintura (sem piscar).
 */

export type Tema = 'light' | 'dark' | 'system'

export const CHAVE_TEMA = 'nagare.theme'

export function temaGuardado(): Tema {
  try {
    const valor = window.localStorage.getItem(CHAVE_TEMA)
    return valor === 'light' || valor === 'dark' ? valor : 'system'
  } catch {
    return 'system'
  }
}

export function temaEfetivo(tema: Tema, sistemaEscuro: boolean): 'light' | 'dark' {
  return tema === 'system' ? (sistemaEscuro ? 'dark' : 'light') : tema
}

let ouvinte: ((e: MediaQueryListEvent) => void) | null = null

/** Aplica e guarda; no modo sistema, segue a troca do sistema operacional na hora. */
export function aplicarTema(tema: Tema): void {
  try {
    window.localStorage.setItem(CHAVE_TEMA, tema)
  } catch {
    /* sem armazenamento: vale nesta pagina */
  }
  const consulta = window.matchMedia('(prefers-color-scheme: dark)')
  if (ouvinte) consulta.removeEventListener('change', ouvinte)
  ouvinte = null
  document.documentElement.dataset.theme = temaEfetivo(tema, consulta.matches)
  if (tema === 'system') {
    ouvinte = (e) => {
      document.documentElement.dataset.theme = e.matches ? 'dark' : 'light'
    }
    consulta.addEventListener('change', ouvinte)
  }
}
