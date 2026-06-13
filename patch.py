import sys, re
content = open('api/main.py', encoding='utf-8').read()

# Fix the dictionary keys inside the append block
content = content.replace('"base_projected_points": pts,', '"projected_points": pts,')
content = content.replace('"p10_points": p10_pts,', '"projected_points_p10": p10_pts,')
content = content.replace('"p90_points": p90_pts,', '"projected_points_p90": p90_pts,')
content = content.replace('"p10_rank": p10_rank,', '"projected_rank_p10": p10_rank,')
content = content.replace('"p90_rank": p90_rank,', '"projected_rank_p90": p90_rank,')
content = content.replace('"top6_chance": top6_chance,', '"top6_chance_pct": top6_chance,')
content = content.replace('"playout_risk": playout_risk,', '"playout_risk_pct": playout_risk,')

# Add missing projected_points_p50 (since it was pts)
content = content.replace('"projected_points": pts,', '"projected_points": pts,\n                    "projected_points_p50": pts,')
# Add missing projected_rank_p50
content = content.replace('"projected_rank": i,', '"projected_rank": i,\n                    "projected_rank_p50": i,')

# Fix bjk_summary keys
old_bjk = 'bjk_row["base_projected_points"] if bjk_row else None'
new_bjk = 'bjk_row["projected_points"] if bjk_row else None'
content = content.replace(old_bjk, new_bjk)

content = content.replace('bjk_row["top6_chance"] if bjk_row else None', 'bjk_row["top6_chance_pct"] if bjk_row else None')
content = content.replace('bjk_row["playout_risk"] if bjk_row else None', 'bjk_row["playout_risk_pct"] if bjk_row else None')
content = content.replace('bjk_row["p10_points"] if bjk_row else None', 'bjk_row["projected_points_p10"] if bjk_row else None')
content = content.replace('bjk_row["p90_points"] if bjk_row else None', 'bjk_row["projected_points_p90"] if bjk_row else None')
content = content.replace('bjk_row["p10_rank"] if bjk_row else None', 'bjk_row["projected_rank_p10"] if bjk_row else None')
content = content.replace('bjk_row["p90_rank"] if bjk_row else None', 'bjk_row["projected_rank_p90"] if bjk_row else None')

# Fix encoding issues that might have sneaked in
content = content.replace('bjǟrklǟven', 'björklöven')
content = content.replace('bjrklven', 'björklöven')
content = content.replace('IF Bjrklven', 'IF Björklöven')
content = content.replace('bjǟrklǟven', 'björklöven')
content = content.replace('Bjrklven', 'Björklöven')
content = content.replace('bjrklven', 'björklöven')

open('api/main.py', 'w', encoding='utf-8').write(content)
print('Patched successfully')
