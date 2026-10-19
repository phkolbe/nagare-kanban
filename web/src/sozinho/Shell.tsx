/**
 * Shell do Nagare fora da plataforma: o trilho (Quadro, Projetos, Metricas, Simulador, Admin),
 * a barra de cima com a busca de quadros, tema, lingua, atalhos e o menu da pessoa. Usa as
 * mesmas classes do Shell da Pampufa, para as telas serem as mesmas nos dois modos. No alto do
 * trilho, o seletor de produto para quem tambem abre os produtos da Pampufa (18/10/2026).
 */

import { useEffect, useRef, useState, type ReactNode } from 'react'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Icone, MarcaProduto, Tag } from '@pampufa/ui'

import { PRODUTO_SOZINHO, useNagare } from '../app/contexto'
import { chaves, nagare } from '../api/nagare'
import type { Lista, Quadro } from '../api/tipos'
import { iniciaisDe } from '../features/quadro/CartaoMini'
import { useFormatoFerramentas, useLigadoNoNavegador } from '../features/quadro/visualizacao'
import { useT, type Chave } from '../i18n'
import { AjudaDeAtalhos, useAtalhos } from '../componentes/Atalhos'
import { SeletorIdioma } from '../componentes/SeletorIdioma'
import { useProdutoSozinho, type ProdutoDisponivel } from './produto'
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
  const { trocar: trocarProduto } = useProdutoSozinho()
  const navegar = useNavigate()
  const [menu, setMenu] = useState(false)
  const [tema, setTema] = useState<Tema>(temaGuardado)
  const [ajuda, setAjuda] = useState(false)
  const refMenu = useFecharFora<HTMLDivElement>(menu, () => setMenu(false))
  const [formatoFerramentas, trocarFormatoFerramentas] = useFormatoFerramentas()
  // Menu lateral recolhido so com os icones (19/10/2026), como o da Pampufa; lembrado no navegador.
  const [recolhido, setRecolhido] = useLigadoNoNavegador('nagare.menu.recolhido', false)
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
    <div className={`shell ng-sozinho${recolhido ? ' recolhido' : ''}`}>
      <aside className="side" aria-label={t('menu.principal')}>
        <SeletorProduto nomeEspaco={espaco.data?.name ?? t('menu.kanban')} />
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
                title={`${recolhido ? `${t(item.rotulo)}: ` : ''}${t(bloqueio === 'plano' ? 'menu.bloqueado_plano' : 'menu.bloqueado_papel')}`}
                aria-disabled="true"
              >
                {conteudo}
              </span>
            ) : (
              <NavLink key={item.caminho} to={`/${item.caminho}`} title={recolhido ? t(item.rotulo) : undefined}>
                {conteudo}
              </NavLink>
            )
          })}
        </nav>
        <button
          type="button"
          className="recolher"
          aria-expanded={!recolhido}
          onClick={() => setRecolhido(!recolhido)}
          title={recolhido ? t('menu.expandir') : t('menu.recolher')}
        >
          <Icone nome={recolhido ? 'expandir' : 'recolher'} />
          <span className="rotulo">{t('menu.recolher')}</span>
        </button>
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
                title={eu?.name}
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
                  <div className="ng-menu-preferencia">
                    <span className="mini">{t('quadro.formato_ferramentas')}</span>
                    <div className="abas ng-abas-mini" role="radiogroup" aria-label={t('quadro.formato_ferramentas')}>
                      {(['bar', 'panel'] as const).map((f) => (
                        <button
                          key={f}
                          type="button"
                          role="radio"
                          aria-checked={formatoFerramentas === f}
                          aria-selected={formatoFerramentas === f}
                          onClick={() => trocarFormatoFerramentas(f)}
                        >
                          {t(f === 'bar' ? 'quadro.formato_barra' : 'quadro.formato_painel')}
                        </button>
                      ))}
                    </div>
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
                      trocarProduto(PRODUTO_SOZINHO)
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
  const { produto } = useNagare()
  const quadros = useQuery({
    queryKey: chaves.quadros(produto),
    queryFn: () => nagare<Lista<Quadro>>(produto, '/boards'),
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

/**
 * O alto do trilho: a marca do Nagare e, quando ha mais de um produto que a pessoa abre, a troca
 * de produto. Num produto da Pampufa o administrator e o viewer so leem (o selo diz).
 */
function SeletorProduto({ nomeEspaco }: { nomeEspaco: string }) {
  const { t } = useT()
  const navegar = useNavigate()
  const { produto, trocar } = useProdutoSozinho()
  const [aberto, setAberto] = useState(false)
  const ref = useFecharFora<HTMLDivElement>(aberto, () => setAberto(false))
  const produtos = useQuery({
    queryKey: ['nagare', 'produtos'],
    queryFn: () => nagare<{ items: ProdutoDisponivel[] }>(PRODUTO_SOZINHO, '/integration/products'),
    staleTime: 60_000,
  })
  const lista = produtos.data?.items ?? []
  const atual = lista.find((p) => p.id === produto)

  // Produto guardado que a pessoa nao abre mais (outra conta, papel trocado): volta ao Nagare.
  useEffect(() => {
    if (produtos.isSuccess && !atual && produto !== PRODUTO_SOZINHO) trocar(PRODUTO_SOZINHO)
  }, [produtos.isSuccess, atual, produto, trocar])

  const nomeDe = (p: ProdutoDisponivel) => (p.id === PRODUTO_SOZINHO ? nomeEspaco : (p.project?.name ?? p.id))
  const escolher = (id: string) => {
    setAberto(false)
    if (id === produto) return
    trocar(id)
    navegar('/quadro')
  }
  const marca = (id: string, tamanho: number) =>
    id === PRODUTO_SOZINHO ? (
      <span className="mark ng-marca-glifo" style={{ width: tamanho, height: tamanho, fontSize: tamanho * 0.55 }} aria-hidden="true">
        流
      </span>
    ) : (
      <MarcaProduto produto={id} tamanho={tamanho} />
    )
  const somenteLeitura = produto !== PRODUTO_SOZINHO && atual?.role === 'viewer'

  if (lista.length <= 1) {
    return (
      <div className="seletor">
        <div className="seletor-botao ng-marca-trilho">
          {marca(PRODUTO_SOZINHO, 40)}
          <span className="rotulo">
            <b>Nagare</b>
            <span>{nomeEspaco}</span>
          </span>
        </div>
      </div>
    )
  }
  return (
    <div className="seletor" ref={ref}>
      <button
        type="button"
        className="seletor-botao"
        aria-label={t('produto.trocar_rotulo', { nome: atual ? nomeDe(atual) : nomeEspaco })}
        aria-haspopup="menu"
        aria-expanded={aberto}
        onClick={() => setAberto(!aberto)}
      >
        {marca(produto, 40)}
        <span className="rotulo">
          <b>{produto === PRODUTO_SOZINHO ? 'Nagare' : (atual?.project?.name ?? produto)}</b>
          <span>{produto === PRODUTO_SOZINHO ? nomeEspaco : somenteLeitura ? t('produto.so_leitura') : t('produto.da_pampufa')}</span>
        </span>
        <span className="rotulo seta">
          <Icone nome="trocar" tamanho={16} />
        </span>
      </button>
      {aberto && (
        <div className="popover seletor-lista" role="menu">
          <div className="popover-titulo">{t('produto.trocar')}</div>
          {lista.map((p) => (
            <button key={p.id} type="button" role="menuitem" onClick={() => escolher(p.id)}>
              {marca(p.id, 24)}
              <span>
                {nomeDe(p)}
                {p.id !== PRODUTO_SOZINHO && p.role === 'viewer' && <span className="mini"> · {t('produto.so_leitura')}</span>}
              </span>
              {p.id === produto && (
                <span className="marcado">
                  <Icone nome="check" tamanho={16} />
                </span>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
