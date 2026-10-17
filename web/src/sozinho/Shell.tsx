/**
 * Shell do Nagare fora da plataforma: o trilho (Quadro, Projetos, Metricas, Simulador, Admin),
 * a barra de cima com a busca de quadros, tema, lingua, atalhos e o menu da pessoa. Usa as
 * mesmas classes do Shell da Pampufa, para as telas serem as mesmas nos dois modos.
 */

import { useEffect, useRef, useState, type ReactNode } from 'react'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Icone, Tag } from '@pampufa/ui'

import { PRODUTO_SOZINHO } from '../app/contexto'
import { chaves, nagare } from '../api/nagare'
import type { Lista, Quadro } from '../api/tipos'
import { iniciaisDe } from '../features/quadro/CartaoMini'
import { useT, type Chave } from '../i18n'
import { AjudaDeAtalhos, useAtalhos } from '../componentes/Atalhos'
import { SeletorIdioma } from '../componentes/SeletorIdioma'
import { useSessao } from './sessao'
import { aplicarTema, temaGuardado, type Tema } from './tema'
import { TRILHO, bloqueioDoItem } from './trilho'

interface Espaco {
  name: string
  plan?: { name: string; slug: string } | null
}

function useFecharFora<T extends HTMLElement>(aberto: boolean, fechar: () => void) {
  const ref = useRef<T>(null)
  useEffect(() => {
    if (!aberto) return
    const clique = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && fechar()
    document.addEventListener('mousedown', clique)
    return () => document.removeEventListener('mousedown', clique)
  }, [aberto, fechar])
  return ref
}

export function ShellSozinho() {
  const { t, eu } = useT()
  const { sair } = useSessao()
  const navegar = useNavigate()
  const [menu, setMenu] = useState(false)
  const [tema, setTema] = useState<Tema>(temaGuardado)
  const [ajuda, setAjuda] = useState(false)
  const refMenu = useFecharFora<HTMLDivElement>(menu, () => setMenu(false))
  const espaco = useQuery({
    queryKey: ['nagare', PRODUTO_SOZINHO, 'espaco'],
    queryFn: () => nagare<Espaco>(PRODUTO_SOZINHO, '/workspace'),
    staleTime: 60_000,
  })
  useAtalhos({ abrirAjuda: () => setAjuda(true) })

  const flags = eu?.flags ?? {}
  const capacidades = eu?.capabilities ?? {}
  const trocarTema = (novo: Tema) => {
    setTema(novo)
    aplicarTema(novo)
  }

  return (
    <div className="shell ng-sozinho">
      <aside className="side" aria-label={t('menu.principal')}>
        <div className="seletor">
          <div className="seletor-botao ng-marca-trilho">
            <span className="mark ng-marca-glifo" aria-hidden="true">
              流
            </span>
            <span className="rotulo">
              <b>Nagare</b>
              <span>{espaco.data?.name ?? t('menu.kanban')}</span>
            </span>
          </div>
        </div>
        <nav>
          {TRILHO.map((item) => {
            const bloqueio = bloqueioDoItem(item, flags, capacidades)
            const conteudo: ReactNode = (
              <>
                <Icone nome={item.icone} />
                <span className="rotulo">{t(item.rotulo)}</span>
                {bloqueio && (
                  <span className="rotulo ng-cadeado" aria-hidden="true">
                    🔒
                  </span>
                )}
              </>
            )
            return bloqueio ? (
              <span
                key={item.caminho}
                className="ng-item-bloqueado"
                title={t(bloqueio === 'plano' ? 'menu.bloqueado_plano' : 'menu.bloqueado_papel')}
                aria-disabled="true"
              >
                {conteudo}
              </span>
            ) : (
              <NavLink key={item.caminho} to={`/${item.caminho}`}>
                {conteudo}
              </NavLink>
            )
          })}
        </nav>
        <div className="foot">
          {espaco.data?.plan && <Tag cor="ouro">{espaco.data.plan.name}</Tag>}
        </div>
      </aside>
      <div className="main">
        <header className="topbar">
          <BuscaDeQuadros />
          <div className="direita ng-topo-acoes">
            <label className="ng-idioma">
              <span className="visualmente-oculto">{t('tema.rotulo')}</span>
              <select value={tema} aria-label={t('tema.rotulo')} onChange={(e) => trocarTema(e.target.value as Tema)}>
                <option value="system">{t('tema.sistema')}</option>
                <option value="light">{t('tema.claro')}</option>
                <option value="dark">{t('tema.escuro')}</option>
              </select>
            </label>
            <SeletorIdioma />
            <button type="button" className="btn sm ghost" aria-label={t('atalhos.titulo')} onClick={() => setAjuda(true)}>
              ?
            </button>
            <div className="ng-menu-pessoa" ref={refMenu}>
              <button
                type="button"
                className="ng-avatar ng-avatar-grande"
                aria-haspopup="menu"
                aria-expanded={menu}
                aria-label={t('menu.pessoa')}
                onClick={() => setMenu(!menu)}
              >
                {iniciaisDe(eu?.name ?? '?')}
              </button>
              {menu && (
                <div className="popover ng-menu-pop" role="menu">
                  <div className="popover-titulo">
                    {eu?.name}
                    <div className="mini">{eu?.email}</div>
                  </div>
                  <div className="ng-menu-tags">
                    {eu && <Tag cor="azul">{t(`papel.${eu.role}` as Chave)}</Tag>}
                    {espaco.data?.plan && <Tag cor="ouro">{espaco.data.plan.name}</Tag>}
                  </div>
                  {capacidades.manage_users && (
                    <button type="button" role="menuitem" onClick={() => navegar('/admin/usuarios')}>
                      {t('admin.usuarios')}
                    </button>
                  )}
                  {capacidades.manage_plans_flags && (
                    <>
                      <button type="button" role="menuitem" onClick={() => navegar('/admin/flags')}>
                        {t('admin.flags')}
                      </button>
                      <button type="button" role="menuitem" onClick={() => navegar('/admin/planos')}>
                        {t('admin.planos')}
                      </button>
                    </>
                  )}
                  <button type="button" role="menuitem" onClick={() => setAjuda(true)}>
                    {t('atalhos.titulo')}
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      sair()
                      navegar('/entrar', { replace: true })
                    }}
                  >
                    {t('menu.sair')}
                  </button>
                </div>
              )}
            </div>
          </div>
        </header>
        <main className="content" id="conteudo">
          <Outlet />
        </main>
      </div>
      {ajuda && <AjudaDeAtalhos fechar={() => setAjuda(false)} />}
    </div>
  )
}

/** Busca global da barra de cima: encontra um quadro pelo nome e abre (`/` foca). */
function BuscaDeQuadros() {
  const { t } = useT()
  const navegar = useNavigate()
  const [texto, setTexto] = useState('')
  const quadros = useQuery({
    queryKey: chaves.quadros(PRODUTO_SOZINHO),
    queryFn: () => nagare<Lista<Quadro>>(PRODUTO_SOZINHO, '/boards'),
    staleTime: 30_000,
  })
  const achados = texto.trim()
    ? (quadros.data?.items ?? []).filter((q) => q.name.toLowerCase().includes(texto.trim().toLowerCase())).slice(0, 8)
    : []
  return (
    <div className="ng-busca-global">
      <input
        type="search"
        data-busca-global
        className="ng-busca"
        placeholder={t('menu.buscar_quadro')}
        aria-label={t('menu.buscar_quadro')}
        value={texto}
        onChange={(e) => setTexto(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Escape') setTexto('')
          if (e.key === 'Enter' && achados[0]) {
            navegar(`/${achados[0].kind === 'simulator' ? 'simulador' : 'quadro'}/${achados[0].id}`)
            setTexto('')
          }
        }}
      />
      {achados.length > 0 && (
        <ul className="popover ng-busca-pop" role="listbox">
          {achados.map((q) => (
            <li key={q.id}>
              <button
                type="button"
                onClick={() => {
                  navegar(`/${q.kind === 'simulator' ? 'simulador' : 'quadro'}/${q.id}`)
                  setTexto('')
                }}
              >
                {q.name}
                {q.kind === 'simulator' && <span className="mini"> · {t('menu.simulador')}</span>}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
