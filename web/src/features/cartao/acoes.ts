/** Consultas e acoes do cartao, usadas pela gaveta e pelas abas. */

import { useMutation, useQuery } from '@tanstack/react-query'

import { useNagare } from '../../app/contexto'
import { chaves, nagare } from '../../api/nagare'
import type { Cartao, CartaoDetalhe } from '../../api/tipos'
import { useAvisos } from '../../componentes/Avisos'
import { mensagemDeErro, useT, type Chave } from '../../i18n'
import { useRecarregarQuadro } from '../quadro/consultas'

/**
 * O cartao pelo id ou pela chave (a API aceita os dois). A pagina do cartao busca pela chave e
 * depois segue pelo id, com o que ja veio como dado inicial: as acoes refazem o cartao pelo id.
 */
export function useCartao(id: string, inicial?: CartaoDetalhe) {
  const produto = useNagare().produto
  return useQuery({
    queryKey: chaves.cartao(produto, id),
    queryFn: () => nagare<CartaoDetalhe>(produto, `/cards/${encodeURIComponent(id)}`),
    initialData: inicial,
    retry: (tentativas, erro) => (erro as { status?: number }).status !== 404 && tentativas < 2,
  })
}

/** Uma acao no cartao que refaz o cartao e o quadro ao terminar. */
export function useAcaoNoCartao<T = unknown>(cartao: Pick<Cartao, 'id' | 'board_id'>, sucesso?: Chave) {
  const produto = useNagare().produto
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
