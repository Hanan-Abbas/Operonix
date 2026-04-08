import re
from difflib import SequenceMatcher
from typing import Iterable, Optional, Tuple


def match_intent_local(
    candidate_text: str,
    allowed_intents: Iterable[str],
    threshold: float = 0.30,
) -> Tuple[Optional[str], float]:
    """
    Dependency-free local intent matcher.
    Returns (best_intent, score). Score is 0..1.
    """
    if not candidate_text:
        return None, 0.0

    allowed = [x for x in (allowed_intents or []) if isinstance(x, str) and x.strip()]
    if not allowed:
        return None, 0.0

    normalized_text = str(candidate_text).lower().replace("_", " ").strip()
    text_tokens = set(re.findall(r"[a-z0-9]+", normalized_text))

    best_intent = None
    best_score = 0.0
    for intent in allowed:
        intent_text = intent.lower().replace("_", " ")
        intent_tokens = set(re.findall(r"[a-z0-9]+", intent_text))
        overlap = 0.0
        if text_tokens and intent_tokens:
            overlap = len(text_tokens & intent_tokens) / float(len(intent_tokens))

        ratio = SequenceMatcher(None, normalized_text, intent_text).ratio()
        score = (0.7 * overlap) + (0.3 * ratio)
        if score > best_score:
            best_score = score
            best_intent = intent

    if best_intent and best_score >= float(threshold):
        return best_intent, float(best_score)
    return None, float(best_score)

