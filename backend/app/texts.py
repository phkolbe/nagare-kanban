"""Human-readable texts in the caller's language (product decision of 17/10/2026).

The flow engine (metrics, KMM, Monte Carlo, simulator) writes sentences for
people: labels, verdicts, interpretations, next actions. They come back in the
language of whoever is calling — Brazilian Portuguese (``pt-BR``) or English
(``en``) — while the source code stays readable English, gettext style::

    _("Observed WIP is {ratio}× throughput", ratio=num(ratio, 1))

* English is the default and the catalog key. ``_`` returns the English string
  untouched (formatted with ``values`` when given), so English output is exactly
  what the code produced before translation existed.
* :data:`PT` maps every English source string to its pt-BR translation, with the
  very same ``{placeholders}``. A missing entry falls back to English;
  ``tests/test_textos.py`` checks statically that none is missing or unused.
* :func:`N_` marks a string for the catalog without translating it, for tables
  built at import time (translate at use time with ``_(value)``).
* :func:`num` and :func:`date_label` format numbers and dates for the locale.

Machine keys, codes, enum values, ids and error-envelope messages are never
translated: the UI translates errors by their code.

The locale lives in a context variable set once per request by
:func:`backend.app.deps.get_current_user` (like the product in :mod:`.tenancy`);
requests without a user read English.
"""

from __future__ import annotations

import re
from contextvars import ContextVar, Token
from datetime import date

__all__ = [
    "EN",
    "N_",
    "PT",
    "PT_BR",
    "_",
    "_n",
    "current_locale",
    "date_label",
    "default_locale",
    "delta_pct",
    "effective_locale",
    "locale",
    "num",
    "on_platform",
    "set_locale",
    "set_via",
]

EN = "en"
PT_BR = "pt-BR"
SUPPORTED_LOCALES: tuple[str, ...] = (PT_BR, EN)

#: The effective locale of the current request.
current_locale: ContextVar[str] = ContextVar("nagare_locale", default=EN)
#: Where the current request came from: ``pampufa`` (the platform) or ``local``.
current_via: ContextVar[str] = ContextVar("nagare_via", default="local")


def default_locale(via: str | None) -> str:
    """Portuguese through the Pampufa, English on the Nagare's own login (N2)."""
    return PT_BR if via == "pampufa" else EN


def effective_locale(chosen: str | None, via: str | None) -> str:
    """The user's own choice when it is a supported one, else the default for ``via``."""
    return chosen if chosen in SUPPORTED_LOCALES else default_locale(via)


def set_locale(value: str | None) -> Token[str]:
    """Fix the locale for the rest of the request (unknown values mean English)."""
    return current_locale.set(value if value in SUPPORTED_LOCALES else EN)


def locale() -> str:
    """The locale of the current request."""
    return current_locale.get()


def set_via(value: str | None) -> Token[str]:
    """Remember whether this request came through the Pampufa platform."""
    return current_via.set("pampufa" if value == "pampufa" else "local")


def on_platform() -> bool:
    """True inside the Pampufa, where the Nagare-only screens (simulator) do not exist."""
    return current_via.get() == "pampufa"


# ---------------------------------------------------------------------------
# translation
# ---------------------------------------------------------------------------


def _(english: str, /, **values: object) -> str:
    """``english`` in the current locale, then ``.format(**values)`` when given."""
    text = PT.get(english, english) if current_locale.get() == PT_BR else english
    return text.format(**values) if values else text


def N_(english: str, /) -> str:
    """Mark ``english`` for the catalog and return it untranslated."""
    return english


def _n(singular: str, plural: str, count: float, /) -> str:
    """``singular`` when ``count`` is one, else ``plural`` — then translated."""
    return _(singular if abs(float(count) - 1) < 1e-9 else plural)


# ---------------------------------------------------------------------------
# numbers and dates
# ---------------------------------------------------------------------------

_NUMBER = re.compile(r"^([+-]?)(\d+)(?:\.(\d+))?([^\d.]*)$")


def num(value: object, places: int | str | None = None) -> str:
    """A number for a sentence: ``","`` decimals and ``"."`` thousands in pt-BR.

    ``places`` picks the English formatting the code always used: ``None`` is
    ``str(value)``, an ``int`` is ``f"{value:.{places}f}"`` and a string is a
    format spec (``"+.1f"``, ``"g"``). English output is exactly that string.
    """
    if places is None:
        text = str(value)
    elif isinstance(places, int):
        text = f"{value:.{places}f}"
    else:
        text = format(value, places)
    if current_locale.get() != PT_BR:
        return text
    match = _NUMBER.match(text)
    if match is None:
        return text
    sign, whole, fraction, rest = match.groups()
    grouped = f"{int(whole):,}".replace(",", ".")
    return sign + grouped + ("," + fraction if fraction is not None else "") + rest


def delta_pct(value: float | None) -> str:
    """`` (+4.2%)`` after a figure, or nothing when the change is undefined."""
    return "" if value is None else f" ({num(value, '+.1f')}%)"


_MONTHS_EN: tuple[str, ...] = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)
_MONTHS_PT: tuple[str, ...] = (
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
)


def date_label(value: date, reference: date | None = None) -> str:
    """``12 Oct`` / ``12 de outubro`` — the year only when it is not ``reference``'s.

    Hand-rolled rather than ``strftime``: ``%-d`` is not portable and ``%b``
    depends on the process locale, and a forecast must read the same everywhere.
    """
    with_year = reference is None or value.year != reference.year
    if current_locale.get() == PT_BR:
        day = "1º" if value.day == 1 else str(value.day)
        label = f"{day} de {_MONTHS_PT[value.month - 1]}"
        return f"{label} de {value.year}" if with_year else label
    label = f"{value.day} {_MONTHS_EN[value.month - 1]}"
    return f"{label} {value.year}" if with_year else label


# ---------------------------------------------------------------------------
# the pt-BR catalog — English source string -> translation, same placeholders
# ---------------------------------------------------------------------------

PT: dict[str, str] = {
    # -- flow/metrics.py -------------------------------------------------------
    "Expedite": "Urgente",
    "Standard": "Padrão",
    "Fixed date": "Data fixa",
    "Intangible": "Intangível",
    "Value demand": "Demanda de valor",
    "Failure demand": "Demanda de falha",
    "Maintenance": "Manutenção",
    "Feature": "Funcionalidade",
    "Defect": "Defeito",
    "Risk": "Risco",
    "Debt": "Dívida técnica",
    "Compliance": "Conformidade",
    "Unknown": "Desconhecido",
    "Dependency": "Dependência",
    "External Vendor": "Fornecedor externo",
    "Waiting On Customer": "Aguardando o cliente",
    "Technical": "Técnico",
    "People": "Pessoas",
    "Environment": "Ambiente",
    "Other": "Outro",
    "Day": "Dia",
    "Week": "Semana",
    "Month": "Mês",
    "Delivered per day": "Entregues por dia",
    "Delivered per week": "Entregues por semana",
    "Delivered per month": "Entregues por mês",
    "{n}-day rolling average": "Média móvel de {n} dias",
    "{n}-week rolling average": "Média móvel de {n} semanas",
    "{n}-month rolling average": "Média móvel de {n} meses",
    "{p}th percentile — {value}d": "Percentil {p} — {value} d",
    "Erratic — investigate batching, blockers and unplanned work before forecasting.": (
        "Errática — investigue lotes, bloqueios e trabalho não planejado antes de fazer previsões."
    ),
    "Queue-dominated: over 85% of lead time is waiting. Attack the biggest queue first.": (
        "Dominada por filas: mais de 85% do lead time é espera. Ataque primeiro a maior fila."
    ),
    (
        "Long tail: the slowest 5% take several times the median. Look for blocked or oversized "
        "items."
    ): (
        "Cauda longa: os 5% mais lentos levam várias vezes a mediana. Procure itens bloqueados "
        "ou grandes demais."
    ),
    "{verdict} Arrivals and departures are balanced.": (
        "{verdict} Chegadas e saídas estão equilibradas."
    ),
    "Customer lead time": "Lead time do cliente",
    "System lead time": "Lead time do sistema",
    "Discovery lead time": "Lead time de descoberta",
    "Not enough periods to judge variability.": (
        "Períodos insuficientes para avaliar a variabilidade."
    ),
    "Highly predictable — forecasts from this history will be tight.": (
        "Altamente previsível — as previsões a partir deste histórico serão precisas."
    ),
    "Normal variability — probabilistic forecasting works well here.": (
        "Variabilidade normal — a previsão probabilística funciona bem aqui."
    ),
    "Lumpy delivery — expect wide forecast ranges until batch sizes drop.": (
        "Entrega irregular — espere faixas de previsão largas até os lotes diminuírem."
    ),
    "No delivered work in this window, so there is no efficiency to report.": (
        "Nenhum trabalho entregue nesta janela, então não há eficiência a relatar."
    ),
    "Excellent — very little of the lead time is spent waiting.": (
        "Excelente — muito pouco do lead time é gasto em espera."
    ),
    "Healthy for knowledge work; most teams sit below this.": (
        "Saudável para trabalho do conhecimento; a maioria dos times fica abaixo disso."
    ),
    "Typical. The lever is queue removal, not working faster.": (
        "Típica. A alavanca é eliminar filas, não trabalhar mais rápido."
    ),
    "Not enough delivered work to describe the shape of the distribution.": (
        "Trabalho entregue insuficiente para descrever a forma da distribuição."
    ),
    "Tight distribution — the process behaves consistently across item types.": (
        "Distribuição concentrada — o processo se comporta de forma consistente entre os tipos "
        "de item."
    ),
    "Typical right-skewed Kanban distribution. Forecast with percentiles, not averages.": (
        "Distribuição Kanban típica, assimétrica à direita. Faça previsões com percentis, não "
        "com médias."
    ),
    "Nothing was delivered in this window, so Little's Law cannot be evaluated.": (
        "Nada foi entregue nesta janela, então a Lei de Little não pode ser avaliada."
    ),
    "Not enough committed work in flight to compare observed and predicted WIP.": (
        "Trabalho comprometido em andamento insuficiente para comparar o WIP observado com o "
        "previsto."
    ),
    "The system is behaving as a stable queue: observed WIP matches throughput × cycle time.": (
        "O sistema se comporta como uma fila estável: o WIP observado corresponde a vazão × "
        "tempo de ciclo."
    ),
    "{verdict} Arrivals are outpacing departures; the queue is still growing.": (
        "{verdict} As chegadas superam as saídas; a fila continua crescendo."
    ),
    "{verdict} Departures outpaced arrivals; the team is draining an existing queue.": (
        "{verdict} As saídas superaram as chegadas; o time está esvaziando uma fila existente."
    ),
    "Date": "Data",
    "Cards": "Cartões",
    "Delivered": "Entregues",
    "{metric} (days)": "{metric} (dias)",
    "Column": "Coluna",
    "Age (days)": "Idade (dias)",
    "Touch time": "Tempo de trabalho",
    "Wait time": "Tempo de espera",
    "Average days per card by column": "Média de dias por cartão em cada coluna",
    "Days": "Dias",
    "{metric} distribution": "{metric}: distribuição",
    "Items": "Itens",
    "Category": "Categoria",
    "Blocked days": "Dias bloqueados",
    "Class of service (delivered)": "Classe de serviço (entregues)",
    "Demand type (delivered)": "Tipo de demanda (entregues)",
    "Delivered by project": "Entregues por projeto",
    "Class": "Classe",
    "Needs delivered work in both classes.": "Precisa de trabalho entregue nas duas classes.",
    (
        "Expedite work clears far faster than standard work; the standard class is absorbing "
        "the interruption cost."
    ): (
        "O trabalho urgente sai muito mais rápido que o padrão; a classe padrão está absorvendo "
        "o custo das interrupções."
    ),
    (
        "Expedite and standard classes finish at comparable speed — the expedite lane is not "
        "distorting the flow."
    ): (
        "As classes urgente e padrão terminam em velocidade comparável — a raia de urgentes não "
        "está distorcendo o fluxo."
    ),
    "Work in progress": "Trabalho em andamento",
    "WIP": "WIP",
    (
        "Observed WIP is well above throughput × cycle time — work is entering faster than it "
        "leaves, so lead times will keep growing."
    ): (
        "O WIP observado está bem acima de vazão × tempo de ciclo — o trabalho entra mais "
        "rápido do que sai, então os lead times vão continuar crescendo."
    ),
    (
        "Observed WIP is below throughput × cycle time — the window probably closed on a batch "
        "of deliveries, or items are leaving the board without a delivery movement."
    ): (
        "O WIP observado está abaixo de vazão × tempo de ciclo — a janela provavelmente fechou "
        "logo após um lote de entregas, ou itens estão saindo do quadro sem uma movimentação de "
        "entrega."
    ),
    "Lead time (85th pct)": "Lead time (percentil 85)",
    "85% of delivered work finished within this many days.": (
        "85% do trabalho entregue terminou em até este número de dias."
    ),
    "Mean cycle time from the commitment point to delivery.": (
        "Tempo de ciclo médio do ponto de compromisso até a entrega."
    ),
    "Mean time demand spends upstream before the team commits to it.": (
        "Tempo médio que a demanda passa no upstream antes de o time se comprometer com ela."
    ),
    "Mean time per delivered item spent in activity columns.": (
        "Tempo médio por item entregue nas colunas de atividade."
    ),
    "Mean time per delivered item spent parked in queue columns.": (
        "Tempo médio por item entregue parado nas colunas de fila."
    ),
    "Flow efficiency": "Eficiência de fluxo",
    "Touch time as a share of total lead time. Most teams start near 15%.": (
        "Tempo de trabalho como fração do lead time total. A maioria dos times começa perto de 15%."
    ),
    "Throughput": "Vazão",
    "Delivered items per week across the selected window.": (
        "Itens entregues por semana na janela selecionada."
    ),
    "Items that reached the delivery point inside the window.": (
        "Itens que chegaram ao ponto de entrega dentro da janela."
    ),
    "Arrival rate": "Taxa de chegada",
    "New demand entering the flow per week. Compare with throughput.": (
        "Nova demanda entrando no fluxo por semana. Compare com a vazão."
    ),
    "Net flow": "Fluxo líquido",
    "Arrivals minus departures. Sustained positive net flow grows the queue.": (
        "Chegadas menos saídas. Um fluxo líquido positivo e persistente aumenta a fila."
    ),
    "Committed items not yet delivered, as of the end of the window.": (
        "Itens comprometidos ainda não entregues, no fim da janela."
    ),
    "Average WIP": "WIP médio",
    "Mean daily work in progress across the window.": (
        "Média diária de trabalho em andamento na janela."
    ),
    "Average age": "Idade média",
    "Mean age of items currently in flight, measured from commitment.": (
        "Idade média dos itens em andamento, medida a partir do compromisso."
    ),
    "Oldest item": "Item mais antigo",
    "Age of the longest-running item in flight. The first thing to unblock.": (
        "Idade do item em andamento há mais tempo. A primeira coisa a destravar."
    ),
    "SLE attainment": "Cumprimento do SLE",
    "Due-date performance": "Desempenho de prazo",
    "Fixed-date items delivered on or before their due date.": (
        "Itens de data fixa entregues até a data prevista."
    ),
    "Delivered items that existed only because something else failed.": (
        "Itens entregues que só existiram porque algo falhou."
    ),
    "Lead time blocked": "Lead time bloqueado",
    "Share of delivered items' lead time spent explicitly blocked.": (
        "Fração do lead time dos itens entregues passada explicitamente bloqueada."
    ),
    "Cards blocked": "Cartões bloqueados",
    "Distinct cards that were blocked at some point in the window.": (
        "Cartões distintos que ficaram bloqueados em algum momento da janela."
    ),
    "Throughput variability": "Variabilidade da vazão",
    (
        "Coefficient of variation of weekly throughput. Below 0.5 the flow is predictable "
        "enough to forecast without heroics."
    ): (
        "Coeficiente de variação da vazão semanal. Abaixo de 0,5 o fluxo é previsível o "
        "bastante para prever sem heroísmo."
    ),
    "Little's Law coherence": "Coerência com a Lei de Little",
    (
        "Observed average WIP divided by throughput × cycle time. A value near 1.0 means the "
        "system is stable and the metrics can be trusted."
    ): (
        "WIP médio observado dividido por vazão × tempo de ciclo. Um valor perto de 1,0 "
        "significa que o sistema está estável e as métricas são confiáveis."
    ),
    "SLE — {percentile}% in {days}d": "SLE — {percentile}% em {days} d",
    "In-flight cards": "Cartões em andamento",
    "system lead time of items delivered in this window": (
        "lead time do sistema dos itens entregues nesta janela"
    ),
    "Sealed envelope": "Envelope lacrado",
    "Blocked days by category": "Dias bloqueados por categoria",
    "Cumulative share": "Fração acumulada",
    (
        "Mean elapsed time from the moment demand entered the flow to delivery (sealed-envelope "
        "time counted)."
    ): (
        "Tempo médio decorrido desde a entrada da demanda no fluxo até a entrega (tempo de "
        "envelope lacrado incluído)."
    ),
    "Mean elapsed time from the moment demand entered the flow to delivery.": (
        "Tempo médio decorrido desde a entrada da demanda no fluxo até a entrega."
    ),
    (
        "Share of delivered work inside the {days}-day service level expectation (target "
        "{percentile}%)."
    ): (
        "Fração do trabalho entregue dentro da expectativa de nível de serviço de {days} dias "
        "(meta de {percentile}%)."
    ),
    "No service level expectation is set on this board.": (
        "Este quadro não tem expectativa de nível de serviço (SLE) definida."
    ),
    "{low}–{high}d": "{low}–{high} d",
    "Unassigned": "Sem projeto",
    # -- flow/kmm.py -----------------------------------------------------------
    "Oblivious": "Alheio",
    "No measurement at all — the pre-kanban baseline.": (
        "Nenhuma medição — a linha de base anterior ao kanban."
    ),
    "Emerging": "Emergente",
    "Visualise the work and count what is in progress.": (
        "Visualizar o trabalho e contar o que está em andamento."
    ),
    "Defined": "Definido",
    "Average lead time, throughput, aging and classification.": (
        "Lead time médio, vazão, envelhecimento e classificação."
    ),
    "Managed": "Gerenciado",
    "Distributions, service level expectations, CFD and flow efficiency.": (
        "Distribuições, expectativas de nível de serviço, CFD e eficiência de fluxo."
    ),
    "Quantitatively Managed": "Gerenciado quantitativamente",
    "Probabilistic forecasts, variability, failure demand and flow debt.": (
        "Previsões probabilísticas, variabilidade, demanda de falha e dívida de fluxo."
    ),
    "Optimizing": "Otimizando",
    "End-to-end lead time, bottleneck analysis and controlled experiments.": (
        "Lead time de ponta a ponta, análise de gargalos e experimentos controlados."
    ),
    "Congruent": "Congruente",
    "Portfolio roll-up and alignment between flow and business outcomes.": (
        "Consolidação do portfólio e alinhamento entre fluxo e resultados de negócio."
    ),
    "Board in use": "Quadro em uso",
    (
        "Anderson's first rung asks only that work be pulled onto a visual board at all — "
        "Yoshima calls this simply colocar o trabalho na parede (put the work on the wall), the "
        "precondition every later metric depends on."
    ): (
        "O primeiro degrau de Anderson pede apenas que o trabalho seja levado a um quadro "
        "visual — Yoshima chama isso simplesmente de colocar o trabalho na parede, a "
        "pré-condição de que toda métrica posterior depende."
    ),
    "Put real, current work onto this board — an empty board cannot be measured.": (
        "Coloque trabalho real e atual neste quadro — um quadro vazio não pode ser medido."
    ),
    "WIP count": "Contagem de WIP",
    (
        "Reading the number of items currently in progress is Anderson's ML1 threshold metric — "
        "the first number a team can state without any movement history at all."
    ): (
        "Ler o número de itens em andamento é a métrica de entrada do ML1 de Anderson — o "
        "primeiro número que um time consegue informar sem nenhum histórico de movimentação."
    ),
    (
        "Mark one column as the commitment point and one as the delivery point so "
        "work-in-progress has a clear boundary to be counted against."
    ): (
        "Marque uma coluna como ponto de compromisso e outra como ponto de entrega para que o "
        "trabalho em andamento tenha uma fronteira clara para ser contado."
    ),
    "Cards visualised": "Cartões visualizados",
    (
        "Representing each unit of work as its own card — not a batched checklist — is what "
        "turns a board into a kanban system rather than a generic to-do list, per Anderson's "
        "ML1 definition."
    ): (
        "Representar cada unidade de trabalho como um cartão próprio — e não como um checklist "
        "em lote — é o que transforma um quadro num sistema kanban em vez de uma lista de "
        "tarefas genérica, segundo a definição de ML1 de Anderson."
    ),
    "Add the team's actual work items as individual cards on the board.": (
        "Adicione os itens de trabalho reais do time como cartões individuais no quadro."
    ),
    "Average lead time": "Lead time médio",
    (
        "Computing a mean lead time from real movement timestamps — not a guess — is Anderson's "
        "ML2 threshold: the first quantitative promise a team can make to a customer."
    ): (
        "Calcular um lead time médio a partir dos horários reais de movimentação — e não de um "
        "palpite — é a entrada do ML2 de Anderson: a primeira promessa quantitativa que um time "
        "pode fazer a um cliente."
    ),
    "Deliver at least one card through the board so a lead time can be observed.": (
        "Entregue ao menos um cartão pelo quadro para que um lead time possa ser observado."
    ),
    (
        "Counting delivered items per period gives Defined-stage teams their first capacity "
        "signal — what Yoshima's coaching practice calls o ritmo do time (the team's rhythm)."
    ): (
        "Contar os itens entregues por período dá aos times no estágio Definido seu primeiro "
        "sinal de capacidade — o que a prática de coaching de Yoshima chama de o ritmo do time."
    ),
    "Deliver work regularly enough that a per-week count becomes meaningful.": (
        "Entregue trabalho com regularidade suficiente para que a contagem semanal faça sentido."
    ),
    "WIP aging": "Envelhecimento do WIP",
    (
        "Watching how long committed items have already been sitting — aging WIP, not just a "
        "headcount — is Defined-stage evidence that the team tracks flow, not only completion."
    ): (
        "Acompanhar há quanto tempo os itens comprometidos estão parados — o envelhecimento do "
        "WIP, não só a contagem — é a evidência do estágio Definido de que o time acompanha o "
        "fluxo, não apenas a conclusão."
    ),
    "Commit at least one card and leave it in flight so its age can be tracked.": (
        "Comprometa ao menos um cartão e deixe-o em andamento para que sua idade possa ser "
        "acompanhada."
    ),
    "Blocker count": "Contagem de bloqueios",
    (
        "Recording blockers explicitly, with a start and end time, is what turns 'we were "
        "stuck' into a number Anderson's ML2 teams can act on."
    ): (
        "Registrar bloqueios explicitamente, com início e fim, é o que transforma 'ficamos "
        "travados' num número sobre o qual os times de ML2 de Anderson podem agir."
    ),
    "Log blockers on cards as they happen, with a category, instead of only in conversation.": (
        "Registre os bloqueios nos cartões quando acontecerem, com uma categoria, em vez de só "
        "comentá-los em conversa."
    ),
    "Work-item types": "Tipos de item de trabalho",
    (
        "Distinguishing types of demand — feature, defect, risk, debt, compliance — is "
        "Anderson's ML2 requirement for classifying work before you can improve how it flows."
    ): (
        "Distinguir os tipos de demanda — funcionalidade, defeito, risco, dívida técnica, "
        "conformidade — é o requisito de ML2 de Anderson para classificar o trabalho antes de "
        "melhorar como ele flui."
    ),
    "Tag delivered cards with more than one value area or demand type as they actually differ.": (
        "Marque os cartões entregues com mais de uma área de valor ou tipo de demanda, conforme "
        "eles realmente diferem."
    ),
    "Lead-time distribution & percentiles": "Distribuição e percentis de lead time",
    (
        "Reporting the full percentile spread (50/70/85/95), not just the mean, is Anderson's "
        "ML3 requirement — averages hide the tail that actually breaks customer promises."
    ): (
        "Relatar toda a faixa de percentis (50/70/85/95), e não só a média, é o requisito de "
        "ML3 de Anderson — médias escondem a cauda que de fato quebra as promessas ao cliente."
    ),
    "Deliver at least {n} cards in the window so the percentile spread stops being a guess.": (
        "Entregue ao menos {n} cartões na janela para que a faixa de percentis deixe de ser um "
        "palpite."
    ),
    "SLE & attainment": "SLE e cumprimento",
    (
        "A committed Service Level Expectation with a measured attainment rate is the "
        "customer-facing promise Anderson's Managed stage exists to keep."
    ): (
        "Uma expectativa de nível de serviço (SLE) assumida, com taxa de cumprimento medida, é "
        "a promessa ao cliente que o estágio Gerenciado de Anderson existe para cumprir."
    ),
    (
        "Set a board SLE (days + target percentile) and deliver enough cards to measure "
        "attainment against it."
    ): (
        "Defina um SLE para o quadro (dias + percentil-alvo) e entregue cartões suficientes "
        "para medir o cumprimento."
    ),
    "Cumulative flow diagram": "Diagrama de fluxo cumulativo",
    (
        "The CFD is Anderson's canonical Managed-stage instrument: it exposes WIP, lead time "
        "and where work is piling up in one picture instead of three separate numbers."
    ): (
        "O CFD é o instrumento canônico do estágio Gerenciado de Anderson: mostra o WIP, o lead "
        "time e onde o trabalho está se acumulando numa só imagem, em vez de três números "
        "separados."
    ),
    (
        "Accumulate at least {n} cards of movement history so the cumulative bands are readable "
        "rather than noise."
    ): (
        "Acumule histórico de movimentação de ao menos {n} cartões para que as faixas "
        "cumulativas sejam legíveis, e não ruído."
    ),
    (
        "Measuring on-time delivery for fixed-date commitments is how Managed-stage teams prove "
        "a class-of-service policy actually works, not just exists on paper."
    ): (
        "Medir a entrega no prazo dos compromissos de data fixa é como os times do estágio "
        "Gerenciado provam que uma política de classe de serviço funciona de verdade, e não só "
        "existe no papel."
    ),
    "Deliver at least one fixed_date card against a due_date.": (
        "Entregue ao menos um cartão de data fixa (fixed_date) com data de entrega (due_date)."
    ),
    "Class-of-service mix": "Mix de classes de serviço",
    (
        "Quantifying the delivered mix across expedite, standard, fixed-date and intangible is "
        "what lets a Managed-stage team see whether its service classes are a real policy or "
        "just labels."
    ): (
        "Quantificar o mix entregue entre urgente, padrão, data fixa e intangível é o que "
        "permite a um time do estágio Gerenciado ver se suas classes de serviço são uma "
        "política real ou só rótulos."
    ),
    "Deliver cards under at least two different service classes.": (
        "Entregue cartões em pelo menos duas classes de serviço diferentes."
    ),
    (
        "Splitting lead time into touch and wait time is Anderson's Managed-stage diagnostic "
        "for where to intervene — Yoshima teaches Brazilian teams to attack the queue first, "
        "not ask people to work faster."
    ): (
        "Dividir o lead time em tempo de trabalho e tempo de espera é o diagnóstico do estágio "
        "Gerenciado de Anderson para saber onde intervir — Yoshima ensina os times brasileiros "
        "a atacar primeiro a fila, e não a pedir que as pessoas trabalhem mais rápido."
    ),
    (
        "Deliver at least {n} cards through activity and queue columns so the touch/wait split "
        "is trustworthy."
    ): (
        "Entregue ao menos {n} cartões passando por colunas de atividade e de fila para que a "
        "divisão trabalho/espera seja confiável."
    ),
    "Monte Carlo forecasts": "Previsões Monte Carlo",
    (
        "Probabilistic forecasting from resampled weekly throughput is Anderson's "
        "Quantitatively Managed hallmark: a date that comes with a stated confidence instead of "
        "a single-point guess."
    ): (
        "A previsão probabilística por reamostragem da vazão semanal é a marca do estágio "
        "Gerenciado quantitativamente de Anderson: uma data que vem com uma confiança "
        "declarada, em vez de um palpite pontual."
    ),
    "Accumulate at least {n} weeks of delivered throughput history to resample from.": (
        "Acumule ao menos {n} semanas de histórico de vazão entregue para reamostrar."
    ),
    (
        "Tracking the coefficient of variation of weekly throughput tells a Quantitatively "
        "Managed team whether its own forecasts can be trusted before it ships them to a "
        "stakeholder."
    ): (
        "Acompanhar o coeficiente de variação da vazão semanal diz a um time Gerenciado "
        "quantitativamente se suas próprias previsões são confiáveis antes de levá-las a um "
        "stakeholder."
    ),
    (
        "Keep delivering for {n}+ weeks so variability is measured over a real sample, not a "
        "handful of lucky weeks."
    ): (
        "Continue entregando por {n}+ semanas para que a variabilidade seja medida numa amostra "
        "real, e não em meia dúzia de semanas de sorte."
    ),
    "Failure demand %": "% de demanda de falha",
    (
        "Separating failure demand from value demand is Anderson's Quantitatively Managed "
        "requirement for pricing the true cost of poor quality upstream."
    ): (
        "Separar a demanda de falha da demanda de valor é o requisito do estágio Gerenciado "
        "quantitativamente de Anderson para precificar o custo real da baixa qualidade a "
        "montante."
    ),
    (
        "Tag at least {n} delivered cards with a demand_type so failure demand has a "
        "denominator worth trusting."
    ): (
        "Marque ao menos {n} cartões entregues com um tipo de demanda (demand_type) para que a "
        "demanda de falha tenha um denominador confiável."
    ),
    "Flow debt (expedite impact)": "Dívida de fluxo (impacto dos urgentes)",
    (
        "Quantifying flow debt — how much slower standard work moves because of expedite "
        "interruptions — is what lets Quantitatively Managed teams price their own "
        "class-of-service policy instead of assuming it is free."
    ): (
        "Quantificar a dívida de fluxo — quanto mais devagar o trabalho padrão anda por causa "
        "das interrupções urgentes — é o que permite aos times Gerenciados quantitativamente "
        "precificar a própria política de classes de serviço, em vez de supor que ela é de "
        "graça."
    ),
    "Deliver enough expedite and standard cards that both have a measurable p85 lead time.": (
        "Entregue cartões urgentes e padrão suficientes para que ambos tenham um lead time p85 "
        "mensurável."
    ),
    "Discovery (upstream) lead time": "Lead time de descoberta (upstream)",
    (
        "Measuring upstream discovery lead time alongside delivery lead time is Anderson's "
        "Quantitatively Managed step toward managing the whole value stream, not just the build "
        "phase."
    ): (
        "Medir o lead time de descoberta no upstream junto com o lead time de entrega é o passo "
        "do estágio Gerenciado quantitativamente de Anderson rumo à gestão de todo o fluxo de "
        "valor, e não só da fase de construção."
    ),
    (
        "Commit at least {n} cards that first spent time upstream so discovery lead time has a "
        "real sample."
    ): (
        "Comprometa ao menos {n} cartões que tenham passado antes pelo upstream para que o lead "
        "time de descoberta tenha uma amostra real."
    ),
    (
        "Checking that observed WIP, throughput and lead time agree via Little's Law is the "
        "statistical proof Anderson demands before anyone trusts a forecast built on top of "
        "them."
    ): (
        "Conferir que WIP observado, vazão e lead time concordam pela Lei de Little é a prova "
        "estatística que Anderson exige antes que alguém confie numa previsão construída sobre "
        "eles."
    ),
    (
        "Deliver a stable, non-empty stream of work so WIP, throughput and lead time can be "
        "reconciled."
    ): (
        "Entregue um fluxo de trabalho estável e não vazio para que WIP, vazão e lead time "
        "possam ser conciliados."
    ),
    "Envelope-to-delivery lead time": "Lead time do envelope até a entrega",
    (
        "Measuring the full envelope-to-delivery lead time — including sealed pre-discovery "
        "time — is Anderson's Optimizing-stage view of the entire customer promise, not only "
        "the visible part of the flow."
    ): (
        "Medir todo o lead time do envelope até a entrega — incluindo o tempo lacrado antes da "
        "descoberta — é a visão do estágio Otimizando de Anderson sobre a promessa inteira ao "
        "cliente, e não só a parte visível do fluxo."
    ),
    (
        "Turn on 'count sealed-envelope time in metrics' for this board and deliver cards that "
        "passed through the envelope."
    ): (
        "Ative 'contar o tempo de envelope lacrado nas métricas' neste quadro e entregue "
        "cartões que passaram pelo envelope."
    ),
    "Bottleneck / queue analysis": "Análise de gargalos / filas",
    (
        "Ranking queues by their wait-time Pareto to find the single worst offender is "
        "Anderson's Optimizing-stage discipline of fixing the constraint instead of everything "
        "at once."
    ): (
        "Ordenar as filas pelo Pareto do tempo de espera para achar a pior delas é a disciplina "
        "do estágio Otimizando de Anderson: corrigir a restrição em vez de tudo ao mesmo tempo."
    ),
    "Deliver enough cards that a wait-time Pareto across queue columns can be ranked.": (
        "Entregue cartões suficientes para que um Pareto do tempo de espera entre as colunas de "
        "fila possa ser ordenado."
    ),
    "Cross-board capability comparison": "Comparação de capacidade entre quadros",
    (
        "Comparing lead-time capability across boards — or before/after a policy change via a "
        "cloned simulator board — is Anderson's Optimizing-stage practice of treating the "
        "process itself as an experiment."
    ): (
        "Comparar a capacidade de lead time entre quadros — ou antes/depois de uma mudança de "
        "política, por meio de um quadro clonado no simulador — é a prática do estágio "
        "Otimizando de Anderson de tratar o próprio processo como um experimento."
    ),
    (
        "Comparing lead-time capability across boards — or before/after a policy change via a "
        "board cloned in the Nagare simulator — is Anderson's Optimizing-stage practice of "
        "treating the process itself as an experiment."
    ): (
        "Comparar a capacidade de lead time entre quadros — ou antes/depois de uma mudança de "
        "política, por meio de um quadro clonado no simulador do Nagare — é a prática do "
        "estágio Otimizando de Anderson de tratar o próprio processo como um experimento."
    ),
    "Stand up at least one more operational board so capability can be compared.": (
        "Monte ao menos mais um quadro operacional para que a capacidade possa ser comparada."
    ),
    "WIP experiment outcomes": "Resultados de experimentos de WIP",
    (
        "Running a WIP experiment and reading the resulting Little's-Law delta is the "
        "Optimizing-stage proof that lowering WIP shortens lead time — Anderson's central "
        "kanban claim made empirical rather than theoretical."
    ): (
        "Rodar um experimento de WIP e ler a variação resultante pela Lei de Little é a prova "
        "do estágio Otimizando de que reduzir o WIP encurta o lead time — a afirmação central "
        "de Anderson sobre kanban, tornada empírica em vez de teórica."
    ),
    "Clone this board into the simulator and run a wip_experiment scenario.": (
        "Clone este quadro no simulador e rode um cenário de experimento de WIP (wip_experiment)."
    ),
    "Run a WIP experiment in the Nagare simulator.": (
        "Rode um experimento de WIP no simulador do Nagare."
    ),
    "Portfolio roll-up across projects": "Consolidação do portfólio entre projetos",
    (
        "Rolling flow metrics up across every project onto one portfolio view is Anderson's "
        "Congruent-stage requirement — flow data that speaks to the business rather than only "
        "to the delivery team."
    ): (
        "Consolidar as métricas de fluxo de todos os projetos numa única visão de portfólio é o "
        "requisito do estágio Congruente de Anderson — dados de fluxo que falam com o negócio, "
        "e não só com o time de entrega."
    ),
    "Deliver work under at least two distinct projects so a portfolio roll-up is possible.": (
        "Entregue trabalho em pelo menos dois projetos distintos para que a consolidação do "
        "portfólio seja possível."
    ),
    "Business-outcome alignment": "Alinhamento com resultados de negócio",
    (
        "Tying flow metrics to explicit business-value categories — feature, defect, risk, "
        "debt, compliance — is Anderson's Congruent-stage bridge between kanban mechanics and "
        "business outcomes, the same dashboard Yoshima insists Brazilian leadership and "
        "delivery teams should both read."
    ): (
        "Ligar as métricas de fluxo a categorias explícitas de valor de negócio — "
        "funcionalidade, defeito, risco, dívida técnica, conformidade — é a ponte do estágio "
        "Congruente de Anderson entre a mecânica do kanban e os resultados de negócio, o mesmo "
        "painel que Yoshima insiste que lideranças e times de entrega brasileiros leiam juntos."
    ),
    "Classify delivered work across at least three distinct value areas.": (
        "Classifique o trabalho entregue em pelo menos três áreas de valor distintas."
    ),
    (
        "ML6 Congruent is fully satisfied — keep monitoring so a regression in any metric is "
        "caught before it erodes the ladder."
    ): (
        "O ML6 Congruente está plenamente atendido — continue monitorando para que uma "
        "regressão em qualquer métrica seja percebida antes de corroer a escada."
    ),
    "{level} is fully satisfied; no further data gaps block the next level.": (
        "{level} está plenamente atendido; nenhuma lacuna de dados bloqueia o próximo nível."
    ),
    # -- flow/forecast.py ------------------------------------------------------
    "item": "item",
    "items": "itens",
    "week": "semana",
    "weeks": "semanas",
    "less often than 95% of the time": "em menos de 95% das vezes",
    "No WIP limits": "Sem limites de WIP",
    "Loose buffers (x2)": "Buffers folgados (x2)",
    "Board policy": "Política do quadro",
    "Tight buffers (x0.6)": "Buffers apertados (x0,6)",
    "Very tight buffers (x0.35)": "Buffers muito apertados (x0,35)",
    "Starved (x0.35 everywhere)": "Sufocado (x0,35 em tudo)",
    "How long to deliver {backlog} {items}?": "Quanto tempo para entregar {backlog} {items}?",
    (
        "A coin-flip date is {optimistic_date} ({optimistic_weeks} {optimistic_word}); the date "
        "worth committing to is {safe_date} ({safe_weeks} {safe_word})."
    ): (
        "A data cara ou coroa é {optimistic_date} ({optimistic_weeks} {optimistic_word}); a "
        "data que vale a pena assumir como compromisso é {safe_date} ({safe_weeks} "
        "{safe_word})."
    ),
    "How many items can we deliver in {days} days?": (
        "Quantos itens conseguimos entregar em {days} dias?"
    ),
    (
        "Plan on {safe_items} {items} by {date} and you will be right {confidence}% of the "
        "time; plan on {optimistic_items} and it is a coin flip."
    ): (
        "Planeje {safe_items} {items} até {date} e você acertará em {confidence}% das vezes; "
        "planeje {optimistic_items} e é cara ou coroa."
    ),
    "Not enough delivered work to compare these policies.": (
        "Trabalho entregue insuficiente para comparar estas políticas."
    ),
    (
        "“{winner}” is the policy to run: 85th-percentile system lead time {lead} days against "
        "{loosest_lead} days under “{loosest}”{lead_change}, while throughput held at "
        "{throughput} items/week{throughput_change}."
    ): (
        "“{winner}” é a política a adotar: lead time do sistema no percentil 85 de {lead} dias "
        "contra {loosest_lead} dias em “{loosest}”{lead_change}, com a vazão mantida em "
        "{throughput} itens/semana{throughput_change}."
    ),
    "{share}% expedite": "{share}% urgente",
    "Not enough standard-class deliveries to measure the cost of expediting.": (
        "Entregas da classe padrão insuficientes para medir o custo dos urgentes."
    ),
    (
        "Going from {from_share}% to {to_share}% expedite pushed the standard class's "
        "85th-percentile lead time from {from_lead} to {to_lead} days{change}."
    ): (
        "Passar de {from_share}% para {to_share}% de urgentes elevou o lead time da classe "
        "padrão no percentil 85 de {from_lead} para {to_lead} dias{change}."
    ),
    "No policy produced enough delivered work to judge.": (
        "Nenhuma política produziu trabalho entregue suficiente para avaliar."
    ),
    "Replayed {arrivals} real arrivals from this board through {policies} policies.": (
        "{arrivals} chegadas reais deste quadro reproduzidas em {policies} políticas."
    ),
    "Lead time (avg)": "Lead time (média)",
    "Lead time (85th)": "Lead time (percentil 85)",
    "Customer lead time (avg)": "Lead time do cliente (média)",
    "the baseline run": "a execução de referência",
    "“{baseline}” is the only run with enough delivered work to compare.": (
        "“{baseline}” é a única execução com trabalho entregue suficiente para comparar."
    ),
    (
        "“{baseline}” is still the best of these {runs} runs: {lead} days at the 85th "
        "percentile on {throughput} items/week. Nothing tried here beat it without costing "
        "delivery."
    ): (
        "“{baseline}” continua sendo a melhor destas {runs} execuções: {lead} dias no percentil "
        "85 com {throughput} itens/semana. Nada do que foi testado aqui a superou sem "
        "prejudicar a entrega."
    ),
    (
        "“{winner}” wins: 85th-percentile lead time {lead} days ({change}% vs “{baseline}”) at "
        "{throughput} items/week{throughput_change}."
    ): (
        "“{winner}” vence: lead time no percentil 85 de {lead} dias ({change}% vs. "
        "“{baseline}”) com {throughput} itens/semana{throughput_change}."
    ),
    (
        "“{winner}” wins: 85th-percentile lead time {lead} days at {throughput} "
        "items/week{throughput_change}."
    ): (
        "“{winner}” vence: lead time no percentil 85 de {lead} dias com {throughput} "
        "itens/semana{throughput_change}."
    ),
    "Throughput has been steady enough that the risk band is a single week.": (
        "A vazão tem sido estável o bastante para que a faixa de risco seja de uma única semana."
    ),
    (
        "The {spread}-{weeks_word} gap between them is the cost of throughput variability, not "
        "of the work itself."
    ): (
        "A diferença de {spread} {weeks_word} entre elas é o custo da variabilidade da vazão, "
        "não do trabalho em si."
    ),
    (
        "Dividing the backlog by average throughput would have promised {naive} {weeks_word} — "
        "a date this simulation only reaches {probability}."
    ): (
        "Dividir o backlog pela vazão média teria prometido {naive} {weeks_word} — uma data que "
        "esta simulação só atinge {probability}."
    ),
    "about {confidence}% of the time": "em cerca de {confidence}% das vezes",
    "Those {gap} {items} are the negotiating room variability takes away from you.": (
        "A diferença de {gap} {items} é a margem de negociação que a variabilidade tira de você."
    ),
    "Average throughput alone would have promised {naive}.": (
        "Só a vazão média teria prometido {naive}."
    ),
    (
        "Average work in progress fell from {loosest_wip} to {wip} items ({change}%) — lead "
        "time tracked it almost exactly, which is Little's Law doing its job: the only way to "
        "finish sooner is to start less."
    ): (
        "O trabalho em andamento médio caiu de {loosest_wip} para {wip} itens ({change}%) — o "
        "lead time acompanhou quase exatamente, que é a Lei de Little fazendo seu trabalho: o "
        "único jeito de terminar antes é começar menos."
    ),
    (
        "Note the honest part: end-to-end customer lead time barely moved ({change}%). Tight "
        "WIP does not make the queue disappear, it moves it in front of the commitment point "
        "where it is still visible, still negotiable, and not yet a promise."
    ): (
        "Repare na parte honesta: o lead time do cliente, de ponta a ponta, quase não se mexeu "
        "({change}%). WIP apertado não faz a fila sumir; ele a move para antes do ponto de "
        "compromisso, onde ela continua visível, continua negociável e ainda não é uma "
        "promessa."
    ),
    (
        "The expedited items themselves came through in {lead} days at the 85th percentile — "
        "the queue did not get shorter, it got reordered."
    ): (
        "Os próprios itens urgentes passaram em {lead} dias no percentil 85 — a fila não ficou "
        "menor, foi reordenada."
    ),
    (
        "That is roughly {delay} days of delay imposed on the standard class for every extra "
        "item somebody called urgent."
    ): (
        "Isso dá cerca de {delay} dias de atraso impostos à classe padrão para cada item a mais "
        "que alguém chamou de urgente."
    ),
    (
        "Total throughput moved {change}% — expediting does not create capacity, it only "
        "decides who waits."
    ): ("A vazão total variou {change}% — urgência não cria capacidade, só decide quem espera."),
    "The board itself delivered {throughput} items/week over the same demand.": (
        "O próprio quadro entregou {throughput} itens/semana com a mesma demanda."
    ),
    "Measured against “{baseline}”:": "Em comparação com “{baseline}”:",
    "It carries {change}% work in progress to do it.": (
        "Para isso, carrega {change}% de trabalho em andamento."
    ),
    "Recommended policy inside that run: “{policy}”.": (
        "Política recomendada dentro dessa execução: “{policy}”."
    ),
    (
        "{confidence}% confidence: all {backlog} {items} delivered within {weeks} {weeks_word} "
        "— by {date}."
    ): (
        "{confidence}% de confiança: o backlog de {backlog} {items} fica pronto em até {weeks} "
        "{weeks_word}, ou seja, até {date}."
    ),
    "{confidence}% confidence: {value} {items} or more by {date}.": (
        "{confidence}% de confiança: {value} {items} ou mais até {date}."
    ),
    "Configuration {n}": "Configuração {n}",
    (
        "Past that point the trade turns: “{policy}” cut throughput to {throughput} items/week "
        "with the bottleneck ({bottleneck}) only {utilization}% busy — the limits are now below "
        "the team's capacity and the board is starving."
    ): (
        "Passado esse ponto, a troca se inverte: “{policy}” derrubou a vazão para {throughput} "
        "itens/semana, com o gargalo ({bottleneck}) ocupado só {utilization}% do tempo — os "
        "limites agora estão abaixo da capacidade do time e o quadro está sufocado."
    ),
    (
        "With this demand no WIP limit shortened the 85th-percentile system lead time without "
        "costing throughput: “{reference}” already finishes in {lead} days at {throughput} "
        "items/week. The board has slack, so the limits make no difference here."
    ): (
        "Com essa demanda, nenhum limite de WIP encurtou o lead time do sistema no percentil 85 "
        "sem custar vazão: “{reference}” já termina em {lead} dias, com {throughput} "
        "itens/semana. O quadro tem folga, então os limites não fazem diferença aqui."
    ),
    "Tighter limits only starve it: “{policy}” cut throughput to {throughput} items/week.": (
        "Limites mais apertados só o sufocam: “{policy}” derrubou a vazão para {throughput} "
        "itens/semana."
    ),
    "Keep the current policy and test again with more arrivals per week.": (
        "Mantenha a política atual e teste de novo com mais chegadas por semana."
    ),
    (
        "Past that point the trade turns: “{policy}” cut throughput to {throughput} items/week "
        "— the limits are now below the team's capacity and the board is starving."
    ): (
        "Passado esse ponto, a troca se inverte: “{policy}” derrubou a vazão para {throughput} "
        "itens/semana — os limites agora estão abaixo da capacidade do time e o quadro está "
        "sufocado."
    ),
    "Policy {n}": "Política {n}",
    "Forecast (50%)": "Previsão (50%)",
    "Forecast (85%)": "Previsão (85%)",
    "Forecast (95%)": "Previsão (95%)",
    "“{run}” forecasts {forecast} at 85% confidence{change}.": (
        "“{run}” prevê {forecast} com 85% de confiança{change}."
    ),
    # -- routers/simulator.py --------------------------------------------------
    "Monte Carlo When": "Monte Carlo: quando",
    "Monte Carlo How Many": "Monte Carlo: quantos",
    "Wip Experiment": "Experimento de WIP",
    "Class Of Service Mix": "Mix de classes de serviço",
    "Replay": "Reprodução",
    "expedite": "urgente",
    "standard": "padrão",
    "fixed_date": "de data fixa",
    "intangible": "intangível",
    "Removed {count} card(s) and their history.": (
        "{count} cartão(ões) removido(s), com o histórico."
    ),
    "Simulated {service_class} demand #{number}": "Demanda {service_class} simulada nº {number}",
    "Simulator": "Simulador",
}
