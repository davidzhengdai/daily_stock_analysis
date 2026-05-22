# -*- coding: utf-8 -*-
"""Regression tests for discovery-specific simulated trade parameters."""

from src.repositories.simtrade_repo import SimTradeRepo
from src.services.simtrade.order_service import OrderService
from src.storage import DatabaseManager


def test_place_order_accepts_discovery_stop_loss_take_profit_overrides():
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        repo = SimTradeRepo(db_manager=db)
        account = repo.get_or_create_account()
        repo.update_account(account["id"], cash_usd=10000.0, stop_loss_pct=20.0, take_profit_pct=60.0)
        service = OrderService(repo=repo)

        service.place_order(
            code="NVDA",
            market="US",
            side="buy",
            order_type="market",
            qty=10,
            name="NVIDIA",
            source="auto",
            current_price=100.0,
            stop_loss_pct=5.0,
            take_profit_pct=15.0,
        )

        position = repo.get_position(account["id"], "NVDA")
        assert position is not None
        assert position["stop_loss_price"] == 95.0
        assert position["take_profit_price"] == 115.0
    finally:
        DatabaseManager.reset_instance()


def test_place_order_accepts_dynamic_exit_price_overrides():
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        repo = SimTradeRepo(db_manager=db)
        account = repo.get_or_create_account()
        repo.update_account(account["id"], cash_usd=10000.0, stop_loss_pct=20.0, take_profit_pct=60.0)
        service = OrderService(repo=repo)

        service.place_order(
            code="NVDA",
            market="US",
            side="buy",
            order_type="market",
            qty=10,
            name="NVIDIA",
            source="auto",
            current_price=100.0,
            stop_loss_pct=5.0,
            take_profit_pct=15.0,
            stop_loss_price=97.0,
            take_profit_price=121.0,
        )

        position = repo.get_position(account["id"], "NVDA")
        assert position is not None
        assert position["stop_loss_price"] == 97.0
        assert position["take_profit_price"] == 121.0
    finally:
        DatabaseManager.reset_instance()


def test_place_order_rejects_invalid_dynamic_exit_price_direction():
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        repo = SimTradeRepo(db_manager=db)
        account = repo.get_or_create_account()
        repo.update_account(account["id"], cash_usd=10000.0)
        service = OrderService(repo=repo)

        service.place_order(
            code="NVDA",
            market="US",
            side="buy",
            order_type="market",
            qty=10,
            name="NVIDIA",
            source="auto",
            current_price=100.0,
            stop_loss_pct=5.0,
            take_profit_pct=15.0,
            stop_loss_price=105.0,
            take_profit_price=95.0,
        )

        position = repo.get_position(account["id"], "NVDA")
        assert position is not None
        assert position["stop_loss_price"] == 95.0
        assert position["take_profit_price"] == 115.0
    finally:
        DatabaseManager.reset_instance()
