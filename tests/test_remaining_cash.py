import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
from unittest.mock import patch
from quant_news_monitor.portfolio_monitor import build_portfolio_analysis
from quant_news_monitor.portfolio_monitor import PortfolioMonitor
from quant_news_monitor.portfolio_book import get_portfolio
from quant_news_monitor.config_store import ConfigStore
from quant_news_monitor.models import utc_now
from quant_news_monitor.portfolio_dividends import reconcile_cash

class RemainingCashTests(unittest.TestCase):
    def test_payables_reduce_assets_not_cash_or_principal(self):
        a=dict(principal=7000,cash_balances={'CNY':100},receivable_balances={'USD':100},payable_balances={'HKD':800,'USD':50})
        rates={'CNY':1,'USD':7,'HKD':.875}
        s=self.calculate(a,rates=rates)['summary']
        self.assertEqual(s['cash_base'],100)
        self.assertEqual(s['receivable_base'],700)
        self.assertEqual(s['payable_base'],1050)
        self.assertEqual(s['net_assets_base'],1430)
        self.assertAlmostEqual(s['principal_return_pct'],(1430/7000-1)*100)
        self.assertEqual(a['principal'],7000)
        a['base_currency']='USD'
        self.assertEqual(self.calculate(a,rates=rates)['summary']['payable_base'],150)
        s=self.calculate(a,rates={'CNY':1,'USD':7})['summary']
        self.assertIsNone(s['payable_base']);self.assertIsNone(s['net_assets_base'])
        a['payable_balances']={'USD':1000}
        self.assertLess(self.calculate(a,rates=rates)['summary']['net_assets_base'],0)

    def test_payables_persist_clear_and_block_empty_book_deletion(self):
        with TemporaryDirectory() as root:
            p=Path(root)/'config.json';p.write_text('{}');store=ConfigStore(p)
            book=store.save_portfolio('应付账户')['id']
            store.set_portfolio_account({'portfolio_id':book,'payable_balances':{'KRW':'12.34','USD':1}})
            self.assertEqual(get_portfolio(store.read(),book)['payable_balances'],{'KRW':12.34,'USD':1})
            with self.assertRaises(ValueError):store.delete_portfolio(book)
            before=store.read()
            for value in [[],{'EUR':1},{'USD':-1},{'USD':True},{'CNY':'0.001'}]:
                with self.assertRaises(ValueError):store.set_portfolio_account({'portfolio_id':book,'payable_balances':value})
                self.assertEqual(store.read(),before)
            store.set_portfolio_account({'portfolio_id':book,'payable_balances':{}})
            self.assertEqual(get_portfolio(store.read(),book)['payable_balances'],{})

    def test_receivables_are_assets_but_never_cash(self):
        a=dict(principal=7000,cash_balances={'CNY':100},receivable_balances={'USD':100,'HKD':800})
        s=self.calculate(a,rates={'CNY':1,'USD':7,'HKD':.875})['summary']
        self.assertEqual(s['cash_base'],100)
        self.assertEqual(s['receivable_base'],1400)
        self.assertEqual(s['net_assets_base'],3180)
        a['base_currency']='USD'
        self.assertEqual(self.calculate(a,rates={'CNY':1,'USD':7,'HKD':.875})['summary']['receivable_base'],200)
        s=self.calculate(a,rates={'CNY':1,'USD':7})['summary']
        self.assertIsNone(s['receivable_base'])
        self.assertIsNone(s['net_assets_base'])

    def test_receivables_save_clear_and_prevent_deletion(self):
        with TemporaryDirectory() as root:
            p=Path(root)/'config.json';p.write_text('{}');store=ConfigStore(p)
            book=store.save_portfolio('应收账户')['id']
            store.set_portfolio_account({'portfolio_id':book,'receivable_balances':{'USD':'12.34'}})
            a=get_portfolio(store.read(),book)
            self.assertEqual(a['receivable_balances'],{'USD':12.34})
            self.assertNotIn('cash_balances',a)
            with self.assertRaises(ValueError):store.delete_portfolio(book)
            before=store.read()
            for value in [[],{'EUR':1},{'USD':-1},{'USD':True},{'CNY':'0.001'}]:
                with self.assertRaises(ValueError):store.set_portfolio_account({'portfolio_id':book,'receivable_balances':value})
                self.assertEqual(store.read(),before)
            store.set_portfolio_account({'portfolio_id':book,'receivable_balances':{}})
            self.assertEqual(get_portfolio(store.read(),book)['receivable_balances'],{})

    def test_multi_currency_cash_is_converted_once_independently_of_principal(self):
        a = dict(principal=7000, principal_currency='CNY', cash_mode='manual',
                 cash_balances={'CNY':1000, 'USD':100, 'HKD':800, 'KRW':10000})
        rates = {'CNY':1, 'USD':7, 'HKD':.875, 'KRW':.005}
        s = self.calculate(a, rates=rates)['summary']
        self.assertEqual(s['cash_base'],2450)
        self.assertEqual(s['net_assets_base'],4130)
        self.assertAlmostEqual(s['principal_return_pct'],(4130/7000-1)*100)
        self.assertEqual(len(s['cash_details']),4)
        a['base_currency']='USD'
        s = self.calculate(a, rates=rates)['summary']
        self.assertEqual(s['cash_base'],350)
        self.assertEqual(s['net_assets_base'],590)
        self.assertEqual(a['principal'],7000)

    def test_missing_one_cash_fx_never_becomes_partial_total(self):
        a = dict(principal=7000,cash_balances={'CNY':100,'USD':10,'HKD':20})
        s = self.calculate(a,positions=[],rates={'CNY':1,'USD':7})['summary']
        self.assertIsNone(s['cash_base'])
        self.assertIsNone(s['net_assets_base'])
        self.assertFalse(s['valuation_current'])
        a['cash_balances']['HKD']=0
        self.assertEqual(self.calculate(a,positions=[],rates={'CNY':1,'USD':7})['summary']['cash_base'],170)

    def test_stale_cash_fx_disables_current_return_but_keeps_reference(self):
        a = dict(principal=7000,cash_balances={'CNY':100,'USD':10})
        with patch('quant_news_monitor.portfolio_account.conversion_current', side_effect=lambda s,c,b,n:c==b):
            s = self.calculate(a,positions=[])['summary']
        self.assertEqual(s['cash_base'],170)
        self.assertFalse(s['valuation_current'])
        self.assertIsNone(s['principal_return_pct'])

    def test_multiple_cash_configuration_is_scoped_validated_and_preserves_principal(self):
        with TemporaryDirectory() as root:
            p=Path(root)/'config.json';p.write_text('{}');store=ConfigStore(p)
            store.set_portfolio_account({'principal':7000,'cash':50,'cash_currency':'CNY'})
            book=store.save_portfolio('独立账户')['id']
            store.set_portfolio_account({'portfolio_id':book,'principal':1000,'principal_currency':'USD'})
            old=store.read()['portfolio']
            store.set_portfolio_account({'portfolio_id':book,'cash_balances':{'CNY':0,'USD':'12.34','HKD':500}})
            self.assertEqual(store.read()['portfolio'],old)
            a=get_portfolio(store.read(),book)
            self.assertEqual((a['principal'],a['principal_currency']),(1000,'USD'))
            self.assertEqual(a['cash_balances'],{'CNY':0,'USD':12.34,'HKD':500})
            for bad in [{},[],{'EUR':2},{'USD':True},{'CNY':-1},{'USD':'NaN'},{'USD':1e13},{'USD':'0.001'},{'HKD':''}]:
                before=store.read()
                with self.subTest(bad=bad),self.assertRaises(ValueError):
                    store.set_portfolio_account({'portfolio_id':book,'cash_balances':bad})
                self.assertEqual(store.read(),before)
            with self.assertRaises(ValueError):store.set_portfolio_account({'portfolio_id':book,'cash':5})
            store.set_portfolio_account({'cash_balances':{'CNY':50,'USD':0}})
            self.assertEqual(store.read()['portfolio']['principal'],7000)
            self.assertNotIn('cash',store.read()['portfolio'])
            store.set_portfolio_account({'cash_mode':'auto'})
            self.assertEqual(self.calculate(store.read()['portfolio'])['summary']['cash_base'],5600)
            store.set_portfolio_account({'cash_mode':'manual'})
            self.assertEqual(self.calculate(store.read()['portfolio'])['summary']['cash_base'],50)

    def test_multi_currency_cash_only_book_collects_all_fx(self):
        with TemporaryDirectory() as root:
            p=Path(root)/'config.json';p.write_text(json.dumps({'portfolio':{'positions':[], 'cash_balances':{'USD':100,'HKD':800}, 'base_currency':'CNY'}}))
            monitor=PortfolioMonitor(str(p))
            def quote(symbol, timeout):
                return {'price':7 if symbol=='CNY=X' else 8,'currency':'CNY','status':'live','source':'Test FX','quote_time':utc_now().isoformat()}
            with patch('quant_news_monitor.portfolio_monitor._fetch_yahoo_quote',side_effect=quote):snapshot=monitor.refresh()
            self.assertEqual(snapshot['fx_rates']['USD'],7)
            self.assertEqual(snapshot['fx_rates']['HKD'],.875)
            self.assertEqual(build_portfolio_analysis([],snapshot,[],[],account=json.loads(p.read_text())['portfolio'])['summary']['cash_base'],1400)

    def test_dividends_credit_and_reverse_each_currency_without_cross_currency_mutation(self):
        a={'cash_balances':{'CNY':100,'USD':50},'principal':7000}
        row=dict(status='received',cash_action='credit',currency='HKD',net_amount=20)
        reconcile_cash(a,None,row)
        self.assertEqual(a['cash_balances'],{'CNY':100,'USD':50,'HKD':20})
        replacement={**row,'currency':'USD','net_amount':10}
        reconcile_cash(a,row,replacement)
        self.assertEqual(a['cash_balances'],{'CNY':100,'USD':60,'HKD':0})
        before=dict(a['cash_balances'])
        with self.assertRaises(ValueError):reconcile_cash(a,row,None)
        self.assertEqual(a['cash_balances'],before)
        self.assertEqual(a['principal'],7000)

    def test_cash_only_book_cannot_be_deleted(self):
        with TemporaryDirectory() as root:
            p=Path(root)/'config.json';p.write_text('{}');store=ConfigStore(p)
            book=store.save_portfolio('多币种现金')['id']
            store.set_portfolio_account({'portfolio_id':book,'cash_balances':{'USD':50}})
            with self.assertRaisesRegex(ValueError,'现金'):store.delete_portfolio(book)

    def calculate(self, account, positions=None, rates=None):
        positions = positions if positions is not None else [dict(market='US',symbol='AAPL',quantity=2,average_cost=100,currency='USD')]
        snapshot = dict(status='live',quotes=[dict(symbol='AAPL',price=120,previous_close=110,currency='USD',status='live',quote_time=utc_now().isoformat())],fx_rates=rates or {'CNY':1,'USD':7,'HKD':.875})
        return build_portfolio_analysis(positions,snapshot,[],[],account=account)

    def test_uninvested_principal_is_cash_not_a_loss(self):
        r=self.calculate(dict(principal=7000,principal_currency='CNY'))
        s=r['summary']
        self.assertEqual(s['cash_base'],5600)
        self.assertEqual(s['net_assets_base'],7280)
        self.assertAlmostEqual(s['principal_return_pct'],4)
        self.assertEqual(s['cash_status'],'estimated')
        self.assertIsNone(r['account']['cash'])

    def test_manual_zero_overrides_estimation_and_auto_can_resume(self):
        a=dict(principal=7000,cash=0)
        self.assertEqual(self.calculate(a)['summary']['cash_base'],0)
        a['cash_mode']='auto'
        self.assertEqual(self.calculate(a)['summary']['cash_base'],5600)

    def test_same_formula_after_currency_change_and_empty_portfolio(self):
        a=dict(principal=7000,base_currency='HKD')
        s=self.calculate(a)['summary']
        self.assertEqual(s['cash_base'],6400)
        self.assertAlmostEqual(s['principal_return_pct'],4)
        s=self.calculate(a,positions=[])['summary']
        self.assertEqual(s['cash_base'],8000)
        self.assertEqual(s['principal_return_pct'],0)

    def test_missing_principal_or_fx_is_not_zero_cash(self):
        for a,rates in [({},None),({'principal':7000,'base_currency':'HKD'},{'CNY':1,'USD':7})]:
            s=self.calculate(a,rates=rates)['summary']
            self.assertIsNone(s['cash_base'])
            self.assertIsNone(s['net_assets_base'])
            self.assertIsNone(s['principal_return_pct'])

    def test_estimated_negative_cash_is_visible_and_not_clamped(self):
        s=self.calculate({'principal':1000})['summary']
        self.assertEqual(s['cash_base'],-400)
        self.assertAlmostEqual(s['principal_return_pct'],28)

    def test_manual_configuration_persists_and_auto_ignores_blank_amount(self):
        with TemporaryDirectory() as root:
            p=Path(root)/'config.json';p.write_text('{}');store=ConfigStore(p)
            store.set_portfolio_account({'principal':7000,'cash_mode':'auto','cash':''})
            self.assertNotIn('cash',store.read()['portfolio'])
            with self.assertRaises(ValueError):store.set_portfolio_account({'cash_mode':'manual'})
            store.set_portfolio_account({'cash':0})
            self.assertEqual(store.read()['portfolio']['cash_mode'],'manual')
            store.set_portfolio_account({'cash_mode':'auto'})
            self.assertEqual(self.calculate(store.read()['portfolio'])['summary']['cash_base'],5600)

    def test_received_dividend_is_counted_once_in_auto_mode(self):
        with TemporaryDirectory() as root:
            p=Path(root)/'config.json';p.write_text(json.dumps({'portfolio':{'principal':7000,'positions':[dict(market='US',symbol='AAPL',quantity=2,average_cost=100,currency='USD')]}}));store=ConfigStore(p)
            row=store.save_dividend(dict(id=str(uuid4()),market='US',symbol='AAPL',currency='USD',status='received',payment_date='2026-01-01',gross_amount=10,tax=0,fee=0,cash_action='credit'))
            a=store.read()['portfolio'];self.assertNotIn('cash',a)
            self.assertEqual(self.calculate(a)['summary']['cash_base'],5670)
            store.delete_dividend(row['id'])
            self.assertEqual(self.calculate(store.read()['portfolio'])['summary']['cash_base'],5600)

    def test_recorded_sales_increase_cash_by_net_profit_not_sale_proceeds(self):
        with TemporaryDirectory() as root:
            p=Path(root)/'config.json';p.write_text(json.dumps({'portfolio':{'principal':7000}}));store=ConfigStore(p)
            store.add_historical_trade(dict(market='US',symbol='AAPL',quantity=1,average_cost=100,
                execution_price=120,currency='USD',fee=2,extra_buy_fee=1,
                trade_date='2026-11-01',cost_fees_confirmed=True))
            s=self.calculate(store.read()['portfolio'])['summary']
            self.assertEqual(s['cash_base'],5600+17*7)
            self.assertAlmostEqual(s['principal_return_pct'],5.7)

    def test_unknown_sale_cost_does_not_produce_a_complete_cash_estimate(self):
        with TemporaryDirectory() as root:
            p=Path(root)/'config.json';p.write_text(json.dumps({'portfolio':{'principal':7000}}));store=ConfigStore(p)
            store.add_historical_trade(dict(market='US',symbol='AAPL',quantity=1,average_cost=100,
                execution_price=120,currency='USD',fee=0,trade_date='2026-11-01',cost_fees_confirmed=True))
            account=store.read()['portfolio']
            account['trades'][0].update(fee=None,fee_complete=False,net_pnl=None)
            s=self.calculate(account)['summary']
            self.assertIsNone(s['cash_base'])
            self.assertIsNone(s['principal_return_pct'])

if __name__=='__main__':unittest.main()
