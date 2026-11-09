/**
 * Campos obrigatorios no design do Nagare (regra geral da homologacao, 08/11/2026): o asterisco
 * em vermelho no rotulo e, no campo `required` vazio, a borda vermelha depois que a pessoa passa
 * por ele (`:user-invalid`) ou tenta salvar (`ng-tentou-salvar`). As cores ficam no nagare.css.
 */

/** O asterisco vermelho do rotulo. */
export function Obrigatorio() {
  return (
    <span className="ng-obrigatorio" aria-hidden="true">
      {' '}*
    </span>
  )
}

/**
 * Ao tentar salvar: marca o bloco para que os `required` vazios (e os invalidos) ganhem a borda
 * vermelha e leva o foco ao primeiro deles. Devolve true quando esta tudo preenchido.
 */
export function conferirObrigatorios(bloco: HTMLElement | null): boolean {
  if (!bloco) return true
  bloco.classList.add('ng-tentou-salvar')
  const faltando = bloco.querySelector<HTMLElement>('input:invalid, select:invalid, textarea:invalid')
  faltando?.focus()
  return !faltando
}
