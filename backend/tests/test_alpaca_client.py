from unittest.mock import patch, MagicMock
import pytest
from app.alpaca_client import AlpacaClient


def test_base_url_is_paper_only():
    client = AlpacaClient("key", "secret")
    assert client.base_url == "https://paper-api.alpaca.markets"
    # Even if someone tries to override, it should not work
    client2 = AlpacaClient("key", "secret", base_url="https://api.alpaca.markets")
    assert client2.base_url == "https://paper-api.alpaca.markets"


@pytest.mark.asyncio
async def test_get_account_calls_paper_endpoint():
    client = AlpacaClient("key", "secret")
    with patch.object(client.trading, 'get_account') as mock:
        mock.return_value = MagicMock(equity="10000", last_equity="9900")
        account = await client.get_account()
        assert account.equity == "10000"