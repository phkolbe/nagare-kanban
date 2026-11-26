/**
 * Copia um texto: a API da area de transferencia e, sem ela (pagina sem HTTPS), o jeito antigo.
 * Usada pelo endereco do cartao e pelo link de cliente dos Reports (onda 2, 25/11/2026).
 */
export async function copiar(texto: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(texto)
      return true
    }
  } catch {
    /* tenta o jeito antigo */
  }
  const campo = document.createElement('textarea')
  campo.value = texto
  campo.setAttribute('readonly', '')
  campo.style.position = 'fixed'
  campo.style.opacity = '0'
  document.body.appendChild(campo)
  campo.select()
  try {
    return document.execCommand('copy')
  } catch {
    return false
  } finally {
    campo.remove()
  }
}
