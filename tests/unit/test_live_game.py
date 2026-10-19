import sys
from pathlib import Path
import unittest

from hexgod.game.live import parse_live_game_data


class LiveGameTests(unittest.TestCase):
    def test_shared_game_names_never_select_another_players_items(self):
        data={'gameData':{'gameTime':10},'activePlayer':{'riotId':'Same#B','riotIdGameName':'Same'},
              'allPlayers':[{'riotId':'Same#A','riotIdGameName':'Same','championName':'Annie','items':[{'itemID':3089}]},
                            {'riotId':'Same#B','riotIdGameName':'Same','championName':'Brand','items':[]}]}
        result=parse_live_game_data(data,{'brand':'复仇焰魂','annie':'黑暗之女'})
        self.assertEqual(result['hero'],'复仇焰魂')
        self.assertEqual(result['itemIds'],[])
        del data['activePlayer']['riotId']
        self.assertFalse(parse_live_game_data(data,{})['itemsKnown'])

    def test_malformed_nested_payload_degrades_without_throwing(self):
        self.assertFalse(parse_live_game_data({'gameData':[]},{})['available'])
        result=parse_live_game_data({'gameData':{'gameTime':10},'activePlayer':[],'allPlayers':{}},{})
        self.assertTrue(result['available'])
        self.assertFalse(result['itemsKnown'])
    def test_active_player_hero_and_match_start(self):
        data = {
            "gameData": {"gameTime": 90.5, "gameMode": "ARAM"},
            "activePlayer": {"riotId": "Player#CN1"},
            "allPlayers": [
                {"riotId": "Other#CN1", "championName": "Annie"},
                {"riotId": "Player#CN1", "championName": "Brand"},
            ],
        }
        result = parse_live_game_data(data, {"brand": "复仇焰魂"}, now=1000)
        self.assertEqual(result["hero"], "复仇焰魂")
        self.assertEqual(result["matchStartMs"], 909500)

    def test_active_player_items_from_local_game_api(self):
        data = {
            "gameData": {"gameTime": 90},
            "activePlayer": {"riotId": "Player#CN1", "championName": "Brand"},
            "allPlayers": [
                {"riotId": "Other#CN1", "items": [{"itemID": 3089, "slot": 0}]},
                {"riotId": "Player#CN1", "items": [
                    {"itemID": 6653, "slot": 0}, {"itemID": 3020, "slot": 1},
                    {"itemID": 3340, "slot": 6}]},
            ],
        }
        result = parse_live_game_data(data, {"brand": "复仇焰魂"}, now=1000)
        self.assertTrue(result["itemsKnown"])
        self.assertEqual(result["itemIds"], [6653, 3020])

    def test_no_matching_player_does_not_invent_inventory(self):
        result = parse_live_game_data({"gameData": {"gameTime": 10},
                                       "activePlayer": {"riotId": "Player#CN1"},
                                       "allPlayers": [{"riotId": "Other#CN1", "items": []}]}, {}, now=100)
        self.assertFalse(result["itemsKnown"])

    def test_unknown_champion_keeps_game_state_without_wrong_hero(self):
        result = parse_live_game_data({"gameData": {"gameTime": 10}, "activePlayer": {
            "championName": "NewChampion"}}, {}, now=100)
        self.assertTrue(result["available"])
        self.assertIsNone(result["hero"])

    def test_bad_payload_is_unavailable(self):
        self.assertFalse(parse_live_game_data({}, {})["available"])

    def test_invalid_numeric_opening_fields_are_not_used(self):
        for value in (float("nan"), float("inf"), -1, True, "10"):
            self.assertFalse(parse_live_game_data({"gameData": {"gameTime": value}}, {})["available"])
        data = {"gameData": {"gameTime": 0}, "activePlayer": {
            "riotId": "P#1", "currentGold": float("nan")},
            "allPlayers": [{"riotId": "P#1", "scores": None}]}
        result = parse_live_game_data(data, {})
        self.assertIsNone(result["currentGold"])
        self.assertIsNone(result["deaths"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
