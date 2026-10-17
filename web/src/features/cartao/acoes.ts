/** Consultas e acoes do cartao, usadas pela gaveta e pelas abas. */

import { useMutation, useQuery } from '@tanstack/react-query'
import { useProduto } from '@pampufa/ui'

import { chaves, nagare } from '../../api/nagare'
import type { Cartao, CartaoDetalhe } from '../../api/tipos'
import { useAvisos } from '../../componentes/Avisos'
import { mensagemDeErro, useT, type Chave } from '../../i18n'
import { useRecarregarQuadro } from '../quadro/consultas'

export function useCartao(id: string) {
  const produto = useProduto().id
  return useQuery({
    queryKey: chaves.cartao(produto, id),
    queryFn: () => nagare<CartaoDetalhe>(produto, `/cards/${encodeURIComponent(id)}`),
  })
}

/** Uma acao no cartao que refaz o cartao e o quadro ao terminar. */
export function useAcaoNoCartao<T = unknown>(cartao: Pick<Cartao, 'id' | 'board_id'>, sucesso?: Chave) {
  const produto = useProduto().id
  const avisar = useAvisos()
  const { t } = useT()
  const recarregar = useRecarregarQuadro()
  return useMutation({
    mutationFn: ({ caminho, metodo, corpo }: { caminho: string; metodo: 'POST' | 'PATCH' | 'DELETE'; corpo?: unknown }) =>
      nagare<T>(produto, caminho, { metodo, corpo }),
    onSuccess: () => {
      if (sucesso) avisar(t(sucesso), 'ok')
      recarregar(cartao.board_id, cartao.id)
    },
    onError: (erro) => avisar(mensagemDeErro(erro, t), 'erro'),
  })
}
