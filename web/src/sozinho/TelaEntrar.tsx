/** Entrada do Nagare fora da plataforma (login local), como a tela antiga. */

import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ErroApi } from '@pampufa/ui'

import type { Idioma } from '../api/tipos'
import { traducao } from '../i18n/texto'
import { CHAVE_VOLTA, useSessao } from './sessao'

export const CHAVE_IDIOMA = 'nagare.idioma'

/** A ultima lingua escolhida neste navegador; sem escolha, o ingles do Nagare sozinho. */
export function idiomaGuardado(): Idioma {
  try {
    return window.localStorage.getItem(CHAVE_IDIOMA) === 'pt-BR' ? 'pt-BR' : 'en'
  } catch {
    return 'en'
  }
}

export function TelaEntrar() {
  const { entrar } = useSessao()
  const navegar = useNavigate()
  const { t } = traducao(idiomaGuardado())
  const [email, setEmail] = useState('master@nagare.app')
  const [senha, setSenha] = useState('')
  const [erro, setErro] = useState<string | null>(null)
  const [enviando, setEnviando] = useState(false)

  const enviar = async () => {
    if (!email.trim() || !senha) return setErro(t('entrar.vazio'))
    setEnviando(true)
    setErro(null)
    try {
      await entrar(email.trim(), senha)
      let volta = '/quadro'
      try {
        volta = window.sessionStorage.getItem(CHAVE_VOLTA) ?? volta
        window.sessionStorage.removeItem(CHAVE_VOLTA)
      } catch {
        /* sem armazenamento */
      }
      navegar(volta.startsWith('/') && !volta.startsWith('//') ? volta : '/quadro', { replace: true })
    } catch (e) {
      if (e instanceof ErroApi && e.status === 401) setErro(t('entrar.invalido'))
      else if (e instanceof ErroApi && e.status === 429) setErro(t('entrar.bloqueado'))
      else if (e instanceof ErroApi) setErro(e.message)
      else setErro(t('erro.rede'))
    } finally {
      setEnviando(false)
    }
  }

  return (
    <div className="tela-login ng-entrar">
      <form
        className="card"
        onSubmit={(e) => {
          e.preventDefault()
          void enviar()
        }}
      >
        <div className="ng-marca">
          <span className="ng-marca-glifo" aria-hidden="true">
            流
          </span>
          <div>
            <h1>Nagare</h1>
            <p className="mini">{t('entrar.subtitulo')}</p>
          </div>
        </div>
        <label className="campo">
          <span>{t('entrar.email')}</span>
          <input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label className="campo">
          <span>{t('entrar.senha')}</span>
          <input
            type="password"
            autoComplete="current-password"
            autoFocus
            value={senha}
            onChange={(e) => setSenha(e.target.value)}
          />
        </label>
        {erro && (
          <p className="erro-campo" role="alert">
            {erro}
          </p>
        )}
        <button className="btn" type="submit" disabled={enviando}>
          {t('entrar.botao')}
        </button>
        <p className="mini ng-dica-demo">{t('entrar.demo')}</p>
      </form>
    </div>
  )
}
