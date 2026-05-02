#!/usr/bin/env python3
"""Direct API probe — bypass cache, print URL + the raw 5 most recent matches
returned by Matchstat for Marta Kostyuk (mid 47742) and Anastasia Potapova
(mid 40156). Cross-checks whether either player references a recent match
against the other that's missing from the other player's history.
"""
import sys
import urllib.parse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from api_client import MatchstatClient

PLAYERS = [
    ('Marta Kostyuk',     47742),
    ('Anastasia Potapova', 40156),
    ('Mirra Andreeva',     77934),  # bonus — also in the mix
]

client = MatchstatClient(verbose=False)

def show_query_url(mid: int):
    params = {'include': 'stat,round,tournament', 'pageSize': 50, 'pageNo': 1, 'filter': 'GameYear:2026'}
    url = f'https://{client.host}{client.BASE_PATH}/wta/player/past-matches/{mid}?{urllib.parse.urlencode(params)}'
    return url

results = {}
for name, mid in PLAYERS:
    print('=' * 80)
    print(f'PLAYER: {name}  (mid={mid})')
    print(f'URL:    {show_query_url(mid)}')
    print('-' * 80)
    resp = client.get(
        'wta',
        f'player/past-matches/{mid}',
        {'include': 'stat,round,tournament', 'pageSize': 50, 'pageNo': 1, 'filter': 'GameYear:2026'},
        force_refresh=True,
    )
    rows = (resp or {}).get('data') or resp or []
    rows_sorted = sorted(rows, key=lambda m: (m.get('date') or '')[:10], reverse=True)
    print(f'API returned {len(rows)} total 2026 matches; showing 5 most recent:\n')
    print(f'  {"DATE":<12} {"ROUND":<10} {"TOURNAMENT":<35} {"OPPONENT":<28} {"SCORE":<25} WON')
    latest_5 = rows_sorted[:5]
    for m in latest_5:
        p1id = m.get('player1Id') or m.get('p1Id')
        p2id = m.get('player2Id') or m.get('p2Id')
        we_p1 = (p1id == mid)
        opp_obj = m.get('player2') if we_p1 else m.get('player1')
        opp_name = (opp_obj or {}).get('name') or '?'
        rd = m.get('round')
        rd_str = (rd.get('shortName') or rd.get('name')) if isinstance(rd, dict) else (rd or '')
        tn = (m.get('tournament') or {}).get('name', '?')
        date = (m.get('date') or '')[:10]
        score = m.get('result') or m.get('score') or '?'
        winner_id = m.get('match_winner') or m.get('winnerId')
        won = (winner_id == mid) if winner_id else None
        print(f'  {date:<12} {rd_str:<10} {tn[:34]:<35} {opp_name[:27]:<28} {score[:24]:<25} {won}')
    results[mid] = (name, latest_5)
    print()

# Cross-check: does Kostyuk's history reference Potapova as opponent? Or vice-versa?
print('=' * 80)
print('CROSS-CHECK')
print('=' * 80)
kos_name, kos_recent = results[47742]
pot_name, pot_recent = results[40156]
def find_opp(matches, target_mid, target_name):
    for m in matches:
        p1id = m.get('player1Id') or m.get('p1Id')
        p2id = m.get('player2Id') or m.get('p2Id')
        if target_mid in (p1id, p2id):
            return m
        opp = (m.get('player1') if p2id == m.get('player1Id') else m.get('player2')) or {}
        if target_name.split()[-1].lower() in (opp.get('name','')).lower():
            return m
    return None

k_vs_p = find_opp(kos_recent, 40156, 'Potapova')
p_vs_k = find_opp(pot_recent, 47742, 'Kostyuk')
print(f'Kostyuk recent  vs Potapova match: {"YES — " + (k_vs_p.get("date","")[:10]) if k_vs_p else "NOT in latest 5"}')
print(f'Potapova recent vs Kostyuk match:  {"YES — " + (p_vs_k.get("date","")[:10]) if p_vs_k else "NOT in latest 5"}')
