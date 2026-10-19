"""Read-only League Live Client Data API adapter for hero and match boundaries."""
from __future__ import annotations

import math
import time
from local_http import get_local_json


LOCAL_GAME_URL = "https://127.0.0.1:2999/liveclientdata/allgamedata"


def parse_live_game_data(data, alias_to_hero, now=None):
    if not isinstance(data, dict):
        return {"available": False, "hero": None, "matchStartMs": None,
                "itemsKnown": False, "itemIds": []}
    now = time.time() if now is None else now
    game = data.get("gameData") if isinstance(data.get("gameData"), dict) else {}
    game_time = game.get("gameTime")
    if (not isinstance(game_time, (int, float)) or isinstance(game_time, bool) or
            not math.isfinite(game_time) or game_time < 0):
        return {"available": False, "hero": None, "matchStartMs": None,
                "itemsKnown": False, "itemIds": []}
    active = data.get("activePlayer") if isinstance(data.get("activePlayer"), dict) else {}
    players = data.get("allPlayers") or []
    champion = active.get("championName")
    active_row = None
    if isinstance(players, list):
        # A shared game name is not an identity. Prefer complete IDs, then
        # legacy names; only use the short name when it is unambiguous.
        for key in ('riotId', 'summonerName', 'riotIdGameName'):
            value = active.get(key)
            if not isinstance(value, str) or not value:
                continue
            candidates = [p for p in players if isinstance(p, dict) and
                          str(p.get(key, '')).casefold() == value.casefold()]
            if len(candidates) == 1:
                active_row = candidates[0]
            # Never fall back to a weaker identifier after a complete ID
            # was provided but did not identify exactly one player.
            if active_row is not None or key in ('riotId', 'summonerName'):
                break
    if active_row is not None:
        champion = active_row.get('championName') or champion
    raw_items = active_row.get("items") if active_row is not None else active.get("items")
    item_ids = []
    item_counts = {}
    if isinstance(raw_items, list):
        for item in raw_items:
            if not isinstance(item, dict) or item.get("slot") == 6:
                continue
            item_id = item.get("itemID")
            if type(item_id) is int and item_id > 0:
                item_ids.append(item_id)
                count = item.get("count", 1)
                count = count if type(count) is int and 1 <= count <= 10 else 1
                item_counts[str(item_id)] = item_counts.get(str(item_id), 0) + count
    hero = alias_to_hero.get(str(champion or "").casefold())
    gold = active.get("currentGold")
    if type(gold) not in (int, float) or not math.isfinite(gold) or gold < 0:
        gold = None
    scores = (active_row or {}).get("scores")
    deaths = scores.get("deaths") if isinstance(scores, dict) else None
    if type(deaths) is not int or deaths < 0:
        deaths = None
    return {"available": True, "hero": hero, "championName": champion,
            "gameTime": float(game_time),
            "gameMode": game.get("gameMode"), "mapNumber": game.get("mapNumber"),
            "currentGold": gold, "deaths": deaths, "itemCounts": item_counts,
            "matchStartMs": int((now - float(game_time)) * 1000),
            "itemsKnown": isinstance(raw_items, list), "itemIds": item_ids}


def read_live_game_data(alias_to_hero, timeout=0.8):
    try:
        data = get_local_json(2999, '/liveclientdata/allgamedata', timeout=timeout)
        return parse_live_game_data(data, alias_to_hero)
    except (OSError, ValueError, TimeoutError) as error:
        return {"available": False, "hero": None, "matchStartMs": None,
                "itemsKnown": False, "itemIds": [], "connection": type(error).__name__}
