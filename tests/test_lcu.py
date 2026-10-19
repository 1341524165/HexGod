import sys
from pathlib import Path
import unittest
from unittest.mock import Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from lcu import LcuClient, parse_auth, parse_lockfile
from local_http import NoRedirect


class LcuTests(unittest.TestCase):
    def test_auth_parsing_and_validation(self):
        self.assertEqual(parse_auth('"LeagueClientUx.exe" "--app-port=2999" --remoting-auth-token=secret'), (2999,'secret'))
        self.assertEqual(parse_lockfile('LeagueClient:42:2999:secret:https'), (2999,'secret'))
        for command in ('--app-port=0 --remoting-auth-token=x','--app-port=65536 --remoting-auth-token=x','--app-port=42'):
            self.assertIsNone(parse_auth(command))
        self.assertIsNone(parse_lockfile('LeagueClient:42:2999:secret:http'))

    def test_discovery_is_throttled_without_client(self):
        discover=Mock(return_value=None)
        client=LcuClient(discovery=discover,clock=Mock(side_effect=[0,1,14,15]))
        for _ in range(4): self.assertFalse(client.read_state()['available'])
        self.assertEqual(discover.call_count,2)

    def test_state_has_no_credentials_or_account_payload(self):
        get=Mock(side_effect=['InProgress',{'gameData':{'gameId':123,'queue':{'id':2400,'gameMode':'ARAM'}},'token':'secret'}])
        client=LcuClient(discovery=lambda:(42,'secret'),get=get)
        state=client.read_state()
        self.assertEqual(state,{'available':True,'phase':'InProgress','gameId':'123','queueId':2400,'gameMode':'ARAM'})
        self.assertEqual([x.args[1] for x in get.call_args_list],['/lol-gameflow/v1/gameflow-phase','/lol-gameflow/v1/session'])

    def test_session_failure_preserves_phase_and_auth_failure_retries(self):
        get=Mock(side_effect=['InProgress',OSError(),OSError(),'Lobby'])
        discover=Mock(return_value=(42,'secret'))
        client=LcuClient(discovery=discover,get=get,clock=Mock(side_effect=[0,1,16]))
        self.assertEqual(client.read_state(),{'available':True,'phase':'InProgress'})
        self.assertFalse(client.read_state()['available'])
        self.assertEqual(client.read_state()['phase'],'Lobby')
        self.assertEqual(discover.call_count,2)

    def test_redirects_never_forward_loopback_credentials(self):
        self.assertIsNone(NoRedirect().redirect_request(None,None,302,'',{},'https://example.com'))
