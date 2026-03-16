"""consensus.py — Konsensüs algoritması yardımcıları"""

def weighted_average(votes: list, weights: dict) -> float:
    total_w, total_s = 0.0, 0.0
    for v in votes:
        w = weights.get(v.agent_name, 0.15) * v.confidence
        total_s += v.signal * w
        total_w += w
    return total_s / total_w if total_w > 0 else 0.0

def agreement_ratio(votes: list) -> float:
    """Kaçı aynı yönde oy verdi?"""
    positives = sum(1 for v in votes if v.signal > 0)
    return positives / len(votes) if votes else 0.5
