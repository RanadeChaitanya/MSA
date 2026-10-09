"""Retry ladder (page level). Each rung re-canonicalises ONE failing page with an alternative
profile (see canonicalize.PROFILES), re-parses and re-drafts it, and keeps whichever attempt has the
fewest ERROR flags. Rungs after the first success are skipped. Pages still failing go to review."""
from __future__ import annotations

import logging
from typing import Callable

log = logging.getLogger("pipeline.ladder")


class SkipRung(Exception):
    """Raised by attempt_fn when a rung cannot change anything (identical image / not applicable)."""


def run_ladder(failing: list[int], profiles: list[str], attempt_fn: Callable[[int, str], tuple],
               score_fn: Callable[[list, list], int], current: dict[int, tuple]) -> dict[int, tuple]:
    """current[p] = (drafts, issues, record). attempt_fn(page, profile) -> (drafts, issues, record)."""
    for p in failing:
        best = current[p]
        best_score = score_fn(best[0], best[1])
        for prof in profiles:
            if best_score == 0:
                break
            try:
                cand = attempt_fn(p, prof)
            except SkipRung as e:
                log.info("page %d profile %-13s skipped: %s", p, prof, e)
                continue
            except Exception as e:                      # a rung failing must never kill the run
                log.warning("page %d profile %s failed: %s", p, prof, e)
                continue
            s = score_fn(cand[0], cand[1])
            log.info("page %d profile %-13s errors %d -> %d", p, prof, best_score, s)
            if s < best_score or (s == best_score and len(cand[0]) > len(best[0])):
                best, best_score = cand, s
        current[p] = best
    return current
