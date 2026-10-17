/** Lingua das telas do Nagare: a escolha e da pessoa e vale em qualquer aparelho (17/10/2026). */

import type { Idioma } from '../api/tipos'
import { useT } from '../i18n'

export function SeletorIdioma() {
  const { t, eu, escolher } = useT()
  const valor = eu?.locale_chosen ?? ''
  return (
    <label className="ng-idioma">
      <span className="visualmente-oculto">{t('idioma.rotulo')}</span>
      <select
        value={valor}
        aria-label={t('idioma.rotulo')}
        onChange={(e) => escolher(e.target.value ? (e.target.value as Idioma) : null)}
      >
        <option value="">{t('idioma.padrao', { idioma: t(eu?.via === 'local' ? 'idioma.en' : 'idioma.pt') })}</option>
        <option value="pt-BR">{t('idioma.pt')}</option>
        <option value="en">{t('idioma.en')}</option>
      </select>
    </label>
  )
}
