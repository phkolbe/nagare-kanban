/**
 * Troca de fluxo (NG14): outro projeto e outro quadro passam a receber as providencias do
 * produto. Os cartoes abertos sao recriados no quadro novo; o antigo fica so leitura.
 * O quadro novo trava ao receber o primeiro cartao (NG13): monte o fluxo antes de trocar.
 */

import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Aviso } from '@pampufa/ui'

import { useNagare } from '../../app/contexto'
import { chaves, nagare } from '../../api/nagare'
import type { Lista, Quadro, Vinculo } from '../../api/tipos'
import { useAvisos } from '../../componentes/Avisos'
import { CaixaDeLista } from '../../componentes/CaixaDeLista'
import { Janela } from '../../componentes/Sobreposicao'
import { Obrigatorio, conferirObrigatorios } from '../../componentes/Obrigatorio'
import { mensagemDeErro, useT, type Chave } from '../../i18n'
import { useProjetos, useQuadros, useVinculo } from '../quadro/consultas'
import { FormProjeto } from './FormProjeto'
import { itensDeProjetos, itensDeQuadros, podeCriarProjeto } from './listas'

interface Modelo {
  key: string
  label: string
  description: string
  column_count: number
}

const MODELOS_CONHECIDOS = new Set(['upstream_downstream', 'simple_delivery', 'discovery_only', 'providencias', 'blank'])

export function TrocarFluxo({ atual, fechar }: { atual: NonNullable<Vinculo['link']>; fechar: () => void }) {
  const { t, eu } = useT()
  const produto = useNagare().produto
  const queryClient = useQueryClient()
  const avisar = useAvisos()
  const projetos = useProjetos()
  const quadros = useQuadros()
  const vinculo = useVinculo()
  const nomeDoQuadroNovo = useRef<HTMLInputElement>(null)
  const modelos = useQuery({
    queryKey: chaves.modelos(produto),
    queryFn: () => nagare<Lista<Modelo>>(produto, '/boards/templates'),
    staleTime: 300_000,
  })
  const [projeto, setProjeto] = useState('')
  const [quadro, setQuadro] = useState('')
  const [novoQuadro, setNovoQuadro] = useState({ nome: '', modelo: 'providencias' })
  const [motivo, setMotivo] = useState('')
  // "+" ao lado da lista (09/11/2026): null fechado; texto, aberto com a sugestao de nome.
  const [novoProjeto, setNovoProjeto] = useState<string | null>(null)

  const candidatos = (quadros.data?.items ?? []).filter((q) => q.id !== atual.board_id)
  const criarQuadro = useMutation({
    mutationFn: () =>
      nagare<Quadro>(produto, '/boards', {
        metodo: 'POST',
        corpo: { name: novoQuadro.nome.trim(), template: novoQuadro.modelo },
      }),
    onSuccess: (criado) => {
      void queryClient.invalidateQueries({ queryKey: chaves.quadros(produto) })
      setQuadro(criado.id)
      setNovoQuadro({ ...novoQuadro, nome: '' })
      avisar(t('vinculo.quadro_criado', { nome: criado.name }), 'ok')
    },
    onError: (erro) => avisar(mensagemDeErro(erro, t), 'erro'),
  })
  const trocar = useMutation({
    mutationFn: () =>
      nagare<Vinculo & { moved_cards: number }>(produto, '/integration/product', {
        metodo: 'PUT',
        corpo: { project_id: projeto, board_id: quadro, motivo: motivo.trim() },
      }),
    onSuccess: (resultado) => {
      void queryClient.invalidateQueries({ queryKey: ['nagare', produto] })
      avisar(t('vinculo.trocado', { cartoes: resultado.moved_cards }), 'ok')
      fechar()
    },
  })
  const nomeDoModelo = (m: Modelo) =>
    MODELOS_CONHECIDOS.has(m.key) ? t(`modelo.${m.key}` as Chave) : m.label

  return (
    <Janela
      titulo={t('vinculo.trocar')}
      fechar={fechar}
      largura={620}
      rodape={
        <>
          <button
            className="btn"
            type="button"
            disabled={trocar.isPending}
            onClick={(e) => {
              const janela = e.currentTarget.closest<HTMLElement>('.ng-janela')
              if (conferirObrigatorios(janela) && projeto && quadro && motivo.trim().length >= 3) trocar.mutate()
            }}
          >
            {t('vinculo.confirmar_troca')}
          </button>
          <button className="btn ghost" type="button" onClick={fechar}>
            {t('comum.cancelar')}
          </button>
        </>
      }
    >
      <Aviso tipo="info">{t('vinculo.troca_explica')}</Aviso>
      <p className="mini">{t('vinculo.atual', { projeto: `${atual.project.key} · ${atual.project.name}`, quadro: atual.board.name })}</p>

      <CaixaDeLista
        rotulo={t('vinculo.novo_projeto')}
        obrigatorio
        itens={itensDeProjetos(projetos.data?.items ?? [], projeto, atual.project_id)}
        valor={projeto}
        aoMudar={setProjeto}
        aoIncluir={podeCriarProjeto(eu, vinculo.data) ? setNovoProjeto : undefined}
        rotuloIncluir={t('projetos.novo')}
      />

      <CaixaDeLista
        rotulo={t('vinculo.novo_quadro')}
        obrigatorio
        itens={itensDeQuadros(candidatos, (q) => (q.card_count ? t('vinculo.com_cartoes', { n: q.card_count }) : null))}
        valor={quadro}
        aoMudar={setQuadro}
        // O quadro novo se cria logo abaixo: o "+" leva para la com o nome digitado.
        aoIncluir={(sugestao) => {
          if (sugestao) setNovoQuadro({ ...novoQuadro, nome: sugestao.slice(0, 120) })
          nomeDoQuadroNovo.current?.focus()
        }}
        rotuloIncluir={t('vinculo.criar_quadro')}
      />
      <fieldset className="ng-secao">
        <legend>{t('vinculo.ou_crie')}</legend>
        <div className="ng-linha-campos">
          <label className="campo">
            <span>{t('estrutura.nome_quadro')}</span>
            <input
              ref={nomeDoQuadroNovo}
              value={novoQuadro.nome}
              maxLength={120}
              onChange={(e) => setNovoQuadro({ ...novoQuadro, nome: e.target.value })}
            />
          </label>
          <label className="campo">
            <span>{t('vinculo.modelo')}</span>
            <select value={novoQuadro.modelo} onChange={(e) => setNovoQuadro({ ...novoQuadro, modelo: e.target.value })}>
              {(modelos.data?.items ?? []).map((m) => (
                <option key={m.key} value={m.key}>
                  {nomeDoModelo(m)} ({t('vinculo.colunas', { n: m.column_count })})
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className="btn sm sec ng-alinhar-baixo"
            disabled={!novoQuadro.nome.trim() || criarQuadro.isPending}
            onClick={() => criarQuadro.mutate()}
          >
            {t('vinculo.criar_quadro')}
          </button>
        </div>
        <p className="mini">{t('vinculo.monte_antes')}</p>
      </fieldset>

      <label className="campo">
        <span>
          {t('vinculo.motivo')}
          <Obrigatorio />
        </span>
        <textarea rows={2} value={motivo} required minLength={3} maxLength={500} onChange={(e) => setMotivo(e.target.value)} />
      </label>
      {trocar.isError && <Aviso tipo="erro">{mensagemDeErro(trocar.error, t)}</Aviso>}
      {novoProjeto !== null && (
        <FormProjeto
          projeto={null}
          nomeSugerido={novoProjeto}
          fechar={() => setNovoProjeto(null)}
          criado={(p) => {
            setProjeto(p.id)
            void queryClient.invalidateQueries({ queryKey: chaves.projetos(produto) })
          }}
        />
      )}
    </Janela>
  )
}
