/** Filtros do quadro (projeto, classe, responsavel, etiqueta, bloqueado, tipo, texto) na URL. */

import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import { CLASSES, TIPOS_DEMANDA, type Cartao, type ClasseServico, type TipoDemanda } from '../../api/tipos'
import { useT } from '../../i18n'
import { usePessoas, useProjetos } from './consultas'
import { contarFiltros, FILTROS_VAZIOS, gravarFiltros, type Filtros } from './regras'

export function BarraDeFiltros({ filtros, cartoes, total }: { filtros: Filtros; cartoes: Cartao[]; total: number }) {
  const { t, eu } = useT()
  const [busca, setBusca] = useSearchParams()
  const [aberto, setAberto] = useState(false)
  const [texto, setTexto] = useState(filtros.texto)
  const projetos = useProjetos()
  const pessoas = usePessoas()
  const ativos = contarFiltros(filtros)
  const etiquetas = [...new Set(cartoes.flatMap((c) => c.tags))].sort()

  const aplicar = (parcial: Partial<Filtros>) => setBusca(gravarFiltros(busca, { ...filtros, ...parcial }), { replace: true })

  // O texto filtra no servidor: espera a pessoa parar de digitar.
  useEffect(() => {
    if (texto === filtros.texto) return
    const espera = window.setTimeout(() => aplicar({ texto }), 350)
    return () => window.clearTimeout(espera)
  })

  const alternarClasse = (classe: ClasseServico) =>
    aplicar({
      classes: filtros.classes.includes(classe) ? filtros.classes.filter((c) => c !== classe) : [...filtros.classes, classe],
    })

  return (
    <div className="ng-filtros">
      <input
        type="search"
        className="ng-busca"
        placeholder={t('filtros.buscar')}
        aria-label={t('filtros.buscar')}
        value={texto}
        onChange={(e) => setTexto(e.target.value)}
      />
      <button type="button" className="btn sm ghost" aria-expanded={aberto} onClick={() => setAberto(!aberto)}>
        {t('filtros.titulo')}
        {ativos > 0 && <span className="contador">{ativos}</span>}
      </button>
      {ativos > 0 && (
        <>
          <span className="mini">{t('filtros.resultado', { total })}</span>
          <button
            type="button"
            className="link"
            onClick={() => {
              setTexto('')
              setBusca(gravarFiltros(busca, FILTROS_VAZIOS), { replace: true })
            }}
          >
            {t('filtros.limpar')}
          </button>
        </>
      )}
      {aberto && (
        <div className="popover ng-filtros-pop" role="dialog" aria-label={t('filtros.titulo')}>
          <div className="ng-linha-campos">
            {eu?.flags.multi_project !== false && (
              <label className="campo">
                <span>{t('filtros.projeto')}</span>
                <select value={filtros.projeto} onChange={(e) => aplicar({ projeto: e.target.value })}>
                  <option value="">{t('filtros.todos')}</option>
                  {(projetos.data?.items ?? []).map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.key} · {p.name}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <label className="campo">
              <span>{t('filtros.responsavel')}</span>
              <select value={filtros.responsavel} onChange={(e) => aplicar({ responsavel: e.target.value })}>
                <option value="">{t('filtros.todos')}</option>
                {(pessoas.data?.people ?? []).map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="ng-linha-campos">
            <label className="campo">
              <span>{t('filtros.etiqueta')}</span>
              <select value={filtros.etiqueta} onChange={(e) => aplicar({ etiqueta: e.target.value })}>
                <option value="">{t('filtros.todas')}</option>
                {etiquetas.map((etiqueta) => (
                  <option key={etiqueta} value={etiqueta}>
                    {etiqueta}
                  </option>
                ))}
              </select>
            </label>
            <label className="campo">
              <span>{t('filtros.tipo')}</span>
              <select value={filtros.tipo} onChange={(e) => aplicar({ tipo: e.target.value as TipoDemanda | '' })}>
                <option value="">{t('filtros.todos')}</option>
                {TIPOS_DEMANDA.map((tipo) => (
                  <option key={tipo} value={tipo}>
                    {t(`tipo_demanda.${tipo}`)}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {eu?.flags.service_classes !== false && (
            <fieldset className="ng-classes">
              <legend>{t('filtros.classe')}</legend>
              {CLASSES.map((classe) => (
                <label key={classe} className={`ng-alternar classe-${classe}`}>
                  <input type="checkbox" checked={filtros.classes.includes(classe)} onChange={() => alternarClasse(classe)} />
                  {t(`classe.${classe}`)}
                </label>
              ))}
            </fieldset>
          )}
          <label className="ng-alternar">
            <input type="checkbox" checked={filtros.bloqueado} onChange={(e) => aplicar({ bloqueado: e.target.checked })} />
            {t('filtros.so_bloqueados')}
          </label>
          <div className="acoes">
            <button type="button" className="btn sm" onClick={() => setAberto(false)}>
              {t('comum.fechar')}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
