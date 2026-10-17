import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useProduto } from '@pampufa/ui'

import { chaves, consulta, nagare } from '../../api/nagare'
import type {
  Cartao,
  Lista,
  Pessoas,
  Projeto,
  Quadro,
  ResultadoMovimento,
  SegmentoVisao,
  Vinculo,
  VisaoQuadro,
} from '../../api/tipos'
import { useAvisos } from '../../componentes/Avisos'
import { mensagemDeErro, useT } from '../../i18n'
import { parametrosDaVisao, type Filtros } from './regras'

export function useQuadros() {
  const produto = useProduto().id
  return useQuery({
    queryKey: chaves.quadros(produto),
    queryFn: () => nagare<Lista<Quadro>>(produto, '/boards?kind=operational'),
    staleTime: 30_000,
  })
}

export function useQuadro(id: string) {
  const produto = useProduto().id
  return useQuery({
    queryKey: chaves.quadro(produto, id),
    queryFn: () => nagare<Quadro>(produto, `/boards/${encodeURIComponent(id)}`),
  })
}

export function useVisao(id: string, segmento: SegmentoVisao, filtros: Filtros) {
  const produto = useProduto().id
  const busca = consulta({ segment: segmento, ...parametrosDaVisao(filtros) })
  return useQuery({
    queryKey: chaves.visao(produto, id, busca),
    queryFn: () => nagare<VisaoQuadro>(produto, `/boards/${encodeURIComponent(id)}/view${busca}`),
    placeholderData: (anterior) => anterior,
  })
}

export function useVinculo() {
  const produto = useProduto().id
  return useQuery({
    queryKey: chaves.vinculo(produto),
    queryFn: () => nagare<Vinculo>(produto, '/integration/product'),
    staleTime: 60_000,
  })
}

export function useProjetos() {
  const produto = useProduto().id
  return useQuery({
    queryKey: chaves.projetos(produto),
    queryFn: () => nagare<Lista<Projeto>>(produto, '/projects'),
    staleTime: 30_000,
  })
}

/** Quem pode receber cartao no produto: as pessoas `nagare.*` da Pampufa (17/10/2026). */
export function usePessoas() {
  const produto = useProduto().id
  return useQuery({
    queryKey: chaves.pessoas(produto),
    queryFn: () => nagare<Pessoas>(produto, '/integration/people'),
    staleTime: 60_000,
  })
}

/** Refaz o que um cartao mexido muda: as visoes do quadro, o cartao e os projetos. */
export function useRecarregarQuadro() {
  const produto = useProduto().id
  const queryClient = useQueryClient()
  return (quadroId: string, cartaoId?: string) => {
    void queryClient.invalidateQueries({ queryKey: chaves.visoes(produto, quadroId) })
    void queryClient.invalidateQueries({ queryKey: chaves.quadro(produto, quadroId) })
    void queryClient.invalidateQueries({ queryKey: chaves.projetos(produto) })
    if (cartaoId) void queryClient.invalidateQueries({ queryKey: chaves.cartao(produto, cartaoId) })
  }
}

/**
 * Move um cartao: a tela ja mostra no destino (otimista) e desfaz se o servidor recusar
 * (WIP cheio, para tras, envelope lacrado...), com a mensagem na lingua da tela.
 */
export function useMover(quadroId: string) {
  const produto = useProduto().id
  const queryClient = useQueryClient()
  const avisar = useAvisos()
  const { t } = useT()
  const recarregar = useRecarregarQuadro()
  return useMutation({
    mutationFn: ({ cartao, destino }: { cartao: Cartao; destino: string }) =>
      nagare<ResultadoMovimento>(produto, `/cards/${cartao.id}/move`, {
        metodo: 'POST',
        corpo: { to_column_id: destino },
      }),
    onMutate: async ({ cartao, destino }) => {
      await queryClient.cancelQueries({ queryKey: chaves.visoes(produto, quadroId) })
      const antes = queryClient.getQueriesData<VisaoQuadro>({ queryKey: chaves.visoes(produto, quadroId) })
      queryClient.setQueriesData<VisaoQuadro>({ queryKey: chaves.visoes(produto, quadroId) }, (visao) =>
        visao
          ? { ...visao, cards: visao.cards.map((c) => (c.id === cartao.id ? { ...c, column_id: destino } : c)) }
          : visao,
      )
      return { antes }
    },
    onError: (erro, _variaveis, contexto) => {
      for (const [chave, dados] of contexto?.antes ?? []) queryClient.setQueryData(chave, dados)
      avisar(mensagemDeErro(erro, t), 'erro')
    },
    onSuccess: (resultado, { cartao }) => {
      if (resultado.delivered) avisar(t('mover.entregue', { chave: cartao.key }), 'ok')
      else if (resultado.committed) avisar(t('mover.compromissado', { chave: cartao.key }), 'ok')
      if (resultado.warnings.length > 0) avisar(t('mover.com_aviso'), 'info')
    },
    onSettled: (_r, _e, { cartao }) => recarregar(quadroId, cartao.id),
  })
}

/** Grava uma politica do quadro (WIP do fluxo, SLE, confirmar movimentos...). */
export function useAlterarQuadro(quadroId: string) {
  const produto = useProduto().id
  const avisar = useAvisos()
  const { t } = useT()
  const recarregar = useRecarregarQuadro()
  return useMutation({
    mutationFn: (corpo: Partial<Quadro>) =>
      nagare<Quadro>(produto, `/boards/${encodeURIComponent(quadroId)}`, { metodo: 'PATCH', corpo }),
    onError: (erro) => avisar(mensagemDeErro(erro, t), 'erro'),
    onSuccess: () => recarregar(quadroId),
  })
}
