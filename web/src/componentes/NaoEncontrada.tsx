/** Pagina inexistente: volta para o quadro. */

import { Link } from 'react-router-dom'
import { Vazio } from '@pampufa/ui'

import { useNagare } from '../app/contexto'
import { useT } from '../i18n'

export function NaoEncontrada() {
  const { t } = useT()
  const { rota } = useNagare()
  return (
    <section className="card">
      <Vazio titulo={t('comum.nao_encontrada')}>
        <Link className="btn sm" to={rota('quadro')}>
          {t('comum.ir_ao_quadro')}
        </Link>
      </Vazio>
    </section>
  )
}
