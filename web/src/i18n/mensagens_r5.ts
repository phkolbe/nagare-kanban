/**
 * Textos da Rodada 5 (R5-D, 12/11/2026): tipos de cartao por produto (PPLAT-312) e o cartao
 * ligado ao caso do SR Adv (PPLAT-310). Juntos em `mensagens.ts`, com a mesma exigencia: toda
 * chave existe em portugues e em ingles.
 */

export const ptR5 = {
  // ------------------------------------------------------------------ tipos de cartao
  'tipos.titulo': 'Tipos de cartão',
  'tipos.descricao':
    'A lista do campo “Tipo de cartão” neste produto. O primeiro é o tipo de um cartão novo. Um tipo que algum cartão já tem é arquivado, não excluído: o cartão continua com ele.',
  'tipos.padrao': 'É a lista padrão do Nagare. Ao mudar qualquer coisa, ela passa a ser a lista deste produto.',
  'tipos.nome': 'Nome',
  'tipos.novo': 'Novo tipo',
  'tipos.adicionar': 'Adicionar',
  'tipos.renomear': 'renomear',
  'tipos.arquivar': 'arquivar',
  'tipos.reativar': 'reativar',
  'tipos.excluir': 'excluir',
  'tipos.subir': 'Subir {nome}',
  'tipos.descer': 'Descer {nome}',
  'tipos.arquivado': 'arquivado',
  'tipos.so_leitura': 'Só o administrador de Tarefas do produto muda esta lista.',
  'erro.invalid_card_type': 'Esse tipo não está na lista deste produto (ou foi arquivado).',
  'erro.card_type_in_use': 'Há cartões com esse tipo. Arquive em vez de excluir, para eles continuarem com ele.',
  'erro.card_type_last_active': 'O produto precisa de pelo menos um tipo em uso.',
  'erro.card_type_name_taken': 'Já existe um tipo com esse nome.',
  'erro.card_type_name_required': 'Dê um nome ao tipo.',

  // ------------------------------------------------------------------ caso do SR Adv
  'caso.campo': 'Caso',
  'caso.sem_caso': 'Sem caso',
  'caso.abrir': 'Abrir o caso',
  'caso.cliente': 'Cliente: {nome}',
  'caso.dica': 'Digite o cliente, o número do processo ou o nome do caso. O cliente vem do caso.',
  'caso.indisponivel': 'A lista de casos do SR Adv não está disponível para a sua conta neste produto.',
  'caso.encerrado': 'encerrado',
  'caso.filtro': 'Tarefas do caso {caso}',
  'caso.filtro_sem_nome': 'Tarefas deste caso',
  'caso.ver_todas': 'ver todas as tarefas',
  'erro.case_link_unavailable': 'Só um cartão de um produto da Pampufa se liga a um caso do SR Adv.',
} as const

export type ChaveR5 = keyof typeof ptR5

export const enR5: Record<ChaveR5, string> = {
  'tipos.titulo': 'Card types',
  'tipos.descricao':
    'The list of the “Card type” field in this product. The first one is the type of a new card. A type some card already has is archived, not deleted: the card keeps it.',
  'tipos.padrao': 'This is the Nagare’s default list. Changing anything makes it this product’s own list.',
  'tipos.nome': 'Name',
  'tipos.novo': 'New type',
  'tipos.adicionar': 'Add',
  'tipos.renomear': 'rename',
  'tipos.arquivar': 'archive',
  'tipos.reativar': 'restore',
  'tipos.excluir': 'delete',
  'tipos.subir': 'Move {nome} up',
  'tipos.descer': 'Move {nome} down',
  'tipos.arquivado': 'archived',
  'tipos.so_leitura': 'Only the product’s Tasks administrator changes this list.',
  'erro.invalid_card_type': 'That type is not on this product’s list (or it was archived).',
  'erro.card_type_in_use': 'Some cards have this type. Archive it instead of deleting, so they keep it.',
  'erro.card_type_last_active': 'The product needs at least one type in use.',
  'erro.card_type_name_taken': 'There is already a type with that name.',
  'erro.card_type_name_required': 'Give the type a name.',

  'caso.campo': 'Case',
  'caso.sem_caso': 'No case',
  'caso.abrir': 'Open the case',
  'caso.cliente': 'Client: {nome}',
  'caso.dica': 'Type the client, the lawsuit number or the case name. The client comes from the case.',
  'caso.indisponivel': 'The SR Adv case list is not available to your account in this product.',
  'caso.encerrado': 'closed',
  'caso.filtro': 'Tasks of case {caso}',
  'caso.filtro_sem_nome': 'Tasks of this case',
  'caso.ver_todas': 'see every task',
  'erro.case_link_unavailable': 'Only a card of a Pampufa product links to an SR Adv case.',
}
