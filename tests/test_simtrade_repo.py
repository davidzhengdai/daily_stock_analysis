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


if __name__ == "__main__":
    unittest.main()
