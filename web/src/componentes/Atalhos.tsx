/**
 * Atalhos de teclado das telas do Nagare (paridade com a tela antiga): `g` e depois `b`/`p`/`m`/
 * `s`/`a` para ir ao quadro, projetos, metricas, simulador e admin; `/` busca; `?` ajuda.
 * Valem nos dois modos; digitando num campo, nao valem.
 */

import { useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'

import { useNagare } from '../app/contexto'
import { useT, type Chave } from '../i18n'
import { Janela } from './Sobreposicao'

/** Destinos do `g`. */
export const IR_PARA: Record<string, string> = {
  b: 'quadro',
  p: 'projetos',
  m: 'metricas',
  s: 'simulador',
  a: 'admin',
}

/** Telas que so existem no Nagare sozinho: simulador e admin nao entram na plataforma. */
export const SO_NO_NAGARE = new Set(['simulador', 'admin'])

export function digitando(alvo: EventTarget | null): boolean {
  const el = alvo as HTMLElement | null
  if (!el) return false
  return el.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName)
}

export function useAtalhos({ abrirAjuda }: { abrirAjuda: () => void }) {
  const navegar = useNavigate()
  const { rota, modo } = useNagare()
  const esperandoG = useRef<number | null>(null)
  useEffect(() => {
    const tecla = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey || digitando(e.target)) return
      if (esperandoG.current !== null) {
        window.clearTimeout(esperandoG.current)
        esperandoG.current = null
        const destino = IR_PARA[e.key]
        if (destino && !(SO_NO_NAGARE.has(destino) && modo === 'plataforma')) {
          e.preventDefault()
          navegar(rota(destino))
        }
        return
      }
      if (e.key === 'g') {
        esperandoG.current = window.setTimeout(() => (esperandoG.current = null), 1400)
      } else if (e.key === '/') {
        const busca = document.querySelector<HTMLInputElement>('[data-busca-global], [data-busca]')
        if (busca) {
          e.preventDefault()
          busca.focus()
        }
      } else if (e.key === '?') {
        e.preventDefault()
        abrirAjuda()
      }
    }
    window.addEventListener('keydown', tecla)
    return () => window.removeEventListener('keydown', tecla)
  }, [navegar, rota, modo, abrirAjuda])
}

const LINHAS: [string, Chave][] = [
  ['g b', 'atalhos.ir_quadro'],
  ['g p', 'atalhos.ir_projetos'],
  ['g m', 'atalhos.ir_metricas'],
  ['g s', 'atalhos.ir_simulador'],
  ['g a', 'atalhos.ir_admin'],
  ['/', 'atalhos.buscar'],
  ['?', 'atalhos.ajuda'],
  ['Esc', 'atalhos.fechar'],
  ['Enter', 'atalhos.abrir_cartao'],
  ['m', 'atalhos.mover_cartao'],
  ['← ↑ → ↓', 'atalhos.navegar_cartoes'],
]

/** `g s`, `g a`: atalhos de telas que nao existem na plataforma. */
export function ehSoDoNagare(teclas: string): boolean {
  const [g, tecla] = teclas.split(' ')
  return g === 'g' && tecla !== undefined && SO_NO_NAGARE.has(IR_PARA[tecla] ?? '')
}

export function AjudaDeAtalhos({ fechar }: { fechar: () => void }) {
  const { t } = useT()
  const { modo } = useNagare()
  return (
    <Janela titulo={t('atalhos.titulo')} fechar={fechar} largura={460}>
      <table className="ng-tabela">
        <tbody>
          {LINHAS.filter(([teclas]) => !(modo === 'plataforma' && ehSoDoNagare(teclas))).map(([teclas, chave]) => (
            <tr key={teclas}>
              <td>
                <kbd>{teclas}</kbd>
              </td>
              <td>{t(chave)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Janela>
  )
}
