"""
Tests for the /trade command's history fetch window (decision D5).

/trade must fetch 1y of history for its ATR-based stop/target estimate —
the same window /analyze and the alert loop use — not the old 6mo.
All Update/Context objects are mocked; log_trade is patched so no DB
(temp or real) is touched.
"""
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

AUTH_ID = "123456789"


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_update():
    update = MagicMock()
    update.effective_chat.id = AUTH_ID
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    return update


def _make_context(args=None):
    ctx = MagicMock()
    ctx.args = args or []
    return ctx


class TestTradeFetchWindow(unittest.TestCase):

    def _run_trade(self, get_historical_mock):
        import bot.telegram_bot as tb
        update = _make_update()
        ctx = _make_context(["BUY", "AAPL", "10", "150.0"])
        with patch("bot.telegram_bot.AUTHORIZED_CHAT_IDS", frozenset([AUTH_ID])), \
             patch("bot.telegram_bot.get_language", return_value="en"), \
             patch("bot.telegram_bot.log_trade") as log_trade_mock, \
             patch("bot.telegram_bot.get_historical", get_historical_mock):
            _run(tb.cmd_trade(update, ctx))
        return update, log_trade_mock

    def test_trade_fetches_one_year_history(self):
        """The ATR window for /trade must be 1y, matching /analyze (D5)."""
        fetch = MagicMock(return_value=None)
        update, log_trade_mock = self._run_trade(fetch)
        fetch.assert_called_once_with("AAPL", period="1y")
        log_trade_mock.assert_called_once()

    def test_trade_replies_even_when_history_unavailable(self):
        """A failed fetch degrades to N/A stop/target, never a crash."""
        fetch = MagicMock(return_value=None)
        update, _ = self._run_trade(fetch)
        update.message.reply_text.assert_called_once()
        self.assertIn("N/A", update.message.reply_text.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
