/** Os enderecos da tela antiga (rotas com `#`) nas rotas novas do Nagare sozinho. */

const MAPA: [RegExp, (m: RegExpExecArray) => string][] = [
  [/^#\/board\/([\w-]+)/, (m) => `/quadro/${m[1]}`],
  [/^#\/board\/?$/, () => '/quadro'],
  [/^#\/projects\/([\w-]+)/, (m) => `/projetos/${m[1]}`],
  [/^#\/projects\/?$/, () => '/projetos'],
  [/^#\/metrics\/([\w-]+)/, (m) => `/metricas/${m[1]}`],
  [/^#\/simulator\/([\w-]+)/, (m) => `/simulador/${m[1]}`],
  [/^#\/simulator\/?$/, () => '/simulador'],
  [/^#\/admin\/users/, () => '/admin/usuarios'],
  [/^#\/admin\/flags/, () => '/admin/flags'],
  [/^#\/admin\/plans/, () => '/admin/planos'],
  [/^#\/login/, () => '/entrar'],
]

export function rotaDoLinkAntigo(hash: string): string | null {
  for (const [padrao, destino] of MAPA) {
    const achado = padrao.exec(hash)
    if (achado) return destino(achado)
  }
  return null
}
