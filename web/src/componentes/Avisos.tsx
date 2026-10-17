/** Avisos passageiros no canto da tela (resultado de mover, salvar, erro). */

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react'

type Tipo = 'ok' | 'info' | 'erro'

interface AvisoAtivo {
  id: number
  texto: string
  tipo: Tipo
}

const Contexto = createContext<((texto: string, tipo?: Tipo) => void) | null>(null)

let proximo = 1

export function ProvedorAvisos({ children }: { children: ReactNode }) {
  const [avisos, setAvisos] = useState<AvisoAtivo[]>([])
  const mostrar = useCallback((texto: string, tipo: Tipo = 'info') => {
    const id = proximo++
    setAvisos((atuais) => [...atuais.slice(-3), { id, texto, tipo }])
    window.setTimeout(() => setAvisos((atuais) => atuais.filter((a) => a.id !== id)), tipo === 'erro' ? 7000 : 4000)
  }, [])
  const valor = useMemo(() => mostrar, [mostrar])
  return (
    <Contexto.Provider value={valor}>
      {children}
      <div className="ng-avisos" aria-live="polite">
        {avisos.map((a) => (
          <div key={a.id} className={`ng-aviso ${a.tipo}`} role={a.tipo === 'erro' ? 'alert' : 'status'}>
            {a.texto}
          </div>
        ))}
      </div>
    </Contexto.Provider>
  )
}

export function useAvisos(): (texto: string, tipo?: Tipo) => void {
  const contexto = useContext(Contexto)
  if (!contexto) throw new Error('useAvisos fora do ProvedorAvisos')
  return contexto
}
