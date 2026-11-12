/**
 * Novo cartao numa coluna: titulo, projeto, tipo (lista do produto, PPLAT-312), caso do SR Adv
 * (PPLAT-310) e classe; o resto se edita no cartao.
 */

import { useEffect, useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { Aviso } from '@pampufa/ui'

import { useNagare } from '../../app/contexto'
import { nagare } from '../../api/nagare'
import { CLASSES, type Cartao, type ClasseServico, type Coluna } from '../../api/tipos'
import { CaixaDeLista } from '../../componentes/CaixaDeLista'
import { Janela } from '../../componentes/Sobreposicao'
import { Obrigatorio, conferirObrigatorios } from '../../componentes/Obrigatorio'
import { mensagemDeErro, useT } from '../../i18n'
import { SEM_CASO, vinculoDoCaso, type VinculoDoCaso } from '../cartao/casos'
import { SeletorDeCaso, useCaso } from '../cartao/SeletorDeCaso'
import { opcoesDeTipo, tipoInicial } from '../cartao/tiposDeCartao'
import { FormProjeto } from '../projetos/FormProjeto'
import { itensDeProjetos, podeCriarProjeto } from '../projetos/listas'
import { useProjetos, useRecarregarQuadro, useTiposDeCartao, useVinculo } from './consultas'

export function NovoCartao({
  quadroId,
  coluna,
  classe,
  caso: casoInicial,
  fechar,
  criado,
}: {
  quadroId: string
  coluna: Coluna
  classe?: ClasseServico
  caso?: VinculoDoCaso
  fechar: () => void
  criado: (cartao: Cartao) => void
}) {
  const { t, eu } = useT()
  const produto = useNagare().produto
  const projetos = useProjetos()
  const vinculo = useVinculo()
  const recarregar = useRecarregarQuadro()
  const [titulo, setTitulo] = useState('')
  const [projeto, setProjeto] = useState('')
  // "+" ao lado da lista (09/11/2026): null fechado; texto, aberto com a sugestao de nome.
  const [novoProjeto, setNovoProjeto] = useState<string | null>(null)
  const [classeEscolhida, setClasse] = useState<ClasseServico>(classe ?? 'standard')
  const tipos = useTiposDeCartao()
  // Vazio ate a pessoa escolher: vale o primeiro da lista do produto.
  const [tipo, setTipo] = useState('')
  const [caso, setCaso] = useState<VinculoDoCaso>(casoInicial ?? SEM_CASO)
  const tipoMostrado = tipo || tipoInicial(tipos.data?.items)
  // Quadro filtrado por um caso que ainda nao tem cartao: o nome e o cliente vem do SR Adv.
  const doFiltro = useCaso(caso.case_id, Boolean(caso.case_id && !caso.case_label))
  useEffect(() => {
    if (doFiltro.data) setCaso((atual) => (atual.case_id === doFiltro.data.id && !atual.case_label ? vinculoDoCaso(doFiltro.data) : atual))
  }, [doFiltro.data])
  const criar = useMutation({
    mutationFn: () =>
      nagare<Cartao>(produto, '/cards', {
        metodo: 'POST',
        corpo: {
          board_id: quadroId,
          column_id: coluna.id,
          title: titulo.trim(),
          project_id: projeto || null,
          service_class: classeEscolhida,
          // Sem a lista ainda: o servidor poe o primeiro tipo do produto.
          value_area: tipos.data ? tipoMostrado : undefined,
          ...(caso.case_id ? caso : {}),
        },
      }),
    onSuccess: (cartao) => {
      recarregar(quadroId)
      criado(cartao)
    },
  })

  return (
    <Janela
      titulo={t('novo.titulo', { coluna: coluna.name })}
      fechar={fechar}
      rodape={
        <>
          <button className="btn" type="submit" form="novo-cartao" disabled={criar.isPending}>
            {t('novo.criar')}
          </button>
          <button className="btn ghost" type="button" onClick={fechar}>
            {t('comum.cancelar')}
          </button>
        </>
      }
    >
      <form
        id="novo-cartao"
        noValidate
        onSubmit={(e) => {
          e.preventDefault()
          if (conferirObrigatorios(e.currentTarget) && titulo.trim()) criar.mutate()
        }}
      >
        <label className="campo">
          <span>
            {t('cartao.campo.titulo')}
            <Obrigatorio />
          </span>
          <input value={titulo} maxLength={500} autoFocus required onChange={(e) => setTitulo(e.target.value)} />
        </label>
        {eu?.flags.multi_project !== false && (
          <CaixaDeLista
            rotulo={t('cartao.campo.projeto')}
            itens={itensDeProjetos(projetos.data?.items ?? [], projeto)}
            valor={projeto}
            aoMudar={setProjeto}
            vazio={t('cartao.sem_projeto')}
            aoIncluir={podeCriarProjeto(eu, vinculo.data) ? setNovoProjeto : undefined}
            rotuloIncluir={t('projetos.novo')}
          />
        )}
        <label className="campo">
          <span>{t('cartao.campo.area')}</span>
          <select value={tipoMostrado} onChange={(e) => setTipo(e.target.value)}>
            {opcoesDeTipo(tipos.data?.items, null, t).map((o) => (
              <option key={o.valor} value={o.valor}>
                {o.texto}
              </option>
            ))}
          </select>
        </label>
        <SeletorDeCaso valor={caso} aoMudar={setCaso} />
        {eu?.flags.service_classes !== false && (
          <label className="campo">
            <span>{t('cartao.campo.classe')}</span>
            <select value={classeEscolhida} onChange={(e) => setClasse(e.target.value as ClasseServico)}>
              {CLASSES.map((c) => (
                <option key={c} value={c}>
                  {t(`classe.${c}`)}
                </option>
              ))}
            </select>
            <small className="mini">{t(`classe.${classeEscolhida}.politica`)}</small>
          </label>
        )}
      </form>
      {criar.isError && <Aviso tipo="erro">{mensagemDeErro(criar.error, t)}</Aviso>}
      {/* Fora do <form> deste cartao: a janela do projeto tem o form dela. */}
      {novoProjeto !== null && (
        <FormProjeto projeto={null} nomeSugerido={novoProjeto} fechar={() => setNovoProjeto(null)} criado={(p) => setProjeto(p.id)} />
      )}
    </Janela>
  )
}
