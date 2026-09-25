"""Independent directional selection; candidates are not orders."""
import math


def select_sides(rows, minimum=6.5):
    pools = {side: sorted(
        [row for row in rows if row.get('direction') == side and row.get('eligible')
         and isinstance(row.get('score'), (float, int))
         and math.isfinite(row['score']) and row['score'] >= minimum],
        key=lambda row: (-row['score'], row['symbol']),
    ) for side in ('LONG', 'SHORT')}
    # Reject an ambiguous same-symbol tie rather than manufacture a hedge.
    while pools['LONG'] and pools['SHORT'] and pools['LONG'][0]['symbol'] == pools['SHORT'][0]['symbol']:
        long_score, short_score = pools['LONG'][0]['score'], pools['SHORT'][0]['score']
        if long_score <= short_score:
            pools['LONG'].pop(0)
        if short_score <= long_score:
            pools['SHORT'].pop(0)
    return {side: candidates[0] if candidates else None for side, candidates in pools.items()}
