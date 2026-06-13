import requests
API = 'https://loven-stats-api-324947473206.europe-west1.run.app'
data = requests.get(f'{API}/api/v1/analytics?season=ha_2526').json()

skaters = data['modules']['shl_transition']['skaters']
goalies = data['modules']['shl_transition']['goalies']

def get_age_multiplier(age):
    if age <= 21: return 0.15
    elif age <= 23: return 0.08
    elif age <= 28: return 0.00
    elif age <= 33: return -0.08
    else: return -0.22

adj_sk_ppg = []
for s in skaters:
    # use default age 26 for simplicity
    adj = round(s['proj_ppg'] * (1 + get_age_multiplier(26)), 2)
    adj_sk_ppg.append(adj)

avg_sk = sum(adj_sk_ppg) / len(adj_sk_ppg) if adj_sk_ppg else 0.35
avg_g = sum(g['proj_sv_pct'] for g in goalies) / len(goalies) if goalies else 89.5

print(f"avg_sk: {avg_sk}, avg_g: {avg_g}")

# model
bjk_points_model = 58.0
bjk_points_model += (avg_sk - 0.38) * 80.0
bjk_points_model += (avg_g - 89.5) * 2.4
bjk_points_model += (0.0 - 95.0) * 0.35 # special teams is 0.0 for BJK because 18266 is broken!
bjk_points_model += 6 * 1.8
bjk_points_model -= 9 * 0.5
bjk_points_model -= 2 * 0.9

print("bjk_points_model before bound:", bjk_points_model)
print("bounded:", max(46.0, min(96.0, bjk_points_model)))
