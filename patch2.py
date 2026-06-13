import sys
content = open('api/main.py', encoding='utf-8').read()

old_return = """                "game_state": game_state,
                "age_curve": age_curve,
                "silly_season": {
                    "baseline": SILLY_SEASON_BASELINE,
                    "shl_readiness": {
                        "skaters": shl_skaters,
                        "goalies": shl_goalies,
                        "benchmarks": shl_benchmarks
                    },
                    "shl_projected_table": shl_projected_table
                },
            },
        }
    except Exception as e:"""

new_return = """                "game_state": game_state,
                "shl_transition": {
                    "skaters": shl_skaters,
                    "goalies": shl_goalies,
                    "benchmarks": shl_benchmarks
                },
                "age_curve": age_curve,
                "shl_projected_table": shl_projected_table,
                "silly_season": {
                    "baseline": SILLY_SEASON_BASELINE,
                    "shl_readiness": {
                        "skaters": shl_skaters,
                        "goalies": shl_goalies,
                        "benchmarks": shl_benchmarks
                    },
                    "shl_projected_table": shl_projected_table
                },
            },
        }
    except Exception as e:"""

if old_return in content:
    content = content.replace(old_return, new_return)
    open('api/main.py', 'w', encoding='utf-8').write(content)
    print("Patched return successfully")
else:
    print("Could not find old return block")
