/**
 * A página do cliente de um relatório emitido (Reports, onda 2, 25/11/2026; decisões R5, R18, R19):
 * `/nagare/r/<token>`, sem menu e sem login.
 *
 * Mostra só o emitido do link (o Documento congelado), na língua em que foi emitido, com a faixa
 * "Emitido em … por …" e o aviso de validade. Sem links para os cartões (o cliente não entra no
 * quadro). Boa para imprimir: o navegador salva em PDF. A página não é indexada (`noindex`, aqui e
 * no cabeçalho que o servidor manda) e o pedido não leva o token de quem estiver logado.
 */

import { useEffect } from 'react'
import { useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Aviso, Carregando, ErroApi } from '@pampufa/ui'

import { PRODUTO_SOZINHO, ProvedorNagare } from '../../app/contexto'
import { requisitarPublico } from '../../api/sessaoLocal'
import { ProvedorIdiomaFixo, useT, type Chave } from '../../i18n'
import { Documento } from './Documento'
import { idiomaDoNavegador, situacaoDoErroPublico, type RelatorioPublico, type SituacaoDaPaginaPublica } from './emitidos'
import { FaixaDoEmitido } from './TelaEmitidos'

const TEXTO_DO_ERRO: Record<SituacaoDaPaginaPublica, Chave> = {
  vencido: 'cliente.vencido',
  revogado: 'cliente.revogado',
  inexistente: 'cliente.inexistente',
  limite: 'cliente.limite',
  erro: 'cliente.erro',
}

function idiomaGuardado(): string | null {
  try {
    return window.localStorage.getItem('nagare.idioma')
  } catch {
    return null
  }
}

/** `noindex` também no `<head>`, para quem lê a página já desenhada. */
function useSemIndexar(titulo: string | undefined) {
  useEffect(() => {
    const meta = document.createElement('meta')
    meta.name = 'robots'
    meta.content = 'noindex, nofollow'
    document.head.appendChild(meta)
    const referencia = document.createElement('meta')
    referencia.name = 'referrer'
    referencia.content = 'no-referrer'
    document.head.appendChild(referencia)
    return () => {
      meta.remove()
      referencia.remove()
    }
  }, [])
  useEffect(() => {
    if (titulo) document.title = titulo
  }, [titulo])
}

export function PaginaDoCliente() {
  const { token = '' } = useParams()
  const relatorio = useQuery({
    queryKey: ['nagare', 'publico', token],
    queryFn: () => requisitarPublico<RelatorioPublico>(`/public/reports/${encodeURIComponent(token)}`),
    // Cada pedido conta um acesso no link: nada de pedir de novo sozinho.
    retry: false,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  })
  useSemIndexar(relatorio.data ? `${relatorio.data.document.code} · ${relatorio.data.document.title}` : undefined)
  const idioma = relatorio.data?.locale ?? idiomaDoNavegador(idiomaGuardado(), typeof navigator === 'undefined' ? undefined : navigator.language)

  return (
    <ProvedorIdiomaFixo idioma={idioma}>
      <main className="ng-cliente">
        {relatorio.isPending ? (
          <Carregando texto="…" />
        ) : relatorio.isError ? (
          <ErroDaPagina erro={relatorio.error} />
        ) : (
          <ProvedorNagare modo="sozinho" produto={relatorio.data.document.product_id ?? PRODUTO_SOZINHO} nomeProduto="Nagare">
            <Conteudo dados={relatorio.data} />
          </ProvedorNagare>
        )}
      </main>
    </ProvedorIdiomaFixo>
  )
}

function ErroDaPagina({ erro }: { erro: unknown }) {
  const { t } = useT()
  const situacao = erro instanceof ErroApi ? situacaoDoErroPublico(erro.status, erro.codigo) : 'erro'
  return <Aviso tipo={situacao === 'limite' || situacao === 'erro' ? 'alerta' : 'erro'}>{t(TEXTO_DO_ERRO[situacao])}</Aviso>
}

function Conteudo({ dados }: { dados: RelatorioPublico }) {
  const { t, dataHora } = useT()
  return (
    <>
      <div className="ng-cliente-topo ng-relatorio-controles">
        <p className="ng-cliente-validade">{t('cliente.validade', { quando: dataHora(dados.expires_at) })}</p>
        <button type="button" className="btn sm" onClick={() => window.print()}>
          {t('cliente.imprimir')}
        </button>
      </div>
      <FaixaDoEmitido quando={dados.issued_at} quem={dados.issued_by_name} />
      <Documento doc={dados.document} publico />
    </>
  )
}
