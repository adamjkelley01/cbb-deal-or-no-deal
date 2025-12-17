import random
from typing import Dict, Optional, List, Set

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from cachetools import TTLCache
from nba_api.stats.static import players as nba_players
from nba_api.stats.static import teams as nba_teams
from nba_api.stats.endpoints import commonplayerinfo, commonteamroster, leaguedashplayerstats

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


SEASON_2024_25 = "2024-25"

# -----------------------
# Helpers
# -----------------------
def height_to_inches(height_text: str) -> int:
    """
    '6-10' -> 82
    '6-9' -> 81
    """
    try:
        s = (height_text or "").strip()
        if "-" not in s:
            return 0
        ft, inch = s.split("-", 1)
        return int(ft) * 12 + int(inch)
    except Exception:
        return 0

def parse_height_inches(height_text: str) -> int:
    """
    Converts NBA height strings like '6-8' into total inches.
    Returns 0 if missing/invalid.
    """
    if not height_text:
        return 0
    s = str(height_text).strip()
    if "-" not in s:
        return 0
    try:
        feet_str, inch_str = s.split("-", 1)
        return int(feet_str) * 12 + int(inch_str)
    except Exception:
        return 0


def three_pt_pct(row) -> float:
    """
    Returns 3PT% as a percent number (0-100), not 0-1.
    LeagueDashPlayerStats FG3_PCT is usually 0-1.
    """
    val = row.get("FG3_PCT", 0)
    try:
        v = float(val)
    except Exception:
        return 0.0

    # If it looks like 0.35, convert to 35.0
    return v * 100.0 if v <= 1.0 else v


def normalize_position(raw_pos: str, height_text: str, fg3_pct_percent: float) -> str:
    """
    Derive PG / SG / SF / PF / C using roster position + height rules.
    Adds extra SF rule for F:
      - if shorter than 6'9 AND 3PT% >= 32% => SF
      - else PF
    """
    p = (raw_pos or "").upper().strip()
    h = parse_height_inches(height_text)

    # F-C split
    if p == "F-C":
        return "C" if h >= 82 else "PF"  # 6'10" = 82 inches

    # F split (UPDATED)
    if p == "F":
        if h >= 81:  # 6'9" = 81 inches
            return "PF"
        # shorter than 6'9"
        return "SF" if fg3_pct_percent >= 32.0 else "PF"

    # G-F split
    if p == "G-F":
        return "SF" if h >= 78 else "SG"  # 6'6" = 78 inches

    # G split
    if p == "G":
        return "SG" if h >= 77 else "PG"  # 6'5" = 77 inches

    # C stays C
    if p == "C":
        return "C"

    # fallback
    return "SF"



def production_score_pergame(row) -> float:
    pts = float(row.get("PTS", 0))
    reb = float(row.get("REB", 0))
    ast = float(row.get("AST", 0))
    stl = float(row.get("STL", 0))
    blk = float(row.get("BLK", 0))
    tov = float(row.get("TOV", 0))
    return pts + 1.2 * reb + 1.5 * ast + 3.0 * stl + 3.0 * blk - 2.0 * tov


def clamp_tier(t: int) -> int:
    return max(1, min(16, t))


def split_into_16_tiers(players_sorted_desc: List[dict]) -> List[List[dict]]:
    """
    players_sorted_desc must already be sorted by score desc.
    Returns tiers[0..15] each list of players in that tier.
    """
    n = len(players_sorted_desc)
    tiers: List[List[dict]] = [[] for _ in range(16)]

    gamma = 2  # tune: 1.2 mild, 1.6 good start, 2.0 strong

    for i, p in enumerate(players_sorted_desc):
        rank = 1.0 - (i / (n - 1) if n > 1 else 0.0)  # best=1, worst=0
        tier_idx = 15 - int((rank ** gamma) * 16)     # best->0, worst->15
        tier_idx = min(15, max(0, tier_idx))
        tiers[tier_idx].append(p)

    return tiers




# -----------------------
# Basic health
# -----------------------
@app.get("/health")
def health():
    return {"status": "ok", "season": SEASON_2024_25}


# -----------------------
# Player search (static list + team lookup)
# -----------------------
TEAM_CACHE: Dict[int, str] = {}

def get_team_name(player_id: int) -> str:
    if player_id in TEAM_CACHE:
        return TEAM_CACHE[player_id]

    try:
        info = commonplayerinfo.CommonPlayerInfo(player_id=player_id)
        df = info.get_data_frames()[0]
        team = df.loc[0, "TEAM_NAME"]
        team_name = str(team) if team else "Unknown"
    except Exception:
        team_name = "Unknown"

    TEAM_CACHE[player_id] = team_name
    return team_name


@app.get("/players/search")
def search_players(q: str = Query(..., min_length=1), limit: int = 10):
    query = q.lower().strip()
    all_players = nba_players.get_players()

    matches = [p for p in all_players if query in p["full_name"].lower()]
    matches.sort(key=lambda p: (not p.get("is_active", False), p["full_name"]))
    matches = matches[: min(max(limit, 1), 25)]

    results = []
    for p in matches:
        pid = int(p["id"])
        results.append({"id": pid, "name": p["full_name"], "team": get_team_name(pid)})

    return {"query": q, "count": len(results), "players": results}


# -----------------------
# Rosters and positions (eligibility)
# -----------------------
# ROSTER_META: player_id -> {"name": ..., "team": ..., "raw_pos": ..., "height": ...}
ROSTER_META: Dict[int, Dict[str, str]] = {}

def build_roster_maps(season: str) -> Dict[str, int]:
    global ROSTER_META
    ROSTER_META = {}

    teams = nba_teams.get_teams()
    for t in teams:
        team_id = t["id"]
        team_name = t.get("full_name", t.get("abbreviation", "Unknown"))

        try:
            roster = commonteamroster.CommonTeamRoster(team_id=team_id, season=season)
            df = roster.get_data_frames()[0]
        except Exception:
            continue

        for _, row in df.iterrows():
            try:
                pid = int(row.get("PLAYER_ID"))
                name = str(row.get("PLAYER", "")).strip()
                raw_pos = str(row.get("POSITION", "")).strip()
                height = str(row.get("HEIGHT", "")).strip()
            except Exception:
                continue

            if not pid or not name:
                continue

            ROSTER_META[pid] = {
                "name": name,
                "team": str(team_name),
                "raw_pos": raw_pos,
                "height": height,
            }

    return {"players": len(ROSTER_META), "teams": len(teams)}


# -----------------------
# Season stats (tiering, excludes no-stats)
# -----------------------
STATS_CACHE = TTLCache(maxsize=2, ttl=60 * 60)  # 1 hour cache

def get_season_stats_df():
    if SEASON_2024_25 in STATS_CACHE:
        return STATS_CACHE[SEASON_2024_25]

    dash = leaguedashplayerstats.LeagueDashPlayerStats(
        season=SEASON_2024_25,
        season_type_all_star="Regular Season",
        per_mode_detailed="PerGame",
    )
    df = dash.get_data_frames()[0]
    STATS_CACHE[SEASON_2024_25] = df
    return df


def build_candidates(slot: Optional[str] = None) -> List[dict]:
    """
    Candidate pool rules:
      1) Must have season stats row (2024-25)
      2) Must be on a 2024-25 roster
      3) If slot provided, must match normalized roster position (height rules)
      4) Exclude players with no stats: GP > 0 and MIN > 0
    """
    df = get_season_stats_df()

    slot_norm: Optional[str] = None
    if slot is not None:
        s = slot.upper().strip()
        if s not in {"PG", "SG", "SF", "PF", "C"}:
            raise HTTPException(status_code=400, detail="slot must be one of PG, SG, SF, PF, C")
        slot_norm = s

    if len(ROSTER_META) == 0:
        raise HTTPException(status_code=503, detail="Roster map empty. Restart backend or call POST /admin/refresh_rosters.")

    candidates: List[dict] = []

    for _, row in df.iterrows():
        try:
            pid = int(row.get("PLAYER_ID"))
        except Exception:
            continue

        meta = ROSTER_META.get(pid)
        if not meta:
            continue

        gp = float(row.get("GP", 0))
        min_pg = float(row.get("MIN", 0))
        if gp <= 0 or min_pg <= 0:
            continue

        if slot_norm is not None:
            raw_pos = meta.get("raw_pos", "")
            height = meta.get("height", "")
            fg3pct = three_pt_pct(row)
            player_slot = normalize_position(raw_pos, height, fg3pct)
            if player_slot != slot_norm:
                continue

        score = production_score_pergame(row)

        candidates.append(
            {
                "id": pid,
                "name": meta["name"],
                "team": meta["team"],
                "score": score,
            }
        )

    return candidates


def build_case_pool_from_candidates(candidates: List[dict], seed: int) -> List[dict]:
    candidates.sort(key=lambda x: x["score"], reverse=True)
    if len(candidates) < 16:
        raise HTTPException(status_code=400, detail=f"Not enough candidates. Found {len(candidates)}.")

    tiers = split_into_16_tiers(candidates)

    rng = random.Random(seed)
    chosen = []
    for tier_index, tier_players in enumerate(tiers, start=1):
        if not tier_players:
            raise HTTPException(status_code=500, detail=f"Tier {tier_index} ended up empty.")
        pick = rng.choice(tier_players)
        chosen.append({"tier": tier_index, "player": pick})

    rng.shuffle(chosen)

    cases = []
    for case_number, item in enumerate(chosen, start=1):
        p = item["player"]
        cases.append(
            {
                "case": case_number,
                "tier": item["tier"],
                "player": {"id": p["id"], "name": p["name"], "team": p["team"]},
                "score": round(p["score"], 2),
            }
        )
    return cases


# -----------------------
# Admin
# -----------------------
@app.post("/admin/refresh_rosters")
def refresh_rosters():
    info = build_roster_maps(SEASON_2024_25)
    return {"season": SEASON_2024_25, **info}

@app.post("/admin/refresh_stats")
def refresh_stats():
    df = get_season_stats_df()
    return {"season": SEASON_2024_25, "rows": int(df.shape[0])}

@app.get("/admin/roster_count")
def roster_count():
    return {"season": SEASON_2024_25, "players": len(ROSTER_META)}

@app.on_event("startup")
def on_startup():
    try:
        info = build_roster_maps(SEASON_2024_25)
        print(f"[startup] roster loaded: {info}")
    except Exception as e:
        print(f"[startup] roster load failed: {e}")


# -----------------------
# Game endpoints
# -----------------------
@app.get("/game/cases_by_slot")
def generate_cases_by_slot(seed: int = 1, slot: str = "PG"):
    candidates = build_candidates(slot=slot)
    cases = build_case_pool_from_candidates(candidates, seed)
    return {"season": SEASON_2024_25, "seed": seed, "slot": slot.upper().strip(), "cases": cases}


@app.get("/game/banker_offer")
def banker_offer(
    slot: str = Query(..., min_length=1),
    target_tier: int = Query(..., ge=1, le=16),
    seed: int = 1,
    exclude_ids: str = "",
):
    """
    Returns ONE random player from the requested tier, from the FULL candidate pool
    for that slot, excluding players already used in the current 16-case set.

    exclude_ids: comma-separated player IDs
    """
    slot_norm = slot.upper().strip()
    if slot_norm not in {"PG", "SG", "SF", "PF", "C"}:
        raise HTTPException(status_code=400, detail="slot must be one of PG, SG, SF, PF, C")

    exclude: Set[int] = set()
    if exclude_ids.strip():
        for part in exclude_ids.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                exclude.add(int(part))
            except Exception:
                continue

    candidates = build_candidates(slot=slot_norm)
    candidates.sort(key=lambda x: x["score"], reverse=True)
    if len(candidates) < 16:
        raise HTTPException(status_code=400, detail=f"Not enough candidates for slot {slot_norm}.")

    tiers = split_into_16_tiers(candidates)

    rng = random.Random(seed)

    # try requested tier first, then expand outward if needed
    desired = clamp_tier(int(target_tier))
    tier_order = [desired]
    for d in range(1, 16):
        lo = desired - d
        hi = desired + d
        if lo >= 1:
            tier_order.append(lo)
        if hi <= 16:
            tier_order.append(hi)

    for t in tier_order:
        pool = [p for p in tiers[t - 1] if int(p["id"]) not in exclude]
        if pool:
            pick = rng.choice(pool)
            return {
                "season": SEASON_2024_25,
                "slot": slot_norm,
                "target_tier": desired,
                "picked_tier": t,
                "player": {"id": pick["id"], "name": pick["name"], "team": pick["team"]},
            }

    raise HTTPException(status_code=404, detail="No available banker offer found after exclusions.")
