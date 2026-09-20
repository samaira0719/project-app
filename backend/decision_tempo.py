"""Decision tempo - is this student getting faster, and deciding more often?

Two plain questions, asked of timestamps the app already collects:

  * If the time spent on each decision is rising, they are slowing down.
  * If the number of decisions made per day (or week) is rising, they are
    deciding more freely - the everyday meaning of growing confidence.

Both are trend questions on short, noisy, unevenly spaced series, so the
statistics are chosen to survive exactly that. None of it is invented here;
each step is a published result doing the job it was published for.

  Hick-Hyman law            RT = a + b * log2(n + 1)
      Choice reaction time grows with the logarithm of the number of
      alternatives, so a five-option decision is *expected* to take longer
      than a two-option one. Every latency is divided by log2(options + 1)
      before anything else touches it, otherwise the metric would mostly be
      measuring how many options someone typed.

  Power law of practice     T = a * N^(-b)
      Newell & Rosenbloom's result that time-on-task falls as a power of the
      number of repetitions - a straight line in log-log space. The fitted
      exponent b is reported as the learning rate: b > 0 means practice is
      paying off. Published values usually sit near 0.2-0.6.

  Theil-Sen slope
      The median of all pairwise slopes. Non-parametric, with roughly a 29%
      breakdown point, so one 40-minute decision the user walked away from
      halfway through cannot invent a trend the way least squares would.

  Mann-Kendall test
      The significance partner to Theil-Sen. Yields a p-value without
      assuming normality, which response-time data never has.

Latencies are logged before any slope is taken: reaction-time distributions
are right-skewed and roughly log-normal, and a slope in log space is a
*percentage* change per bucket - both the honest summary and the one a
student can actually read.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from statistics import median
from typing import Literal

Granularity = Literal["day", "week"]

# ---------------------------------------------------------------------------
# Tuning constants. Every one of them is a display choice, not a claim about
# the world - they set where a score saturates, not whether a trend exists.
# ---------------------------------------------------------------------------

#: Buckets needed before a trend is reported at all. Two points always make a
#: perfect line; three is the smallest number that can disagree with itself.
MIN_BUCKETS = 3

#: Decisions needed before the power-law exponent is fitted.
MIN_FOR_POWER_LAW = 5

#: Slope (in ln-units per bucket) at which a score reaches about 12 or 88.
#: 0.35 ln-units is roughly a 42% change per bucket, which is already dramatic.
SATURATION = 0.35

#: Pseudo-count for shrinking a score toward 50. A trend read off four buckets
#: is half-believed; one read off twenty is believed almost entirely.
SHRINK_PRIOR = 4.0

#: Scores inside 50 +/- this are called "steady" rather than given a direction.
DEADBAND = 4.0

#: A decision left open longer than this was abandoned, not deliberated.
MAX_PLAUSIBLE_SECONDS = 60 * 45

#: Below this the user almost certainly re-submitted an existing decision.
MIN_PLAUSIBLE_SECONDS = 1.5


@dataclass(frozen=True)
class DecisionEvent:
    """One timed decision, reduced to the three things the maths needs."""

    at: datetime
    seconds: float
    options: int


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


def hick_normalise(seconds: float, options: int) -> float:
    """Seconds per unit of choice difficulty (Hick-Hyman).

    Dividing by log2(n + 1) puts a two-option decision and an eight-option one
    on the same axis, so a rising trend means the student really is slowing
    down rather than simply comparing more things.
    """
    return seconds / math.log2(max(options, 2) + 1)


def theil_sen(xs: list[float], ys: list[float]) -> float:
    """Median of every pairwise slope. Returns 0.0 when nothing is comparable."""
    slopes = [
        (ys[j] - ys[i]) / (xs[j] - xs[i])
        for i in range(len(xs))
        for j in range(i + 1, len(xs))
        if xs[j] != xs[i]
    ]
    return median(slopes) if slopes else 0.0


def _normal_sf(z: float) -> float:
    """Upper tail of the standard normal, via the complementary error function."""
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def _sign(value: float) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def mann_kendall(ys: list[float]) -> dict[str, float]:
    """Non-parametric monotonic-trend test with the standard tie correction.

    Returns S (the concordance count), the normal statistic z, and a two-sided
    p-value. With fewer than MIN_BUCKETS points the normal approximation is
    meaningless, so the p-value comes back as 1.0 - no evidence, rather than a
    number that would look like evidence.
    """
    n = len(ys)
    if n < MIN_BUCKETS:
        return {"s": 0.0, "z": 0.0, "p": 1.0}

    s = sum(
        _sign(ys[j] - ys[i])
        for i in range(n)
        for j in range(i + 1, n)
    )

    # Var(S) = [n(n-1)(2n+5) - sum over tied groups of t(t-1)(2t+5)] / 18
    counts: dict[float, int] = {}
    for y in ys:
        key = round(y, 9)
        counts[key] = counts.get(key, 0) + 1
    ties = sum(t * (t - 1) * (2 * t + 5) for t in counts.values() if t > 1)
    variance = (n * (n - 1) * (2 * n + 5) - ties) / 18.0
    if variance <= 0:
        return {"s": float(s), "z": 0.0, "p": 1.0}

    # Continuity correction: pull S one unit toward zero before standardising.
    if s > 0:
        z = (s - 1) / math.sqrt(variance)
    elif s < 0:
        z = (s + 1) / math.sqrt(variance)
    else:
        z = 0.0

    return {"s": float(s), "z": z, "p": min(1.0, 2.0 * _normal_sf(abs(z)))}


def power_law_fit(latencies: list[float]) -> dict[str, float] | None:
    """Fit T = a * N^(-b) by least squares on ln T against ln N.

    `latencies` must be in the order the decisions were made, so N is simply
    1, 2, 3, ... - the practice count the law is defined over. The returned
    `exponent` is b with the sign already flipped, so a positive number means
    the student is speeding up with practice, matching how the literature
    quotes it.
    """
    if len(latencies) < MIN_FOR_POWER_LAW:
        return None

    xs = [math.log(i) for i in range(1, len(latencies) + 1)]
    ys = [math.log(max(t, 1e-6)) for t in latencies]
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    if sxx <= 0:
        return None
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    slope = sxy / sxx
    intercept = mean_y - slope * mean_x

    syy = sum((y - mean_y) ** 2 for y in ys)
    residual = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys))
    r2 = 1.0 - residual / syy if syy > 0 else 0.0

    return {
        # b in T = a * N^(-b). Positive b = faster with practice.
        "exponent": round(-slope, 4),
        "scale": round(math.exp(intercept), 2),
        "r2": round(max(r2, 0.0), 3),
        "n": n,
    }


def _score(slope: float, buckets: int, higher_is_better: bool) -> dict[str, float]:
    """Map a slope to a bounded 0-100 score, then shrink it toward 50.

    tanh is used rather than a clamp so the score stays smooth and strictly
    monotonic in the slope: two students with wildly different slopes never
    land on the same 100. The 0-100 range follows the convention of the
    Decision Self-Efficacy Scale, where the middle is the honest "no signal".
    """
    direction = 1.0 if higher_is_better else -1.0
    raw = 50.0 + 50.0 * math.tanh(slope / SATURATION) * direction
    # Evidence weight: a trend from few buckets is mostly prior, not data.
    weight = buckets / (buckets + SHRINK_PRIOR)
    return {
        "raw": round(raw, 1),
        "score": round(50.0 + (raw - 50.0) * weight, 1),
        "reliability": round(weight, 3),
    }


def _verdict(score: float, improving: str, worsening: str) -> str:
    if score > 50 + DEADBAND:
        return improving
    if score < 50 - DEADBAND:
        return worsening
    return "steady"


def _evidence(p: float) -> str:
    """Plain-language strength, stated once so the UI never has to guess."""
    if p < 0.05:
        return "significant"
    if p < 0.10:
        return "suggestive"
    return "inconclusive"


# ---------------------------------------------------------------------------
# Bucketing
# ---------------------------------------------------------------------------


def _bucket_index(moment: datetime, granularity: Granularity) -> int:
    """A gap-aware integer index: consecutive buckets differ by exactly 1.

    Using the calendar ordinal rather than "the 4th bucket that had data"
    matters - a week with no decisions in it is a real gap, and Theil-Sen
    should see it as one.
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    day = moment.astimezone(timezone.utc).date()
    if granularity == "week":
        monday = day.toordinal() - day.weekday()
        return monday // 7
    return day.toordinal()


def _bucket_label(index: int, granularity: Granularity) -> str:
    if granularity == "week":
        # Invert the floor division back to the Monday that started the week.
        # Ordinal 1 is itself a Monday, so every Monday is 1 (mod 7).
        monday = date.fromordinal(index * 7 + 1)
        return f"w/c {monday.isoformat()}"
    return date.fromordinal(index).isoformat()


def choose_granularity(events: list[DecisionEvent]) -> Granularity:
    """Days while the history is short, weeks once it would look too sparse."""
    if len(events) < 2:
        return "day"
    span = (events[-1].at - events[0].at).days
    return "week" if span > 21 else "day"


def clean(events: Iterable[DecisionEvent]) -> list[DecisionEvent]:
    """Drop rows that cannot be a real deliberation, and sort by time.

    An implausibly long gap almost always means the modal was left open on a
    forgotten tab; an implausibly short one means the form was re-submitted
    without being re-thought. Neither is deliberation, and leaving them in
    would let a single abandoned tab dominate the median.
    """
    kept = [
        event
        for event in events
        if event.seconds is not None
        and MIN_PLAUSIBLE_SECONDS <= event.seconds <= MAX_PLAUSIBLE_SECONDS
    ]
    return sorted(kept, key=lambda e: e.at)


# ---------------------------------------------------------------------------
# The public entry point
# ---------------------------------------------------------------------------


def compute_tempo(
    events: Iterable[DecisionEvent],
    granularity: Granularity | None = None,
) -> dict:
    """Build the full tempo report: two series, two scores, one verdict each.

    Always returns the same shape. When there is not enough history the scores
    are absent and `status` says how much more is needed, so the UI renders a
    progress message instead of a fabricated trend.
    """
    timed = clean(events)
    grain: Granularity = granularity or choose_granularity(timed)

    # --- fold the events into buckets -------------------------------------
    grouped: dict[int, list[DecisionEvent]] = {}
    for event in timed:
        grouped.setdefault(_bucket_index(event.at, grain), []).append(event)

    series = []
    for index in sorted(grouped):
        bucket = grouped[index]
        adjusted = [hick_normalise(e.seconds, e.options) for e in bucket]
        series.append(
            {
                "index": index,
                "label": _bucket_label(index, grain),
                # Median, not mean: one abandoned decision should not move it.
                "median_seconds": round(median([e.seconds for e in bucket]), 1),
                "adjusted_seconds": round(median(adjusted), 2),
                "decisions": len(bucket),
            }
        )

    base = {
        "granularity": grain,
        "series": series,
        "totals": {
            "decisions": len(timed),
            "buckets": len(series),
            "needed": MIN_BUCKETS,
        },
        "power_law": power_law_fit(
            [hick_normalise(e.seconds, e.options) for e in timed]
        ),
    }

    if len(series) < MIN_BUCKETS:
        short = MIN_BUCKETS - len(series)
        unit = "day" if grain == "day" else "week"
        return {
            **base,
            "status": "collecting",
            "speed": None,
            "confidence": None,
            "overall": None,
            "message": (
                f"Time {short} more {unit}{'' if short == 1 else 's'} of "
                "decisions and the trend lines appear here."
            ),
        }

    # x is the calendar index, rebased so the first bucket is 0. Rebasing does
    # not change any slope; it just keeps the numbers small and readable.
    origin = series[0]["index"]
    xs = [float(row["index"] - origin) for row in series]

    # --- speed: slope of log(time per decision) ---------------------------
    log_latency = [math.log(max(row["adjusted_seconds"], 1e-6)) for row in series]
    speed_slope = theil_sen(xs, log_latency)
    speed_test = mann_kendall(log_latency)
    # A falling latency is the good direction, hence higher_is_better=False.
    speed = _score(speed_slope, len(series), higher_is_better=False)
    speed.update(
        {
            "slope_log": round(speed_slope, 4),
            # exp(slope) - 1 turns a log slope back into "x% per day/week".
            "pct_change_per_bucket": round((math.exp(speed_slope) - 1) * 100, 1),
            "p_value": round(speed_test["p"], 4),
            "z": round(speed_test["z"], 3),
            "evidence": _evidence(speed_test["p"]),
            "verdict": _verdict(speed["score"], "faster", "slower"),
            "first_seconds": series[0]["median_seconds"],
            "last_seconds": series[-1]["median_seconds"],
        }
    )

    # --- confidence: slope of decisions per bucket ------------------------
    volume = [float(row["decisions"]) for row in series]
    volume_slope = theil_sen(xs, volume)
    volume_test = mann_kendall(volume)
    # Normalised by the typical bucket, so "+1 per week" means much more to
    # someone who usually makes two decisions than to someone who makes ten.
    typical = max(median(volume), 1.0)
    confidence = _score(volume_slope / typical, len(series), higher_is_better=True)
    confidence.update(
        {
            "slope": round(volume_slope, 3),
            "slope_relative": round(volume_slope / typical, 4),
            "p_value": round(volume_test["p"], 4),
            "z": round(volume_test["z"], 3),
            "evidence": _evidence(volume_test["p"]),
            "verdict": _verdict(confidence["score"], "rising", "falling"),
            "typical_per_bucket": round(typical, 1),
            "first_count": series[0]["decisions"],
            "last_count": series[-1]["decisions"],
        }
    )

    return {
        **base,
        "status": "ready",
        "speed": speed,
        "confidence": confidence,
        "overall": round((speed["score"] + confidence["score"]) / 2, 1),
        "message": _narrative(speed, confidence, grain),
    }


def _narrative(speed: dict, confidence: dict, grain: Granularity) -> str:
    """One sentence a student can act on, built from the two verdicts.

    Deliberately says what the numbers say and nothing more: where the test
    came back inconclusive the wording stays hedged rather than borrowing
    confidence the p-value does not support.
    """
    unit = "day" if grain == "day" else "week"
    pace = abs(speed["pct_change_per_bucket"])

    if speed["verdict"] == "faster":
        first = f"You are settling decisions about {pace:.0f}% quicker each {unit}"
    elif speed["verdict"] == "slower":
        first = f"Decisions are taking about {pace:.0f}% longer each {unit}"
    else:
        first = f"Your time per decision is holding steady {unit} to {unit}"

    if confidence["verdict"] == "rising":
        second = "and you are making more of them - that reads as growing confidence."
    elif confidence["verdict"] == "falling":
        second = "and you are making fewer of them, which usually means avoidance."
    else:
        second = "and your decision volume is level."

    hedge = ""
    if speed["evidence"] == "inconclusive" and confidence["evidence"] == "inconclusive":
        hedge = (
            " Both trends are still within what chance could produce - "
            "treat them as early signals."
        )
    return f"{first} {second}{hedge}"
