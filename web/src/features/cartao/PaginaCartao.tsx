/**
 * A pagina propria do cartao (decisao de 24/11/2026), pelo endereco da chave:
 * `/nagare/card/PPLAT-404`, `/nagare/sr-adv/card/SRADV-101` (produto da Pampufa aberto pelo
 * Nagare sozinho) e `/grupo-sr/sr-adv/tarefas/cartao/SRADV-101` (modulo Tarefas).
 *
 * O conteudo e o mesmo da gaveta (`ConteudoDoCartao`), numa tela inteira com o quadro de origem
 * indicado e o caminho de volta (ao quadro como estava, se veio da gaveta; senao, ao quadro do
 * cartao). Mover segue as mesmas regras do quadro: so para frente, WIP, confirmar se o quadro pede.
 */

import { useEffect, useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { Carregando, ErroDeApi, Vazio } from '@pampufa/ui'

import { useNagare } from '../../app/contexto'
import type { Cartao, CartaoDetalhe, Coluna, SegmentoVisao } from '../../api/tipos'
import { useAvisos } from '../../componentes/Avisos'
import { useT } from '../../i18n'
import { useAlterarQuadro, useMover, useQuadro, useVisao } from '../quadro/consultas'
import { FILTROS_VAZIOS, ordenarColunas, validarDestino } from '../quadro/regras'
import { ConfirmarMovimento } from '../quadro/TelaQuadro'
import { useCartao } from './acoes'
import { ConteudoDoCartao, CopiarLink } from './GavetaCartao'

export function PaginaCartao() {
  const { chave = '' } = useParams()
  const { t } = useT()
  const pelaChave = useCartao(chave)
  if (pelaChave.isPending) return <Carregando texto={t('comum.carregando')} />
  if (pelaChave.isError) {
    if ((pelaChave.error as { status?: number }).status === 404) return <CartaoNaoEncontrado chave={chave} />
    return <ErroDeApi erro={pelaChave.error} />
  }
  return <CartaoNaPagina key={pelaChave.data.id} inicial={pelaChave.data} />
}

function CartaoNaoEncontrado({ chave, texto }: { chave: string; texto?: string }) {
  const { t } = useT()
  const { rota } = useNagare()
  return (
    <section className="card">
      <Vazio titulo={texto ?? t('cartao.nao_encontrado', { chave })}>
        <Link className="btn sm" to={rota('quadro')}>
          {t('comum.ir_ao_quadro')}
        </Link>
      </Vazio>
    </section>
  )
}

function CartaoNaPagina({ inicial }: { inicial: CartaoDetalhe }) {
  const { t, eu } = useT()
  const { rota } = useNagare()
  const local = useLocation()
  const navegar = useNavigate()
  const avisar = useAvisos()
  const cartao = useCartao(inicial.id, inicial)
  const c = cartao.data ?? inicial
  const flags = eu?.flags ?? {}
  // O mesmo segmento que o quadro abre por padrao: a visao (WIP, colunas) vem do mesmo cache.
  const segmentosLigados: SegmentoVisao[] = [
    ...(flags.unified_flow !== false ? (['unified'] as const) : []),
    ...(flags.upstream_board !== false ? (['upstream'] as const) : []),
    ...(flags.downstream_board !== false ? (['downstream'] as const) : []),
  ]
  const quadro = useQuadro(c.board_id)
  const visao = useVisao(c.board_id, segmentosLigados[0] ?? 'unified', FILTROS_VAZIOS)
  const mover = useMover(c.board_id)
  const alterarQuadro = useAlterarQuadro(c.board_id)
  const [confirmar, setConfirmar] = useState<{ cartao: Cartao; destino: Coluna } | null>(null)

  useEffect(() => {
    const antes = document.title
    document.title = `${c.key} · ${c.envelope_state === 'sealed' ? t('cartao.lacrado') : c.title}`
    return () => {
      document.title = antes
    }
  }, [c.key, c.title, c.envelope_state, t])

  if (quadro.isPending || (visao.isPending && !visao.data)) return <Carregando texto={t('comum.carregando')} />
  if (quadro.isError) return <ErroDeApi erro={quadro.error} />
  if (visao.isError) return <ErroDeApi erro={visao.error} />
  const q = quadro.data
  const v = visao.data!
  const colunas = ordenarColunas(q.columns ?? v.columns)
  const podeEditar = Boolean(eu?.capabilities.manage_cards) && !q.read_only && q.kind === 'operational'
  const deOnde = (local.state as { de?: unknown } | null)?.de
  const voltar = typeof deOnde === 'string' && deOnde ? deOnde : rota(`quadro/${q.id}`)

  const pedirMovimento = (alvo: Cartao, destinoId: string) => {
    const destino = colunas.find((x) => x.id === destinoId)
    if (!destino) return
    const veredito = validarDestino(alvo, destinoId, colunas, v.wip)
    // Como no quadro: WIP cheio vai ao servidor (a vaga pode ter liberado); o resto a tela ja barra.
    const doWip = veredito.motivo === 'mover.coluna_cheia' || veredito.motivo === 'mover.grupo_cheio'
    if (veredito.estado === 'invalido' && veredito.motivo && !doWip) {
      avisar(t(veredito.motivo), 'erro')
      return
    }
    if (q.confirm_moves) setConfirmar({ cartao: alvo, destino })
    else mover.mutate({ cartao: alvo, destino: destinoId })
  }

  return (
    <div className="ng-pagina-cartao">
      <nav className="ng-pagina-cartao-trilha" aria-label={t('cartao.no_quadro', { quadro: q.name })}>
        <Link className="btn sm ghost" to={voltar}>
          <span aria-hidden="true">←</span> {t('cartao.voltar_quadro', { quadro: q.name })}
        </Link>
        <span className="ng-espaco" />
        <span className="mini">{t('cartao.pagina_kicker')}</span>
        <span className="ng-chave">{c.key}</span>
      </nav>
      <section className="card ng-pagina-cartao-corpo">
        <ConteudoDoCartao
          cartao={c}
          quadro={q}
          colunas={colunas}
          wip={v.wip}
          podeEditar={podeEditar}
          mover={pedirMovimento}
          arquivado={() => navegar(voltar)}
          links={<CopiarLink chave={c.key} />}
          naPagina
        />
      </section>
      {confirmar && (
        <ConfirmarMovimento
          cartao={confirmar.cartao}
          destino={confirmar.destino}
          fechar={() => setConfirmar(null)}
          confirmar={(naoPerguntar) => {
            const { cartao: alvo, destino } = confirmar
            setConfirmar(null)
            if (naoPerguntar) alterarQuadro.mutate({ confirm_moves: false })
            mover.mutate({ cartao: alvo, destino: destino.id })
          }}
        />
      )}
    </div>
  )
}

/**
 * Nagare sozinho, `/nagare/<produto>/card/<chave>`: o link leva o produto, para abrir no produto
 * certo mesmo com outro escolhido no seletor. Troca o produto uma vez; se a pessoa nao abre esse
 * produto (o trilho volta ao `nagare`), diz isso em vez de procurar a chave no produto errado.
 */
export function PaginaCartaoDoProduto({
  produtoAtual,
  trocar,
}: {
  produtoAtual: string
  trocar: (produto: string) => void
}) {
  const { produto = '', chave = '' } = useParams()
  const { t } = useT()
  const [tentou, setTentou] = useState(false)
  useEffect(() => {
    if (produto && produto !== produtoAtual && !tentou) {
      setTentou(true)
      trocar(produto)
    }
  }, [produto, produtoAtual, tentou, trocar])
  if (produto === produtoAtual) return <PaginaCartao />
  if (!tentou) return <Carregando texto={t('comum.carregando')} />
  return <CartaoNaoEncontrado chave={chave} texto={t('cartao.produto_sem_acesso', { produto, chave })} />
}
