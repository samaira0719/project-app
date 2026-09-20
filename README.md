# Decide Well - Student Decision Lab

A full-stack decision-making companion for students (ages 14-25). Users log in, complete
the User Decision-Making Survey once, add tasks, and the **priority scoring engine**
ranks them - with **Gemini-powered explanations** of why each decision was made.

Every recommendation carries a **confidence score** and a **predicted satisfaction
score**, each with the arithmetic behind it. What the user reports back feeds a
**reinforcement loop** that retunes the weighting to them over time - opt-in, bounded,
and fully inspectable.

## Quick start

```bash
pip install -r requirements.txt
copy .env.example .env        # then fill in GEMINI_API_KEY and DATABASE_URL
python run.py                 # http://127.0.0.1:8000
```

API docs (Swagger): http://127.0.0.1:8000/docs

## Flow

1. **Log in / Sign up** - JWT auth; every user's tasks, survey and history are isolated.
   Sign-up presents the data-protection notice with one checkbox per purpose; the two
   optional ones are genuinely optional (see [Data privacy and consent](#data-privacy-and-consent)).
2. **New users** are routed to the survey wizard; returning users land on the dashboard.
3. **Add tasks** - category dropdown (Study, Purchases, Travel, Entertainment, Personal,
   Career, Health, Other) with due date. Category-specific behavior:
   - **Study** adds an estimated time to complete
   - **Travel** uses a date-only due date (no timestamp)
   - everything else is just title + due date + category
4. Tasks are stored per-user in **Neon Postgres** (`DATABASE_URL` in `.env`; falls back
   to local SQLite if unset).
5. The engine **auto-ranks** pending tasks; the Decide tab shows the Up Next card,
   factor breakdowns and an AI explanation.
6. Each decision returns a **confidence** and a **predicted satisfaction** percentage,
   both itemised. Afterwards the user says how it actually went, and that verdict
   retunes the engine for them - see [The feedback loop](#the-feedback-loop).

## Light and dark

The header carries a three-way switch: **Light**, **Dark**, or **Match my device**.
The choice lives in `localStorage` under `dw_theme` (System stores nothing and follows
`prefers-color-scheme`, reacting live if the OS flips while the app is open).

A small inline script in the `<head>` stamps `data-theme` on `<html>` before the first
paint, so a dark-mode user never sees a flash of the light palette while the module
script loads. Everything else is token-driven: `:root` holds the light palette and
`:root[data-theme="dark"]` re-points the same names, so components did not need
rewriting - only the handful of rules that named a colour directly instead of using a
token.

Notes on the dark palette:

- warm off-black (`#141517`) rather than blue-black, so it still reads as the same
  product as the warm-white light theme
- the primary action inverts to a light sage button with dark text; `--sage-deep`
  becomes the *light* end of the ramp, since it now has to carry text on a dark surface
- `.btn-dark` (the "Add" buttons and "Explain with AVEX") becomes a raised neutral
  rather than a literal inversion - a slab of near-white would have been the loudest
  thing on the page, which overstates a secondary action
- the Eisenhower tiles keep their four colours but drop in luminance so they read as
  surfaces instead of glowing, with light ink; every pair still clears 5:1
- the task chips inside those tiles become near-opaque dark glass, mirroring the
  near-opaque white chips in light mode, so normal text colours behave on them
- the login screen is untouched: it already ships its own dark palette

Contrast was measured on the live DOM in both themes rather than by eye. Dark mode
passes 4.5:1 for body text and 3:1 for large text on every view. **Light mode does not**:
its meta text (`--ink-3`, the due dates and captions) sits around 2.6:1, which predates
this work and was left alone rather than changed unasked - worth revisiting.

## The survey

The **User Decision-Making Survey** lives in [backend/survey_spec.py](backend/survey_spec.py)
- a single source of truth. The frontend renders the wizard from `GET /api/survey/spec`
and the backend validates submissions against the same spec.

It **branches**. Steps 1-4 are asked of everybody and carry every answer the scoring
engine and the decision engine actually read (`motivator`, `many_tasks`, `choice_factors`,
`decision_style`, `when_unsure` and the procrastination scales), so nothing downstream can
lose an input it depends on. Step 4 - *Where you need help* - is the branch point: each
later section names the support areas that unlock it.

| Branch | Unlocked by | Asks about |
|---|---|---|
| Studies | Studies | hardest subject, challenges, when you focus best, session length, how close a deadline gets before you start |
| Health & energy | Health | health consciousness, exercise, sleep, when energy crashes, what blocks self-care |
| Motivation & confidence | Motivation *or* Confidence | how often unmotivated, what kills motivation, restart effort, accountability, self-trust |
| Career | Career | priority, approach, clarity on what's next, what makes it hard |
| Travel planning | Travel Planning | hardest part, what you protect when something gives, lead time |
| Purchases & money | Purchases | deciding time, what influences you, hardest categories, regret, budget clarity |
| Managing time | Managing Time | first-task skill, life balance, planning horizon, overcommitting, where time leaks |
| Social life | Social Activity *or* Entertainment | biggest challenge, asking friends, what recharges you, going out when you'd rather rest |

Sections declare `depends_on` as `{"key": "support_areas", "contains": x}` or
`{..., "contains_any": [...]}`; the same two forms work per question. `validate_answers`
skips locked branches entirely - they are neither required nor kept, so deselecting an
area drops the answers that only existed because of it. The wizard renumbers live
("Step 4 of 5" the moment you pick one area, "of 6" for two), gives each branch its own
accent colour, and labels it *"Because you picked Studies"* so the path is visible.

The branch answers feed decisions through [Prompts/profile.py](backend/Prompts/profile.py)
- AVEX sees when you start, how long you concentrate, when your energy crashes and what
stalls you before it rates any option - and through
[behavioral.py](backend/behavioral.py), where e.g. *"I start the night before"* joins the
procrastination cluster and an after-lunch energy crash maps onto the decision-fatigue
literature. Neither layer changes a score.

> Trade-off worth knowing: `study_challenges`, `sleep_hours`/`exercise_freq` and
> `career_priority` sit in branches, so a student who doesn't pick those areas no longer
> supplies them and the matching category boost simply doesn't apply. Every consumer reads
> them with `.get()`, so this degrades cleanly rather than breaking - but it does mean an
> unpicked area gets no personalisation, which is the intended reading of "I don't need
> help here".

## The scoring model

Each pending task gets `S = 100*(wU*U + wC*C + wA*A) + 100*wE*E` (capped at 100),
where the effort term is a pure bonus - a time estimate can only raise a task's alarm
level, never lower it below tasks without estimates:

| Factor | Meaning | Formula |
|---|---|---|
| U | Urgency | `2^(-hours_left / 48)` - exponential deadline pressure (Travel uses departure date if earlier); overdue pins to 1 |
| C | Importance | calibrated per-category weight + survey boosts |
| E | Effort criticality | critical ratio `est_time / time_left` with a quick-win floor (Study only) |
| A | Aging | `1 - 2^(-days_open / 5)` - anti-starvation for old backlog |

**How the survey personalises the engine** (full derivation in
[backend/scoring.py](backend/scoring.py)):

- deadline-driven answers raise the urgency weight
- goal / long-term answers raise the importance weight
- procrastination signals raise the aging weight, so stale tasks resurface
- "easiest first" / reward-driven users get a stronger quick-win floor
- every support area picked boosts its matching task category; poor sleep or
  exercise answers nudge Health tasks upward

## The Eisenhower matrix

The Tasks tab has a **List / Matrix** toggle. The matrix places every pending task from
the *same* U and C factors that drive the ranking, so the grid and the list can never
disagree:

|  | Urgent | Not Urgent |
|---|---|---|
| **Important** | **Do** - green | **Decide** - cyan |
| **Not Important** | **Delegate** - red | **Delete** - grey |

```
urgent    means U >= 0.50  (exactly 48h to the effective deadline)
             or E >= 0.80 (the time budget is all but spent)
important means C >= 0.70  (personalised category importance)
```

Both cutoffs are the midpoints of their own scale rather than free parameters: `U = 0.5`
*is* one deadline half-life, and `C = 0.70` is where the calibrated category table
separates outcome-bearing categories (Study, Career, Health, Travel) from discretionary
ones. Each quadrant carries the finding that justifies it, and the *shape* of the grid
is read back to the user - a full "Delegate" quadrant triggers the mere-urgency warning,
an empty "Decide" quadrant means nothing was caught before its deadline.

The tiles use the classic four-D colour code. Every ink colour clears 5:1 contrast on its
own fill and tasks sit on white chips inside each tile, so the small print stays readable
rather than decorative.

### Dragging a task to another quadrant

Tasks can be dragged between quadrants. Because placement is *derived* from U and C rather
than stored, a drop writes a **placement override** (`tasks.quadrant_override`) -
deliberately scoped so that:

- the **score, rank and factor breakdown never change**; the engine stays the single
  source of numeric truth and the Decide tab is unaffected
- a moved task keeps a left accent and a reset button that hands it back to the engine, and the
  tooltip names the quadrant the engine had chosen (`computed_quadrant` on the API)
- `PATCH /api/tasks/{id}/quadrant` with `{"quadrant": null}` clears the override

Overriding is itself reported back under the grid, via Meehl's clinical-versus-actuarial
work: formulas beat expert judgement in most repeated predictions, *except* for the
"broken leg" case - a rare fact the formula cannot see. That is exactly when moving a task
is right, and the note says so in both directions.

The implementation uses **pointer events**, not HTML5 drag-and-drop, which never fires on
touch. A mouse starts dragging after 6px of travel; a finger has to press and hold ~320ms
first, so swiping across a task still scrolls the page. A plain click still opens the
decision workspace either way. While dragging, nearing the top or bottom of the viewport
auto-scrolls the page - without it the stacked mobile layout would put half the quadrants
out of reach.

## Per-task decisions

Clicking any task opens its **decision workspace**: list the options you're choosing
between (for Study: which subjects; for Purchases: which products; ...), keep or tune the
category's suggested criteria (each weighted 1-5), rate every option on every criterion,
and hit *Decide for me*. The engine ([backend/decision_engine.py](backend/decision_engine.py))
scores each option:

```
score_i = 100 * sum over j of ( w_j * r_ij / 5 )
w_j is proportional to user_weight_j * survey_multiplier(tag_j)
```

Criteria carry semantic tags (urgency / importance / interest / effort / cost / quality)
and the survey bends the weights - a deadline-driven student's "upcoming test" criterion
counts ~20% more; a price-conscious student's "value for money" counts ~20% more. Every
boost is reported as a note so the decision stays explainable, and Gemini explains the
winner in plain language. Decided tasks show their chosen option and get a **+4 momentum
bonus** in the global ranking (a made decision removes start-friction - act on it while
it's fresh).

## The audit

Every decision result carries an **Audit** panel that re-derives the answer so it can be
checked rather than trusted (`decision_engine.build_audit`). It is computed on read, so
decisions made before the panel existed get one too. It shows:

1. **The method** - Simple Additive Weighting (SAW), the weighted-sum form of MAUT
   (Fishburn 1967; Keeney & Raiffa 1976), with each step of the normalisation named.
2. **The weight table** - your 1-5 importance times the survey multiplier gives the effective
   weight, and that over the total gives its share of the score.
3. **Every point's origin** - an option by criterion grid where each cell is
   `100 * share * rating / 5`; the rows add up to the scores exactly.
4. **Why this option** - the margin over the runner-up plus three robustness checks:
   Pareto dominance, whether the win survives throwing the weights away (equal-weight
   recomputation), and whether it survives a wrong rating.
5. **A sensitivity analysis** (Triantaphyllou & Sánchez 1997) - the closed form for the
   smallest weight change that flips the result,

   ```
   delta_j = (1 - w_j) * D / (D - d_j)
D = sum over j of w_j * (v_Aj - v_Bj),   d_j = v_Aj - v_Bj
   ```

   plus the smallest single-rating error that would do the same, `x >= 5D / w_j`. Both are
   reported only where they stay feasible (weights inside [0,1], ratings inside 1-5).
6. **What the method assumes** - preferential independence, interval-scale ratings, a
   linear value function and full compensation, stated plainly so the model can be
   argued with.

## Behavioural science correlation

[backend/behavioral.py](backend/behavioral.py) maps the survey answers, the Eisenhower
distribution and each decision onto documented findings from judgement-and-decision-making
research. Every correlation reports **what in the user's own data triggered it**, **what
the engine did about it**, and **the citation**, in two kinds:

- **driver** - the finding explains a parameter the engine actually moved, and carries the
  before/after (for example `urgency weight 0.48 to 0.56 (+17%)`)
- **risk** - the finding predicts a distortion the user is exposed to. The engine surfaces
  it and names the countermeasure rather than silently "correcting" the answer.

It surfaces in three places: under the matrix (what the shape of the grid says), inside
the audit (what this decision sits on top of), and on the Insights tab via
`GET /api/insights/behavioral`, alongside the table of which engine parameters the survey
moved. The module is explanation-only - it never changes a score.

> On the Insights table: the three core weights are rescaled to sum to 1 *after* they are
> set, so a raised weight can still carry a smaller slice of the score when another rose
> further. Both the raw lever and the resulting share are shown, because either alone
> tells half the story.

## Decision tempo

Two questions on the Insights tab, asked of timestamps the app already collects:

- **Is the time spent per decision rising?** If so, the user is slowing down.
- **Is the number of decisions per day (or week) rising?** If so, they are deciding
  more freely - the everyday meaning of growing confidence.

Every step borrows a published result rather than inventing a metric
(`backend/decision_tempo.py`):

| Step | Method | Why this one |
|---|---|---|
| Normalise the raw latency | **Hick-Hyman law**, `T / log2(options + 1)` | Choice time grows with the log of the number of alternatives, so a six-option decision is *expected* to take longer. Without this the metric would mostly measure how many options someone typed. |
| Summarise a bucket | **Median**, not mean | One decision left open on a forgotten tab must not move the day. |
| Fit the trend | **Theil-Sen slope** (median of all pairwise slopes) | ~29% breakdown point. Least squares would let a single outlier manufacture a trend. |
| Test the trend | **Mann-Kendall**, with tie correction and continuity correction | Gives a p-value without assuming normality, which response-time data never has. |
| Report the practice effect | **Power law of practice**, `T = a x N^-b` | Newell & Rosenbloom. The fitted `b` is the learning rate; published values sit near 0.2-0.6. |

Latencies are logged before the slope is taken - reaction times are right-skewed and
roughly log-normal - which is why the speed figure reads as a **percentage change per
bucket** rather than a number of seconds.

Each slope becomes a 0-100 score, `50 +/- 50 * tanh(slope / 0.35)`, where 50 is the
honest "no signal" middle, following the 0-100 convention of the Decision Self-Efficacy
Scale. tanh rather than a clamp, so the score stays strictly monotonic in the slope
instead of saturating a range of very different users onto the same 100.

Scores are then **shrunk toward 50** by `n / (n + 4)`, where `n` is the number of
buckets. A trend read off four days is half-believed; one read off twenty is believed
almost entirely. The reliability figure and both Mann-Kendall p-values are printed under
"How this is calculated", so a confident-looking number can always be checked against
the evidence behind it.

### Where the timing comes from

The clock runs in the browser, from opening the decision panel to submitting it, because
it is the *user's* deliberation being measured and not the request's round trip. It uses
`performance.now()` so a system clock change cannot produce a negative elapsed time, it
pauses while the tab is hidden, and it restarts on Re-decide and Adjust ratings - each of
those is genuinely new thinking rather than a continuation.

Timings ride on the **append-only interaction log**, not on the decision row: a task
holds only its latest decision, whereas the log holds every one of them with the
timestamp that makes a trend possible. Three consequences follow, all intended:

- Re-deciding adds a point to the series instead of overwriting one.
- Nothing is recorded without the **personalization consent**, so a user who has not
  opted in sees the "still collecting" state rather than a chart built from data they
  declined to provide.
- Erasing the learning data erases the tempo history with it, and the export already
  carries it, because both operate on that same log.

Decisions made before the timer existed carry no seconds and are skipped rather than
imputed - a guessed latency would be indistinguishable from a real one in the trend.
Anything under 1.5s or over 45 minutes is discarded as a re-submit or an abandoned tab.

### The charts

Two small multiples, never one plot with two y-axes: seconds and counts share no scale,
and overlaying them would draw a correlation that is not in the data. Each panel carries
a single series, so colour never has to tell two things apart, and every verdict badge
pairs its colour with the word, so nothing is encoded by hue alone. The SVGs are drawn at
measured pixel width and redrawn on resize, so axis text stays legible on a phone instead
of being scaled down with the viewBox.

## Confidence and predicted satisfaction

Every decision comes back with two percentages, and neither is ever just asserted -
each ships the arithmetic that produced it, itemised, in the panel under the result.

**Confidence** answers *how much should this recommendation be trusted*. It is a
property of the decision itself, computed identically for every user, so two
confidence scores are directly comparable and a brand-new account gets a real one on
day one. Five components, weighted:

| Component | Max | What it measures |
|---|---|---|
| Lead over the runner-up | 30 | Clarity, `(S1 - S2) / S1`. Full marks at a 25% lead. |
| Survives being wrong | 25 | Pareto dominance, or how many rating points it would take to flip the result. Scaled down 25% when equal weights pick a different winner. |
| Quality of the inputs | 20 | You rated the options (1.00), AVEX rated them with reasons (0.80), or every rating defaulted to a neutral 3 (0.15). |
| Breadth of the criteria | 15 | Number of criteria (60%) and how evenly the weight is spread across them - a normalised Herfindahl index (40%). |
| Profile & history behind it | 10 | Whether the survey is on file, plus how much learned history stands behind the weighting. |

**Predicted satisfaction** answers a different question - *how happy is this
particular user likely to be with it* - and is learned rather than computed. It starts
from a 60% population baseline, moves to the user's own average once they have rated a
few decisions, and is then adjusted for how clear-cut the win is, whether it rests on
criteria their history says they value, who supplied the ratings, how often they act on
recommendations at all, and a learned correction for past over- or under-prediction.

The two are kept apart on purpose. A structurally excellent decision can still be one
the user ends up unhappy with, and collapsing both into a single "score" would hide
exactly the case worth knowing about.

Both are **frozen** onto the decision when it is made (`task_decisions.assessment`)
rather than recomputed on read. Comparing what was predicted then against what the user
reports now is the entire calibration signal.

## The feedback loop

A contextual bandit sitting on top of the rule-based engine - not a black box replacing
it. The engine still computes every number on screen; the loop can only nudge a handful
of **bounded multipliers** on parameters the engine already had, so anything it changes
still shows up in the Audit panel as a weight the user can read.

```
1. the user acts            ->  an Interaction row is written
2. the action is scored     ->  reward r in [-1, +1]
3. the reward is split      ->  credit c_k over the criteria that actually
                                produced the recommendation
4. the policy is updated    ->  theta_k <- theta_k * exp(alpha * r * c_k)
5. the next decision uses it -> blended in by trust, which grows with experience
```

**Reward.** An explicit verdict plus a 1-5 star rating:
`r = 0.65 * (stars - 3) / 2 + outcome`, where outcome is `+0.35` followed, `0.00`
adapted, `-0.35` rejected. Followed and five stars is exactly `+1.00`; rejected and one
star is exactly `-1.00`. Implicit signals are weaker, because the inference is weaker:
completing a decided task `+0.25`, correcting AVEX's ratings `-0.30`, re-running a
decision `-0.15`, dragging a task out of the quadrant the engine chose `-0.25`. Questions
asked of the assistant are stored as context and never scored.

**Credit assignment.** The reward is apportioned by each criterion's share of the
winning option's points - a criterion that supplied 40% of the points takes 40% of the
blame or the praise. Without this the reward would smear evenly over every parameter and
the learner would converge on nothing in particular. A quadrant drag is credited to
whichever axis moved: across the urgent line it blames urgency, across the other,
importance.

**The update** is the exponentiated-gradient (multiplicative weights / Hedge) rule of
Kivinen & Warmuth (1997) and Freund & Schapire (1997). Multiplicative rather than
additive because the parameters are multipliers - they must stay strictly positive - and
because the update is scale-free. Every multiplier is clamped to `[0.65, 1.55]`: the
learner tunes the engine, it cannot overrule it, and it cannot drive a factor to zero
however bad one week was. The learning rate decays as
`alpha = 0.22 / (1 + events / 25)`, the Robbins-Monro condition in its usual practical
form.

### Levels: why it improves over time

A learned multiplier is never applied at full strength. It is shrunk toward the neutral
1.0 by `trust = events / (events + 12)`, so the effective multiplier is
`1 + (theta - 1) * trust`. This is the empirical-Bayes shrinkage you would want anyway -
with two data points the posterior should stay near the prior - and it is what makes the
user-visible level honest rather than decorative: **the level *is* how much of the
learned policy is switched on.**

| Level | From | Label | |
|---|---|---|---|
| 1 | 0 events | Calibrating | Survey profile alone |
| 2 | 5 | Learning | First patterns, nudged gently |
| 3 | 15 | Adapting | Feedback is shaping the weighting |
| 4 | 35 | Tuned | Meaningfully personalised |
| 5 | 75 | Dialled in | Fine-grained corrections |

The Insights tab shows the whole policy in the open: what the engine now believes about
each criterion, how much of that belief is currently applied, the running averages it
keeps (satisfaction, follow rate, calibration bias) and the raw event log with each
reward. Sending one piece of feedback prints a receipt naming every multiplier it moved
and by how much - a claim about the user's own data should be checkable on the spot.

Everything here is gated on the personalization consent. With it off, no Interaction is
written, no policy is updated, and the satisfaction estimate falls back to the
population prior; the rest of the app is unaffected.

## Data privacy and consent

The sign-up screen presents the data-protection notice **before** the account exists,
rendered from `GET /api/privacy/notice` - the same module the server validates against,
so the text a user agreed to and the text on record cannot drift apart. It follows the
rules that actually bind a product like this (GDPR Arts. 4(11), 6, 7, 13, 15-22; UK
GDPR; India's DPDP Act 2023 ss. 5-6; the CCPA/CPRA notice-at-collection duty):

- **Freely given** - each optional purpose has a real, documented fallback, so declining
  still leaves a working product.
- **Specific and granular** - one checkbox per purpose. Bundling personalization into
  the terms box would void both.
- **Informed** - what is collected, why, on what legal basis, who receives it, how long
  it is kept, and what happens if you decline, all one tap away in the form itself.
- **Unambiguous, opt-in** - every box renders unticked. A pre-ticked box is not consent
  (Planet49, C-673/17).
- **Withdrawable as easily as given** - one call, effective on the next request.
- **Demonstrable** - every grant and withdrawal is appended to `consent_records` with
  the exact policy version (Art. 7(1)).
- **Age** - a self-declaration for the under-16 case (Art. 8).

| Scope | Required | If declined |
|---|---|---|
| `essential` | yes | No account; close it instead. |
| `personalization` | no | Nothing is recorded and nothing is learned. The survey-tuned engine and the full confidence score still work. |
| `ai_processing` | no | No task text is sent to Google. The assistant answers from the built-in matcher, insights come from the engine, and you rate options yourself. |

Bumping `POLICY_VERSION` in [backend/privacy.py](backend/privacy.py) re-prompts
everyone: `/api/privacy/consent` reports `stale` for any user whose stored version is
behind, and the Profile tab offers the new text.

### The rights are wired up, not just listed

A consent screen that promises access, portability, withdrawal and erasure and
implements none of them is worse than no screen at all. Each promise has an endpoint,
and each endpoint has a control in **Profile -> Data & privacy**:

| Right | Endpoint |
|---|---|
| Access & portability | `GET /api/privacy/export` - every row keyed to the user, as downloadable JSON (never the password hash) |
| Withdraw consent | `PATCH /api/privacy/consent` |
| Restrict processing | `DELETE /api/privacy/learning-data` - wipes the event log and resets the policy, keeps the account |
| Erasure | `DELETE /api/privacy/account` - cascades to every child table, no soft-delete, no recovery window |
| Object to automated decisions | the Audit panel, plus dragging, re-rating or ignoring any recommendation |

## Gemini insights

Set `GEMINI_API_KEY` in `.env` (free key: https://aistudio.google.com/apikey). The
**Explain with AI** button sends the ranking + factor breakdowns + the student's survey
profile to Gemini (`gemini-flash-latest`, with automatic fallback models) and returns a
plain-language explanation, stored in decision history. Without a key - or if Gemini is
down - a deterministic engine-generated explanation is used instead.

## The assistant bubble

A circle in the bottom-right corner of every signed-in screen. Hovering it previews the
panel, clicking pins it open, Escape or the X puts it away. It answers two kinds of
question in one conversation:

- **Your work.** What to start with, what's overdue, what's due this week, why a given
  task ranks where it does, what you've already decided. Grounded in your live account.
- **The app.** What a score means, how the matrix works, where a button is, what the
  survey changes, what the app can't do yet.

`POST /api/chat` rebuilds the account snapshot from the database on **every** turn -
pending tasks in engine order with their factor breakdowns, completed tasks, per-task
decisions, past Explain runs, the stats block and the survey profile - and puts it in
the system instruction alongside a product reference written from the actual behaviour
of the engine and the UI ([backend/Prompts/chat.py](backend/Prompts/chat.py)). The
client only ever sends the conversation text, so nothing in the browser can widen what
the assistant is allowed to see, and the answers can only cite that one user's data.

The assistant is read-only. It cannot add, complete or delete a task; it points at the
control instead.

Without a key - or if Gemini is down - [backend/chat_fallback.py](backend/chat_fallback.py)
answers from the same numbers with a small intent matcher, and the reply is tagged
**Engine** in the transcript so it is never passed off as the model. The thread lives in
`sessionStorage`, stamped with the user id, and is dropped on logout.

## Structure

```
backend/
  config.py       .env-driven settings
  database.py     engine (Neon Postgres via psycopg, or SQLite) + additive migrations
  models.py       User, SurveyResponse, Task, DecisionSnapshot,
                  Interaction, DecisionFeedback, UserPolicy, ConsentRecord
  schemas.py      Pydantic validation (category-conditional task fields)
  security.py     PBKDF2 password hashing + JWT bearer auth
  survey_spec.py  the User Decision-Making Survey (spec + validation)
  scoring.py      the priority scoring engine + Eisenhower placement
  decision_engine.py  option-level MCDA, and the audit that re-derives it
  behavioral.py   the behavioural-science correlation layer (explanation only)
  decision_tempo.py  the speed / confidence trend: Hick-Hyman normalisation,
                  Theil-Sen slope, Mann-Kendall test, power law of practice
  feedback.py     the reinforcement loop: reward, credit, policy update, and
                  the confidence / predicted-satisfaction scoring
  privacy.py      the data-protection notice and consent scopes (one source of
                  truth for the sign-up form and the server that validates it)
  gemini.py       Gemini REST client + fallback insights
  chat_fallback.py  deterministic answers for the assistant bubble
  Prompts/        one module per prompt (insight, decision, rating, chat)
  routers/        auth, privacy, survey, tasks, insights, chat, feedback
frontend/         vanilla-JS SPA (no build step) served at /
legacy/           the original CLI prototype, kept for reference
```
