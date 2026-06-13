import sys
with open('api/main.py', 'r', encoding='utf-8') as f:
    content = f.read()

old_shl_goalies = """            shl_goalies.append({
                "name": display_name,
                "sv_pct": sv_pct,
                "proj_sv_pct": proj_sv_pct,
                "gaa": g.get("gaa", 0),
                "proj_gaa": proj_gaa
            })"""

new_shl_goalies = """            shl_goalies.append({
                "name": display_name,
                "sv_pct": sv_pct,
                "proj_sv_pct": proj_sv_pct,
                "gaa": g.get("gaa", 0),
                "proj_gaa": proj_gaa
            })

        # Add confirmed signings that are missing from player_impact / goalie_radar
        for s in SILLY_SEASON_BASELINE.get("confirmed_signings", []):
            name = s.get("name")
            pos = s.get("pos", "F")
            
            # Check if already added
            if any(name_match_strict(name, p["name"].replace(" 🆕", "").replace(" (Utland)", "").replace(" (SHL/Exempt)", "")) for p in shl_skaters):
                continue
            if any(name_match_strict(name, g["name"].replace(" 🆕", "").replace(" (Utland)", "").replace(" (SHL/Exempt)", "")) for g in shl_goalies):
                continue
                
            is_goalie = pos == "GK"
            override_data = signings_overrides.get(name)
            
            if is_goalie:
                if override_data:
                    proj_sv = override_data.get("proj_sv_pct", 90.0)
                    ha_sv = override_data.get("ha_sv_pct", 90.0)
                    proj_gaa = override_data.get("proj_gaa", 2.50)
                else:
                    proj_sv = 90.0
                    ha_sv = 91.0
                    proj_gaa = 2.50
                    
                shl_goalies.append({
                    "name": f"{name} (Utland)",
                    "sv_pct": ha_sv,
                    "proj_sv_pct": proj_sv,
                    "gaa": 2.50,
                    "proj_gaa": proj_gaa
                })
            else:
                if override_data:
                    proj_ppg = override_data.get("proj_ppg", 0.5)
                    ha_ppg = override_data.get("ha_ppg", 0.5)
                else:
                    proj_ppg = 0.50 if "D" not in pos else 0.30
                    ha_ppg = proj_ppg / 0.60
                    
                readiness = skater_readiness_by_position(pos, proj_ppg)
                shl_skaters.append({
                    "name": f"{name} (Utland)",
                    "position": pos,
                    "ha_ppg": round(ha_ppg, 2),
                    "proj_ppg": round(proj_ppg, 2),
                    "readiness": readiness
                })"""

content = content.replace(old_shl_goalies, new_shl_goalies)

with open('api/main.py', 'w', encoding='utf-8') as f:
    f.write(content)
print("Patched confirmed signings")
