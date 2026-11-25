/**
 * Desenha um Documento de relatorio (24/11/2026): secoes com indicadores contra o periodo
 * anterior, tabelas, barras e notas. Generico: os sete relatorios da onda 1 (e os que vierem)
 * usam este mesmo desenho, porque o servidor ja manda o texto pronto na lingua da pessoa.
 */

import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'

import { useNagare } from '../../app/contexto'
import { BarrasHorizontais } from '../../graficos/Graficos'
import { useT } from '../../i18n'
import {
  formatarValor,
  textoDaVariacao,
  tomDaVariacao,
  type Bloco,
  type CartaoNaCelula,
  type Documento as Doc,
  type Formatos,
  type Indicador,
  type Unidade,
} from './formato'

function useFormatos(): Formatos {
  const { t, numero, data, dataHora } = useT()
  return { numero, data, dataHora, dias: t('relatorios.dias'), horas: t('relatorios.horas'), porSemana: t('relatorios.por_semana') }
}

export function Documento({ doc, abrirProduto }: { doc: Doc; abrirProduto?: (produto: string, quadroId: string) => void }) {
  const { t, dataHora } = useT()
  return (
    <article className="ng-relatorio" aria-label={doc.title}>
      <header className="ng-relatorio-cabeca">
        <div className="mini">{doc.code}</div>
        <h2>
          {doc.title}
          {doc.subtitle && <span className="ng-relatorio-sub"> · {doc.subtitle}</span>}
        </h2>
        <p className="ng-relatorio-periodo">
          <b>{doc.period.label}</b> <span className="mini">({t('relatorios.comparado_com', { periodo: doc.previous.label })})</span>
        </p>
        <p className="mini">{t('relatorios.gerado_em', { quando: dataHora(doc.generated_at) })}</p>
      </header>
      {doc.sections.map((secao) => (
        <section key={secao.id} className="ng-relatorio-secao">
          <h3>{secao.title}</h3>
          {secao.description && <p className="mini">{secao.description}</p>}
          {secao.blocks.map((bloco, i) => (
            <BlocoDoRelatorio key={i} bloco={bloco} abrirProduto={abrirProduto} />
          ))}
        </section>
      ))}
    </article>
  )
}

function BlocoDoRelatorio({ bloco, abrirProduto }: { bloco: Bloco; abrirProduto?: (produto: string, quadroId: string) => void }) {
  const f = useFormatos()
  if (bloco.type === 'kpis') {
    return (
      <div className="ng-indicadores ng-relatorio-indicadores">
        {bloco.items.map((item) => (
          <Cifra key={item.key} item={item} />
        ))}
      </div>
    )
  }
  if (bloco.type === 'note') {
    if (!bloco.text) return null
    return <p className={`ng-relatorio-nota ${bloco.tone === 'warning' ? 'alerta' : ''}`}>{bloco.text}</p>
  }
  if (bloco.type === 'bars') {
    return (
      <div className="ng-relatorio-barras">
        <BarrasHorizontais
          titulo={bloco.title}
          textoVazio={bloco.empty}
          larguraRotulo={220}
          series={[{ chave: 'v', rotulo: bloco.title, cor: 'var(--ng-rampa-b)' }]}
          formatarValor={(v) => formatarValor(v, bloco.unit, f)}
          linhas={bloco.items.map((item) => ({
            rotulo: item.label,
            segmentos: [{ chave: 'v', valor: Number(item.value) || 0 }],
            nota: item.note === undefined || item.note === null ? undefined : String(item.note),
          }))}
        />
      </div>
    )
  }
  return <Tabela bloco={bloco} abrirProduto={abrirProduto} />
}

function Cifra({ item }: { item: Indicador }) {
  const { t, numero } = useT()
  const f = useFormatos()
  const tom = tomDaVariacao(item)
  const variacao = textoDaVariacao(item.trend_pct, numero)
  return (
    <div className="ng-indicador" title={item.hint ?? undefined}>
      <div className="ng-indicador-rotulo">
        <span>{item.label}</span>
        {item.low_confidence && <span className="mini" title={t('relatorios.amostra_pequena')}>≈</span>}
      </div>
      <div className="ng-indicador-valor">{formatarValor(item.value, item.unit, f)}</div>
      {(variacao || item.previous !== null) && (
        <div className="ng-indicador-pe mini">
          {variacao && <span className={tom === 'bom' ? 'ng-bom' : tom === 'ruim' ? 'ng-ruim' : ''}>{variacao}</span>}
          {item.previous !== null && <span>{t('relatorios.antes', { valor: formatarValor(item.previous, item.unit, f) })}</span>}
        </div>
      )}
    </div>
  )
}

function Tabela({
  bloco,
  abrirProduto,
}: {
  bloco: Extract<Bloco, { type: 'table' }>
  abrirProduto?: (produto: string, quadroId: string) => void
}) {
  const { t } = useT()
  const f = useFormatos()
  const { rotaDoCartao } = useNagare()
  const numerica = (u: Unidade) => !['text', 'card', 'date', 'datetime'].includes(u)
  return (
    <div className="ng-relatorio-tabela">
      {bloco.title && <h4>{bloco.title}</h4>}
      {bloco.rows.length === 0 ? (
        <p className="mini">{bloco.empty}</p>
      ) : (
        <div className="ng-rolagem-x">
          <table className="ng-tabela">
            <thead>
              <tr>
                {bloco.columns.map((c) => (
                  <th key={c.key} className={numerica(c.unit) ? 'num' : undefined}>
                    {c.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {bloco.rows.map((linha, i) => {
                const link = linha.link as { product: string; board_id: string } | undefined
                return (
                  <tr key={i}>
                    {bloco.columns.map((c, j) => {
                      const valor = linha[c.key]
                      let conteudo: ReactNode = formatarValor(valor, c.unit, f)
                      if (c.unit === 'card' && valor && (valor as CartaoNaCelula).key) {
                        const cartao = valor as CartaoNaCelula
                        conteudo = (
                          <Link to={rotaDoCartao(String(cartao.key))}>
                            <b>{cartao.key}</b> {cartao.title}
                          </Link>
                        )
                      } else if (j === 0 && link && abrirProduto) {
                        conteudo = (
                          <button type="button" className="ng-link" title={t('relatorios.abrir_produto')} onClick={() => abrirProduto(link.product, link.board_id)}>
                            {conteudo}
                          </button>
                        )
                      }
                      return (
                        <td key={c.key} className={numerica(c.unit) ? 'num' : undefined}>
                          {conteudo}
                        </td>
                      )
                    })}
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
