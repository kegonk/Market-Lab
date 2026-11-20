"""Offline determinism, agent feedback, synthetic matching and UI service safety."""
import json
from pathlib import Path
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer
from threading import Thread

from market_lab.interactive import SessionConfig, SyntheticMarket
from market_lab.web import create_handler


class TestSyntheticMarket(unittest.TestCase):
    def test_session_same_seed_identical_trace(self):
        cfg = SessionConfig(seed=77, ticks=60, shock_tick=25)
        a, b = SyntheticMarket(cfg), SyntheticMarket(cfg)
        for _ in range(cfg.ticks):
            a.step(); b.step()
        self.assertEqual(a.fingerprint(), b.fingerprint())
        self.assertEqual(a.state(), b.state())

    def test_control_and_treatment_align_pre_shock_then_diverge(self):
        a = SyntheticMarket(SessionConfig(seed=4, ticks=55, shock_tick=25, shock_enabled=False))
        b = SyntheticMarket(SessionConfig(seed=4, ticks=55, shock_tick=25, shock_enabled=True))
        a.advance(24); b.advance(24)
        self.assertEqual(a.state()['market'], b.state()['market'])
        self.assertEqual(a.state()['recent_trades'], b.state()['recent_trades'])
        a.step(); b.step()
        self.assertEqual(a.shock['sell_requested'], b.shock['sell_requested'])
        self.assertGreater(b.shock['cancelled_qty'], 0)
        self.assertEqual(a.shock['cancelled_qty'], 0)
        self.assertLessEqual(b.shock['sell_filled'], b.shock['sell_requested'])
        self.assertTrue(b.book.check_conservation())

    def test_sessions_use_virtual_data_only(self):
        session = SyntheticMarket()
        self.assertEqual(session.state()['source'], 'SIMULATED')
        self.assertEqual(session.state()['mode'], 'SYNTHETIC_OFFLINE')
        self.assertEqual(session.state()['symbol'], 'VCOIN/VCU')
        self.assertTrue(session.state()['conservation_pass'])
        self.assertNotIn('account_id', json.dumps(session.state()))

    def test_candles_derived_from_trade_prices(self):
        session = SyntheticMarket(SessionConfig(ticks=40, shock_tick=20, candle_ticks=4))
        session.advance(30)
        for bar in session.state()['candles']:
            self.assertLessEqual(bar['low'],bar['open'])
            self.assertLessEqual(bar['low'],bar['close'])
            self.assertGreaterEqual(bar['high'],bar['open'])
            self.assertGreaterEqual(bar['high'],bar['close'])
            self.assertGreaterEqual(bar['volume'],0)
        self.assertTrue(session.state()['conservation_pass'])
        self.assertGreater(session.state()['total_trades'],0)

    def test_agents_activity_and_no_overrun(self):
        session=SyntheticMarket(SessionConfig(ticks=40,shock_tick=11))
        session.advance(100)
        state=session.state()
        self.assertEqual(state['tick'],40)
        self.assertTrue(state['done'])
        self.assertEqual(sum(state['agent_actions'].values()),39*6)
        session.step()
        self.assertEqual(session.state(), state)

    def test_config_and_step_validation(self):
        for bad in (dict(seed=True),dict(ticks=2),dict(shock_tick=5000),
                    dict(cancel_percent=140),dict(events_per_tick=0),dict(shock_enabled='true')):
            with self.subTest(bad=bad),self.assertRaises(ValueError):
                SessionConfig(**bad)
        session=SyntheticMarket()
        for val in (0,-1,101,True,1.5):
            with self.subTest(val=val),self.assertRaises(ValueError):
                session.advance(val)

    def test_static_pages_no_cdn_or_exchange(self):
        static=Path(__file__).resolve().parents[1]/'market_lab'/'static'
        html=(static/'index.html').read_text(encoding='utf-8')
        script=(static/'app.js').read_text(encoding='utf-8')
        self.assertIn('SIMULATED',html)
        self.assertIn('/api/step',script)
        self.assertNotIn('https://',html+script)
        self.assertNotIn('wss://',html+script)


class TestLocalApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),create_handler())
        cls.thread=Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base=f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join(timeout=2)

    def fetch(self,method,path,body=None,headers=None):
        raw=None if body is None else json.dumps(body).encode()
        request=Request(self.base+path,data=raw,headers=headers or {},method=method)
        with urlopen(request,timeout=3) as response:
            return response.status,response.read(),response.headers

    def test_get_and_post(self):
        status,html,head=self.fetch('GET','/')
        self.assertEqual(status,200)
        self.assertIn(b'MARKET',html)
        self.assertIn('frame-ancestors',head['Content-Security-Policy'])
        _,body,_=self.fetch('GET','/api/state')
        self.assertEqual(json.loads(body)['tick'],0)
        _,body,_=self.fetch('POST','/api/step',{'count':2},{'Content-Type':'application/json'})
        self.assertEqual(json.loads(body)['tick'],2)
        _,body,_=self.fetch('POST','/api/reset',{'seed':9,'ticks':60,'shock_tick':30}, {'Content-Type':'application/json'})
        self.assertEqual(json.loads(body)['tick'],0)
        self.assertEqual(json.loads(body)['config']['seed'],9)

    def test_reject_cross_site_and_nonjson(self):
        with self.assertRaises(HTTPError) as e:
            self.fetch('POST','/api/reset',{}, {'Content-Type':'text/plain'})
        self.assertEqual(e.exception.code,415)
        with self.assertRaises(HTTPError) as e:
            self.fetch('POST','/api/step',{'count':1},{'Content-Type':'application/json','Origin':'https://attacker.example'})
        self.assertEqual(e.exception.code,403)
        with self.assertRaises(HTTPError) as e:
            self.fetch('POST','/api/step',{'count':1},{'Content-Type':'application/json','Sec-Fetch-Site':'cross-site'})
        self.assertEqual(e.exception.code,403)


if __name__ == '__main__': unittest.main()
