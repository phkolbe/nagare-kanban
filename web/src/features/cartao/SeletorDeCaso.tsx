/**
 * O caso do SR Adv no cartao (PPLAT-310, 12/11/2026): a lista busca na API do `sr-adv` (o mesmo
 * gateway e o mesmo token da Pampufa; o produto vai no caminho) e o cliente vem do caso. Sem
 * acesso aos casos do produto (outro produto, ou a conta sem papel no SR Adv), o campo some; o
 * caso que o cartao ja tem continua a vista no `LinkDoCaso`.
 */

import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { requisitar } from '@pampufa/ui'

import { MODO, useNagare } from '../../app/contexto'
import { chaves, consulta, urlCasos } from '../../api/nagare'
import type { CasoDoSrAdv, ListaDeCasos } from '../../api/tipos'
import { CaixaDeLista } from '../../componentes/CaixaDeLista'
import { useT } from '../../i18n'
import { itensDeCasos, rotuloDoCaso, SEM_CASO, termoDaBusca, vinculoDoCaso, type VinculoDoCaso } from './casos'

/** Os casos do produto que batem com `termo` (vazio: os primeiros). So na plataforma. */
export function useCasos(termo: string) {
  const { produto } = useNagare()
  return useQuery({
    queryKey: chaves.casos(produto, termo),
    queryFn: () => requisitar<ListaDeCasos>(`${urlCasos(produto)}${consulta({ busca: termo, limite: 20 })}`),
    enabled: MODO === 'plataforma',
    staleTime: 30_000,
    retry: false,
    placeholderData: (anterior) => anterior,
  })
}

/**
 * Um caso pelo id (o cartao de providencia ligado pelo link nao traz o nome; o quadro filtrado
 * por caso tambem nao). So leitura; sem acesso, fica sem nome.
 */
export function useCaso(id: string | null | undefined, precisa = true) {
  const { produto } = useNagare()
  return useQuery({
    queryKey: [...chaves.casos(produto, ''), 'um', id ?? ''],
    // `GET .../casos/{id}` devolve a ficha (`{caso, processos, ...}`): so o caso interessa aqui.
    queryFn: async () =>
      (await requisitar<{ caso: CasoDoSrAdv }>(`${urlCasos(produto)}/${encodeURIComponent(id ?? '')}`)).caso,
    enabled: MODO === 'plataforma' && Boolean(id) && precisa,
    staleTime: 60_000,
    retry: false,
  })
}

export function SeletorDeCaso({
  valor,
  aoMudar,
  desabilitado = false,
}: {
  valor: VinculoDoCaso
  aoMudar: (vinculo: VinculoDoCaso) => void
  desabilitado?: boolean
}) {
  const { t } = useT()
  const [texto, setTexto] = useState('')
  const [termo, setTermo] = useState('')
  // Regra geral (R5-B): espera ~300 ms parado antes de buscar.
  useEffect(() => {
    const espera = window.setTimeout(() => setTermo(termoDaBusca(texto)), 300)
    return () => window.clearTimeout(espera)
  }, [texto])
  const disponivel = useCasos('')
  const casos = useCasos(termo)

  // Sem acesso aos casos, nada a escolher (o cartao mostra o caso que ja tem com o LinkDoCaso).
  if (MODO !== 'plataforma' || disponivel.isError) return null
  const lista = (termo ? casos.data : disponivel.data)?.itens ?? []
  return (
    <CaixaDeLista
      rotulo={t('caso.campo')}
      itens={itensDeCasos(lista, valor, t('caso.encerrado'))}
      valor={valor.case_id ?? ''}
      vazio={t('caso.sem_caso')}
      desabilitado={desabilitado}
      dica={valor.client_name ? t('caso.cliente', { nome: valor.client_name }) : t('caso.dica')}
      aoDigitar={setTexto}
      aoMudar={(id) => {
        if (!id) return aoMudar(SEM_CASO)
        const caso = lista.find((c) => c.id === id)
        if (caso) aoMudar(vinculoDoCaso(caso))
      }}
    />
  )
}

/** O caso do cartao e o link para a pagina dele no SR Adv. */
export function LinkDoCaso({ vinculo, url }: { vinculo: VinculoDoCaso; url: string | null | undefined }) {
  const { t } = useT()
  const { produto } = useNagare()
  const semNome = !vinculo.case_label
  const caso = useCaso(vinculo.case_id, semNome)
  if (!vinculo.case_id) return null
  const nome = vinculo.case_label ?? (caso.data ? rotuloDoCaso(caso.data) : null)
  const cliente = vinculo.client_name ?? caso.data?.cliente_nome
  const destino = url || `/grupo-sr/${encodeURIComponent(produto)}/processos/${encodeURIComponent(vinculo.case_id)}`
  return (
    <p className="ng-caso">
      <span className="mini">{t('caso.campo')}:</span> <strong>{nome ?? t('caso.filtro_sem_nome')}</strong>
      {cliente && <span className="mini"> · {t('caso.cliente', { nome: cliente })}</span>}{' '}
      <a className="link" href={destino}>
        {t('caso.abrir')} ↗
      </a>
    </p>
  )
}
