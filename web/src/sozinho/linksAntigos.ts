/** Os enderecos da tela antiga (rotas com `#`) nas rotas novas do Nagare sozinho, ja com os
 * nomes em ingles da URL (19/10/2026). */

const MAPA: [RegExp, (m: RegExpExecArray) => string][] = [
  [/^#\/board\/([\w-]+)/, (m) => `/board/${m[1]}`],
  [/^#\/board\/?$/, () => '/board'],
  [/^#\/projects\/([\w-]+)/, (m) => `/projects/${m[1]}`],
  [/^#\/projects\/?$/, () => '/projects'],
  [/^#\/metrics\/([\w-]+)/, (m) => `/metrics/${m[1]}`],
  [/^#\/simulator\/([\w-]+)/, (m) => `/simulator/${m[1]}`],
  [/^#\/simulator\/?$/, () => '/simulator'],
  [/^#\/admin\/users/, () => '/admin/users'],
  [/^#\/admin\/flags/, () => '/admin/flags'],
  [/^#\/admin\/plans/, () => '/admin/plans'],
  [/^#\/login/, () => '/sign-in'],
]

export function rotaDoLinkAntigo(hash: string): string | null {
  for (const [padrao, destino] of MAPA) {
    const achado = padrao.exec(hash)
    if (achado) return destino(achado)
  }
  return null
}
