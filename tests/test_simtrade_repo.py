# -*- coding: utf-8 -*-

import unittest

from src.repositories.simtrade_repo import SimTradeRepo
from src.storage import DatabaseManager, SimulatedAccount


class SimTradeRepoTest(unittest.TestCase):
    def setUp(self) -> None:
        DatabaseManager.reset_instance()
        self.db = DatabaseManager(db_url="sqlite:///:memory:")
        self.repo = SimTradeRepo(db_manager=self.db)

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()

    def test_get_or_create_account_creates_default_account(self) -> None:
        account = self.repo.get_or_create_account()

        self.assertEqual(account["id"], 1)
        self.assertEqual(account["name"], "模拟账户")

    def test_get_or_create_account_tolerates_duplicate_default_rows(self) -> None:
        with self.db.get_session() as session:
            session.add(SimulatedAccount(name="first"))
            session.add(SimulatedAccount(name="second"))
            session.commit()

        account = self.repo.get_or_create_account()

        self.assertEqual(account["id"], 1)
        self.assertEqual(account["name"], "first")

    def test_list_trade_history_includes_account_and_ai_reasoning(self) -> None:
        account = self.repo.get_or_create_account()
        signal = self.repo.create_signal(
            account["id"],
            code="AAPL",
            name="Apple",
            market="US",
            signal="buy",
            confidence=0.82,
            reasoning="Momentum and sentiment both improved.",
        )
        self.repo.create_order(
            account_id=account["id"],
            code="AAPL",
            name="Apple",
            market="US",
            currency="USD",
            side="buy",
            order_type="market",
            qty=3,
            fill_price=188.5,
            fill_qty=3,
            commission=1.0,
            realized_pnl=0.0,
            status="filled",
            source="auto",
            ai_signal_id=signal["id"],
        )

        history = self.repo.list_trade_history(account["id"], limit=10)

        self.assertEqual(len(history), 1)
        self.assertTrue(history[0]["id"].startswith("open-"))
        self.assertEqual(history[0]["status"], "open")
        self.assertEqual(history[0]["account_name"], "模拟账户")
        self.assertEqual(history[0]["ai_reasoning"], "Momentum and sentiment both improved.")
        self.assertEqual(history[0]["qty"], 3)
        self.assertEqual(history[0]["buy_price"], 188.5)

    def test_list_trade_history_backfills_sell_realized_pnl(self) -> None:
        account = self.repo.get_or_create_account()
        self.repo.create_order(
            account_id=account["id"],
            code="AAPL",
            name="Apple",
            market="US",
            currency="USD",
            side="buy",
            order_type="market",
            qty=10,
            fill_price=100.0,
            fill_qty=10,
            commission=1.0,
            status="filled",
            source="manual",
        )
        self.repo.create_order(
            account_id=account["id"],
            code="AAPL",
            name="Apple",
            market="US",
            currency="USD",
            side="sell",
            order_type="market",
            qty=4,
            fill_price=125.0,
            fill_qty=4,
            commission=1.0,
            status="filled",
            source="auto",
            rejection_reason="止盈触发",
        )

        history = self.repo.list_trade_history(account["id"], limit=10)

        self.assertEqual(history[0]["status"], "closed")
        self.assertEqual(history[0]["qty"], 4)
        self.assertEqual(history[0]["buy_price"], 100.0)
        self.assertEqual(history[0]["sell_price"], 125.0)
        self.assertEqual(history[0]["realized_pnl"], 100.0)
        self.assertEqual(history[0]["sell_reason"], "止盈触发")


if __name__ == "__main__":
    unittest.main()
