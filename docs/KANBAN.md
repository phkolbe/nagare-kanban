# The method behind Nagare

*Why this product is shaped the way it is.*

Nagare (流れ, "flow") is not a generic task tracker with WIP counters bolted on. Every
screen, every constraint and every metric in it comes from a specific body of practice:
the **Kanban Method** as formulated by **David J. Anderson**, extended by the flow
metrics work of **Daniel Vacanti**, and — for those of us who learned it in Brazil —
taught with particular emphasis on **upstream discovery and options thinking** by
**Rodrigo Yoshima**.

This document is the rationale. If you ever wonder *"why does the tool refuse to do
this?"*, the answer is here. It is written for practitioners: coaches, team leads,
delivery managers and the engineers who have to live with the policies.

Everything below is synthesised in our own words. Where an idea belongs to a named
author, we attribute it and point you at the source rather than paraphrasing it at
length.

---

## 1. The starting position

Kanban is not a process. It is a **method for improving whatever process you already
have**, applied to a service that receives demand and delivers work.

Three foundational principles frame it:

1. **Start with what you do now.** Keep the current roles, titles, responsibilities and
   workflow. Kanban is applied *over* the existing system, not in place of it.
2. **Agree to pursue improvement through evolutionary change.** Big-bang redesigns
   trigger resistance proportional to their size; small changes that survive contact
   with reality compound.
3. **Encourage acts of leadership at every level.** Improvement suggestions are not the
   exclusive property of managers.

And a service-orientation that is easy to skip and expensive to skip:

- Understand and focus on your **customers' needs and expectations**.
- Manage the **work**, let people self-organise around it.
- Evolve **policies** to improve outcomes.

The practical consequence for a tool: Nagare never asks you to adopt a prescribed
workflow. Boards are built from your columns, your segments, your policies. What the
tool *does* insist on is that those policies be **explicit** and that the system
**tell the truth** about itself.

---

## 2. The six general practices, and where they live in Nagare

| Practice | What it means | Where it lives in the product |
|---|---|---|
| **Visualise** | Make invisible knowledge work visible: the items, their state, their type, their risk. | The board: upstream/downstream segments, column groups, service-class stripes, blocked flags, aging dots. |
| **Limit WIP** | Cap the number of items in progress so the system pulls instead of pushes. | Per-column, per-group and whole-flow WIP limits, enforced by the API at move time. |
| **Manage flow** | Watch the *work*, not the workers. Find and drain queues; reduce variability; shorten lead time. | Flow efficiency, aging WIP, CFD, blocker Pareto, Little's Law coherence. |
| **Make policies explicit** | Write down what "ready", "done", "expedite" actually mean so decisions stop being personal. | Column descriptions, WIP limits, classes of service, SLE, the commitment point, the movement rules the API enforces. |
| **Implement feedback loops** | Cadences at different frequencies: daily flow, delivery planning, operations review. | The metrics dashboard and the simulator are the raw material for the replenishment, delivery and operations reviews. |
| **Improve collaboratively, evolve experimentally** | Change the system with a model and a hypothesis, then measure. | The simulator: change a WIP limit or the expedite share, replay the same demand, read the delta. |

The order matters less than the fact that the last one is impossible without the first
five. You cannot run an experiment on a system you cannot see or measure.

---

## 3. Upstream, downstream, and the commitment point

Most tools model a single pipeline that starts at "backlog". That is the single most
expensive modelling error in knowledge work, because it hides the moment where the
economics change.

**The commitment point is the boundary where an option becomes a liability.**

- **Upstream** (also *discovery*, *option generation*): ideas, requests, hypotheses,
  half-understood problems. Nothing here is promised to anyone. Items may be discarded
  at no cost beyond the thinking already spent. Abandonment upstream is a *success* — it
  means you avoided building the wrong thing.
- **The commitment point**: the team and the customer agree that this item *will* be
  delivered. From here the clock that customers care about is running and the system
  owes an answer.
- **Downstream** (*delivery*): a pull system with WIP limits, working the committed
  items to completion.
- **The delivery point**: the item is in the customer's hands. Lead time stops.

Two consequences that Nagare enforces:

1. **Two different lead times.** *Discovery lead time* (creation → commitment) and
   *system lead time / cycle time* (commitment → delivery) behave completely
   differently and must never be averaged together. Customer lead time spans both.
   Nagare computes all three separately and stamps `committed_at` and `delivered_at`
   **immutably** the first time a card crosses each boundary.
2. **Different management styles either side of the line.** Upstream you want *many*
   options and cheap disposal. Downstream you want *few* items and fast completion.
   A single WIP limit across both would be nonsense, which is why Nagare has per-segment
   columns, per-group limits and a whole-flow limit that you can scope as you wish.

The board draws the commitment point as an explicit rail, tinted differently either
side, because a boundary you cannot see is a boundary nobody defends.

---

## 4. Options thinking and real options

Borrowed from financial options and brought into software by Chris Matts and Olav
Maassen (*Commitment*), and heavily emphasised in the upstream teaching Rodrigo Yoshima
does in Brazil:

> An idea is an **option**, not an obligation. An option has value, has an expiry, and
> should be exercised at the last responsible moment.

Three rules follow:

- **Never commit early unless you know why.** Committing converts a cheap option into an
  expensive liability, and the only thing you gain is certainty you probably did not
  need yet.
- **Options expire.** A request that has sat upstream for eight months is usually not an
  option any more — it is a decision nobody wants to make. Nagare shows upstream aging
  for this reason.
- **Commit when you have information, not when the calendar says so.** The replenishment
  cadence exists to make that decision deliberately and visibly.

### The sealed envelope

This is Nagare's most opinionated feature. Demand can be registered as a **sealed
envelope**: it exists, it is counted, it has a requester and a project — but its
contents are deliberately not opened, it carries no assignee, and it **cannot be moved
or worked** until someone explicitly opens it.

Why bother?

- It separates **capturing demand** from **starting discovery**. Teams that skip this
  step end up with an "upstream" that is really a push queue of half-analysed work.
- Opening an envelope is a *decision with a timestamp* (`opened_at`). That timestamp
  makes the cost of your intake process measurable: the time between arrival and
  opening is pure queue.
- A board toggle (`count_envelope_in_metrics`) lets you decide whether that pre-discovery
  wait belongs in customer lead time. Both answers are defensible; what is not
  defensible is not knowing which one you chose.

---

## 5. Discovery / upstream kanban

Upstream work is not a smaller version of delivery work. Its purpose is different:

| | Upstream | Downstream |
|---|---|---|
| Goal | generate and refine options; *discard* well | complete committed work fast and predictably |
| Success | abandoning the wrong ideas cheaply | short, predictable lead times |
| WIP limits | looser, often per-group; used to stop analysis sprawl | tight, per-column; used to force pull |
| Failure mode | analysis of things nobody will ever build | starting more than you can finish |

A workable upstream shape — the one the seeded Nagare board uses — is:

```
Envelope → Ideas → Discovery → Options → Ready to Commit ‖ Analysis → … → Delivered
  (sealed)  queue    activity    queue        queue      commitment point
```

- **Envelope**: captured, sealed, untouched.
- **Ideas**: opened, triaged, waiting for someone to think about them.
- **Discovery**: active work — interviews, spikes, sketches, sizing.
- **Options**: understood well enough to commit, but deliberately *not* committed.
  A healthy pool here is what makes replenishment a choice rather than a formality.
- **Ready to Commit**: the shortlist the replenishment meeting will pull from.

**Replenishment** is the cadence where options move across the commitment point. It has
a pull trigger (downstream has capacity), not a calendar trigger — though most teams run
it weekly for coordination reasons and pull *only as much as capacity allows*.

---

## 6. Classes of service

A class of service is a **policy**, not a priority label. It encodes the shape of the
**cost of delay** for that item and tells everyone in advance how the system should
treat it — before the argument, not during it.

| Class | Cost-of-delay shape | Legitimate use | Policy in Nagare |
|---|---|---|---|
| **Expedite** | Steep and immediate — every hour costs real money or trust. | Production incident, regulatory stop-ship, a customer contract that dies today. | Bypasses **column** WIP limits (flagged `wip_override: true`), pulled first, work on it before anything else. |
| **Fixed date** | Flat, then a cliff at a specific date. | Contractual deadlines, trade shows, regulatory dates, hardware launches. | Requires `due_date`; measured by *due-date performance*, scheduled backwards from the date using the lead-time distribution. |
| **Standard** | Roughly linear — sooner is better, no cliff. | The overwhelming majority of work. | FIFO within the class, governed by the SLE. |
| **Intangible** | Low now, potentially severe later. | Refactoring, upgrades, observability, tech-debt paydown, compliance groundwork. | Fills spare capacity; the first thing displaced by an expedite — which is exactly why the *share* of intangible work must be watched. |

### Rules that keep classes honest

- **Expedite must be rare and must hurt.** A commonly used starting policy is at most
  one expedite item in the system at a time, and the team stops other work to serve it.
  If more than roughly 5–10% of your throughput is expedite, the class has stopped
  meaning "emergency" and started meaning "this requester shouts loudest".
- **A class of service is agreed with the customer**, not assigned unilaterally by
  whoever files the ticket.
- **Fixed date is not "important"** — it is "worthless after Tuesday". If the date is
  soft, the class is standard.
- **Intangible is not filler.** If it never gets pulled, your system is accumulating a
  debt with a variable interest rate.

Nagare reports the class-of-service **mix** and the effect of expedite on the standard
class's lead time, so the conversation can be about data instead of temperament. The
simulator can quantify it directly: raise the expedite share and watch standard-class
lead time degrade.

---

## 7. WIP limits and Little's Law

### The law

For a **stable** queueing system observed over a period:

```
L = λ × W          average WIP = average arrival rate × average time in system
```

In flow terms teams usually write it as:

```
Average Lead Time = Average WIP ÷ Average Throughput
```

The assumptions matter and are routinely ignored:

- The system is **stable**: arrivals ≈ departures over the observation window.
- All work that enters **eventually exits** (nothing is silently deleted or hidden).
- WIP is neither growing nor shrinking systematically across the window.
- Units are consistent, and the *same* items are counted in all three quantities.

Nagare reports all three quantities together plus a **coherence ratio** rather than
quoting one in isolation. When the ratio drifts far from 1, that is not a bug in the
maths — it is a signal that your system is not stable, usually because WIP is growing.

### Why limit WIP

The law's practical reading: **for a given throughput, the only lever on lead time is
WIP.** You cannot make a team deliver faster by giving it more to do simultaneously —
you can only make each individual item take longer.

Additional effects that do not show up in the equation:

- **Queues dominate lead time.** In most knowledge work, an item spends the large
  majority of its life waiting, not being worked on.
- **Context switching is not free.** Multitasking adds real overhead *and* variability.
- **Limits create the pull signal.** A full column is a request for help, and it is the
  only mechanism that reliably surfaces bottlenecks.
- **Blocked items become visible.** With no limit, a blocked item is invisible because
  everyone just starts something else.

### Practical limit setting

- Start from **current** WIP, not from an ideal. Count what is in each column today and
  set the limit slightly below it. Then tighten.
- Limit the **queue columns** (the "done" buffers), not only the activity columns —
  those buffers are where lead time actually goes.
- Use **group limits** when several columns form one stage of work (e.g. all of
  Discovery), and a **whole-flow limit** when the meaningful constraint is the team, not
  any one step.
- A **minimum** WIP is advisory, never blocking. It marks a *starving* column — capacity
  standing idle downstream of a stalled step. Nagare surfaces it as a warning and never
  refuses a move because of it.

---

## 8. Wait time, touch time, and flow efficiency

Every column in Nagare declares its `kind`:

- `activity` — someone is actively working the item. Time here is **touch time**.
- `queue` — the item is waiting for someone or something. Time here is **wait time**.
- `envelope` — captured but unopened (pre-discovery).
- `done` — terminal.

```
Flow Efficiency = touch time ÷ (touch time + wait time) × 100
```

Typical measured flow efficiency in knowledge work sits somewhere in the **5–40%** band;
teams that measure it for the first time are usually shocked to find themselves under
15%. That is not laziness — it is queueing, dependencies and hand-offs.

Why this is the most actionable metric in the product:

- If efficiency is 15%, **making people type faster cannot help you**. Even doubling
  touch-time productivity moves lead time by a rounding error. Draining queues can halve
  it.
- It reframes improvement away from utilisation (a measure of how busy people look) and
  towards flow (a measure of how fast work moves). High utilisation and long lead times
  are the same phenomenon, not a paradox.

This is also why Nagare insists that every column declare `queue` or `activity` when it
is created. A board where everything is an "activity" cannot compute flow efficiency,
and the KMM dashboard will tell you so.

---

## 9. Pull systems

A pull system has one defining property: **work enters a step only when that step signals
capacity.** Nagare implements pull through three mechanisms.

1. **WIP maxima are enforced at move time.** A move into a column at its limit is
   rejected with `wip_limit_exceeded`, naming which policy broke (column, group or
   flow) and the current count. The tool does not warn — it declines. A limit that can
   be clicked past is decoration.
2. **Forward-only movement.** Cards move forward in the flow. There is no "drag it back
   to Analysis to make the board look better". A backward move requires an
   administrator, an explicit `force`, and it is recorded as `is_backward` with an audit
   entry. Doing rework? Say so, in a comment or a new card — do not silently rewind the
   history that your metrics are computed from.
3. **Replenishment across the commitment point** is an explicit act, not a side effect
   of somebody being idle.

**Expedite is the one sanctioned bypass**, and even then only for *column* limits, and
even then it is reported back in the response as `wip_override: true` and counted in
the demand metrics. The escape hatch is visible by design.

---

## 10. Forecasting: SLE and probability, not estimates

### Service Level Expectation

An SLE is a **two-part statement**: a time *and* a probability, derived from your own
historical lead-time distribution.

> "85% of standard-class items are delivered within 14 days of commitment."

It is not a target handed down by management, not a promise on any individual item, and
not something to negotiate. It is a measurement of the system you currently have. If
you dislike the number, change the system — do not change the number.

Nagare stores `sle_days` and `sle_percentile` per board, plots the percentile lines on
the lead-time scatter, and reports **SLE attainment** (the share of delivered items that
actually met it). Aging WIP is drawn against the same percentile bands so you can see,
today, which in-flight items are about to breach it — while there is still time to act.

### Why not estimates

Story points and hour estimates attempt to answer a probabilistic question with a single
number. They also consume real capacity in meetings, and their accuracy tends to be
weakly correlated with the effort spent producing them. The historical throughput of the
team already contains everything the estimate was trying to guess — including the
interruptions, the rework and the holidays.

### Monte Carlo

Nagare's forecasting engine samples the board's **weekly throughput history** with
replacement across ≥10,000 trials:

- **"When will N items be done?"** — repeatedly draw weeks until N items accumulate;
  the distribution of week counts is your answer.
- **"How many items by date D?"** — repeatedly draw the number of weeks until D; the
  distribution of totals is your answer.

Report percentiles, always:

| Percentile | Use it for |
|---|---|
| 50th | internal planning, "most likely" |
| 70th | a working assumption |
| **85th** | the date you actually commit to externally |
| 95th | contractual or high-consequence commitments |

Two disciplines make this work: **right-size** items so they are roughly comparable
(rather than estimating them), and **split** anything obviously enormous before
committing. Then count items, not points.

---

## 11. The Kanban Maturity Model, and which Nagare metric serves each level

The **KMM** (Anderson & Bozheva) maps organisational maturity across seven levels, each
with characteristic practices and outcomes. Nagare does not grade your organisation —
it does something more useful and more honest: for each level, it reports **which of the
metrics that level depends on your board can actually produce today**, and why the
missing ones are missing.

| Level | Focus | Metrics Nagare checks |
|---|---|---|
| **ML0 — Oblivious** | No process awareness; work is invisible. | Nothing is measured; the mere existence of a populated board moves you off ML0. |
| **ML1 — Emerging** | The team can see its work. | Cards visualised, WIP count, board in active use. |
| **ML2 — Defined** | Consistent process; work types are distinguished. | Average lead time, throughput, WIP aging, blocker count, work-item types. |
| **ML3 — Managed** | Customer-focused; policies are explicit and measured. | Lead-time distribution with percentiles, SLE and attainment, CFD, due-date performance, class-of-service mix, flow efficiency. |
| **ML4 — Quantitatively Managed** | The system is managed with data and models. | Monte Carlo forecasts, throughput variability, failure demand, flow debt, discovery (upstream) lead time, Little's Law coherence. |
| **ML5 — Optimizing** | Deliberate, measured improvement of the whole. | End-to-end (envelope → delivery) lead time, bottleneck/queue analysis, capability comparison across boards, WIP-experiment outcomes. |
| **ML6 — Congruent** | The whole organisation is aligned around flow of value. | Portfolio roll-up across projects, business-outcome alignment. |

`readiness_pct` for a level is simply *available metrics ÷ metrics for that level*. The
value of this view is not the score. It is the **"why"** attached to each unavailable
metric: *"no column on this board is marked `queue`, so wait time — and therefore flow
efficiency — cannot be computed."* That sentence is a concrete next practice, which is
what a maturity model is for.

A caution: maturity levels describe **the organisation's capability**, not a ladder to
be climbed for its own sake. A five-person team delivering predictably at ML3 needs
nothing from ML5.

---

## 12. Anti-patterns Nagare deliberately guards against

### Backward moves

**What people do:** drag a card back to a previous column when it needs rework, or to
"correct" a premature move.

**Why it is harmful:** the movement history *is* the metric. Rewinding it deletes real
wait time, inflates flow efficiency, corrupts the CFD, and quietly deletes the evidence
of a quality problem.

**What Nagare does:** rejects the move with `backward_move_blocked`. A master or
administrator can pass `force: true`, which records `is_backward = 1` plus an audit
entry — so a genuine correction is possible but never invisible. Rework is better
modelled as a *new* card with `demand_type = failure`, which then shows up as failure
demand, where it belongs.

### Unlimited WIP

**What people do:** run a board with no limits, or with limits nobody enforces, because
"we need flexibility".

**Why it is harmful:** without a limit there is no pull signal, no bottleneck visibility,
and no reason to finish anything. Lead time inflates in exact proportion to WIP.

**What Nagare does:** enforces maxima server-side at move time, at three scopes, and
colours over-limit columns red with a pulsing outline until the breach is resolved.

### Expedite abuse

**What people do:** mark everything urgent; expedite becomes the only way to get
anything done.

**Why it is harmful:** each expedite preempts standard work, which lengthens standard
lead time, which makes standard delivery unreliable, which makes people expedite —
a reinforcing loop that ends with a board where every item is on fire.

**What Nagare does:** flags every WIP bypass explicitly (`wip_override: true`), reports
the class-of-service mix as a first-class metric, and lets the simulator quantify the
damage: replay the same demand at 5% versus 25% expedite and read the effect on
standard-class lead time.

### Per-person swimlanes

**What people do:** one lane per team member, so everyone can see "their" work.

**Why it is harmful:** it optimises for **resource utilisation** instead of **flow**.
Work stops being a shared responsibility, hand-offs get hidden inside a lane, blocked
items sit unnoticed because they are "someone else's lane", and WIP limits become
personal quotas that guarantee everyone is busy and nothing finishes.

**What Nagare does:** offers no per-person swimlanes. Assignee is a card *field* — you
can filter by it, and the board shows an avatar — but the board's structure is the value
stream, not the org chart. Group columns by workflow stage, never by person.

### A few more the tool nudges you away from

- **"Done" columns used as parking.** They are queues. Give them a WIP limit; their
  contents are pure wait time and Nagare will show it.
- **Averages without distributions.** Every distribution in the product reports
  50/70/85/95 percentiles, and any sample under 8 items is stamped `low_confidence` so a
  chart drawn from three data points cannot masquerade as a forecast.
- **Aging ignored.** The oldest in-flight item is almost always the most valuable thing
  to discuss at standup, and almost never the one that gets discussed. Nagare sorts the
  aging view accordingly.
- **Metrics used on people.** Every metric here is a property of the *system*: lead
  time, throughput, flow efficiency, blocked time. None of them is a personal
  performance measure, and any attempt to use them that way will make the data stop
  being true within a month.

---

## 13. Running the cadences with Nagare

Feedback loops are a practice, not a feature — but the tool is built to feed them.

| Cadence | Typical rhythm | Question it answers | What to open |
|---|---|---|---|
| **Daily standup** | daily, ≤15 min | What is blocked or aging? | Board, filtered to blocked; aging WIP chart, right to left |
| **Replenishment** | weekly or on pull signal | What do we commit to next? | Upstream board, Options and Ready to Commit; whole-flow WIP |
| **Delivery planning** | per delivery cadence | What ships, and can we promise it? | Monte Carlo forecast, SLE attainment |
| **Service delivery review** | ~fortnightly | Are we meeting our SLE? | Metrics summary, lead-time scatter and distribution |
| **Operations review** | monthly | How do our services interact? | Multiple boards; demand mix, failure demand |
| **Risk review** | monthly | What keeps hurting us? | Blocker Pareto, blocked time, due-date performance |

Work the board **right to left**: the item nearest delivery is the one whose completion
frees capacity.

---

## 14. Further reading

Attribution, and where to go for the full treatment. Nagare synthesises these ideas; it
does not reproduce them.

- **David J. Anderson** — *Kanban: Successful Evolutionary Change for Your Technology
  Business* (2010). The foundational text: practices, classes of service, cadences.
- **David J. Anderson & Teodora Bozheva** — *Kanban Maturity Model* (2018). The ML0–ML6
  framework used by Nagare's KMM dashboard.
- **Daniel S. Vacanti** — *Actionable Agile Metrics for Predictability* (2015) and
  *When Will It Be Done?* (2019). Flow metrics, the correct use of Little's Law, aging
  WIP, and Monte Carlo forecasting.
- **Olav Maassen & Chris Matts** — *Commitment* (2013). Real options and the last
  responsible moment.
- **Patrick Steyaert** — Discovery Kanban / upstream kanban.
- **Donald G. Reinertsen** — *The Principles of Product Development Flow* (2009). Cost
  of delay, queueing economics, batch size.
- **John D. C. Little** — the 1961 proof of the queueing relationship everything above
  leans on.
- **Rodrigo Yoshima** (Aspercom, Brazil) — writing and training on upstream kanban,
  options thinking and the strategic use of the Kanban Method in Portuguese; the
  emphasis in this product on discovery, sealed demand and the commitment point owes a
  great deal to that teaching.

---

*See [API.md](API.md) for the REST reference and [../README.md](../README.md) for setup.*
