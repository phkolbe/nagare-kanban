/** Filtros do quadro (projeto, classe, responsavel, etiqueta, bloqueado, tipo, texto) na URL.
 * A busca fica na linha do titulo; o resto, nas ferramentas do quadro (18/10/2026). */

import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import { CLASSES, TIPOS_DEMANDA, type Cartao, type ClasseServico, type TipoDemanda } from '../../api/tipos'
import { useT } from '../../i18n'
import { usePessoas, useProjetos } from './consultas'
import { contarFiltros, FILTROS_VAZIOS, gravarFiltros, type Filtros } from './regras'

/** A busca de cartoes (sempre a vista na linha do titulo, 18/10/2026); filtra no servidor. */
export function BuscaDeCartoes({ filtros }: { filtros: Filtros }) {
  const { t } = useT()
  const [busca, setBusca] = useSearchParams()
  const [texto, setTexto] = useState(filtros.texto)
  // "Limpar" em outro lugar zera a busca: o campo acompanha.
  useEffect(() => setTexto(filtros.texto), [filtros.texto])
  // O texto filtra no servidor: espera a pessoa parar de digitar.
  useEffect(() => {
    if (texto === filtros.texto) return
    const espera = window.setTimeout(() => setBusca(gravarFiltros(busca, { ...filtros, texto }), { replace: true }), 350)
    return () => window.clearTimeout(espera)
  })
  return (
    <input
      type="search"
      data-busca
      className="ng-busca"
      placeholder={t('filtros.buscar')}
      aria-label={t('filtros.buscar')}
      value={texto}
      onChange={(e) => setTexto(e.target.value)}
    />
  )
}

/**
 * Selo de filtro ligado (18/10/2026): com as ferramentas fechadas, diz que ha filtro, segmento
 * ou busca e quantos cartoes aparecem, e limpa tudo de uma vez.
 */
export function SeloFiltrado({ filtros, total, segmentado, limpar }: { filtros: Filtros; total: number; segmentado: boolean; limpar: () => void }) {
  const { t } = useT()
  if (contarFiltros(filtros) === 0 && !segmentado) return null
  return (
    <span className="ng-pilula ng-selo-filtrado">
      {t('filtros.filtrado', { total })}
      <button type="button" className="link" onClick={limpar}>
        {t('filtros.limpar')}
      </button>
    </span>
  )
}

/** O botao dos filtros (projeto, responsavel, etiqueta, tipo, classe, bloqueado), com o painel. */
export function BotaoDeFiltros({ filtros, cartoes }: { filtros: Filtros; cartoes: Cartao[] }) {
  const { t, eu } = useT()
  const [busca, setBusca] = useSearchParams()
  const [aberto, setAberto] = useState(false)
  const projetos = useProjetos()
  const pessoas = usePessoas()
  const ativos = contarFiltros({ ...filtros, texto: '' })
  const etiquetas = [...new Set(cartoes.flatMap((c) => c.tags))].sort()

  const aplicar = (parcial: Partial<Filtros>) => setBusca(gravarFiltros(busca, { ...filtros, ...parcial }), { replace: true })

  const alternarClasse = (classe: ClasseServico) =>
    aplicar({
      classes: filtros.classes.includes(classe) ? filtros.classes.filter((c) => c !== classe) : [...filtros.classes, classe],
    })

  return (
    <div className="ng-filtros">
      <button type="button" className="btn sm ghost" aria-expanded={aberto} onClick={() => setAberto(!aberto)}>
        {t('filtros.titulo')}
        {ativos > 0 && <span className="contador">{ativos}</span>}
      </button>
      {ativos > 0 && (
        <button type="button" className="link" onClick={() => setBusca(gravarFiltros(busca, { ...FILTROS_VAZIOS, texto: filtros.texto }), { replace: true })}>
          {t('filtros.limpar')}
        </button>
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
