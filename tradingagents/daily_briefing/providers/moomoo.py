"""Strictly read-only Moomoo OpenD adapter."""

from contextlib import contextmanager
from importlib import import_module

from ..models import BrokerPnlPosition, MoomooSecurity


class MoomooError(RuntimeError):
    pass


class MoomooReadOnlyProvider:
    def __init__(self, host: str, port: int, account_id: int | None = None, sdk=None):
        self.host, self.port, self.account_id, self._sdk = host, port, account_id, sdk

    @property
    def sdk(self):
        try:
            return self._sdk or import_module("moomoo")
        except ImportError as exc:
            raise MoomooError("Install daily dependencies: pip install 'tradingagents[daily]'") from exc

    @contextmanager
    def _quote_context(self):
        context = self.sdk.OpenQuoteContext(
            host=self.host, port=self.port, security_firm=self.sdk.SecurityFirm.FUTUMY
        )
        try:
            yield context
        finally:
            context.close()

    def _success(self, result, operation: str):
        ret, data = result
        if ret != self.sdk.RET_OK:
            raise MoomooError(f"OpenD {operation} failed: {data}")
        return data

    @staticmethod
    def _securities(data) -> list[MoomooSecurity]:
        records = data.to_dict("records") if hasattr(data, "to_dict") else data
        return [
            MoomooSecurity(str(row.get("code", "")), str(row.get("name", "")))
            for row in records
            if row.get("code")
        ]

    def watchlist_groups(self):
        with self._quote_context() as context:
            return self._success(
                context.get_user_security_group(self.sdk.UserSecurityGroupType.ALL), "get groups"
            )

    def watchlist(self, group_name: str) -> list[MoomooSecurity]:
        with self._quote_context() as context:
            data = self._success(context.get_user_security(group_name), "get watchlist")
        return self._securities(data)

    @contextmanager
    def _trade_context(self, market: str):
        context = self.sdk.OpenSecTradeContext(
            host=self.host,
            port=self.port,
            filter_trdmarket=getattr(self.sdk.TrdMarket, market),
            security_firm=self.sdk.SecurityFirm.FUTUMY,
        )
        try:
            yield context
        finally:
            context.close()

    def _eligible_account_ids(self, market: str) -> set[int]:
        with self._trade_context(market) as context:
            data = self._success(context.get_acc_list(), f"get {market} accounts")
        records = data.to_dict("records") if hasattr(data, "to_dict") else data
        return {
            int(row["acc_id"])
            for row in records
            if str(row.get("trd_env", "")).upper() == "REAL"
            and str(row.get("acc_status", "")).upper() == "ACTIVE"
            and str(row.get("acc_role", "")).upper() == "NORMAL"
        }

    def select_account_id(self) -> int:
        eligible = self._eligible_account_ids("US") & self._eligible_account_ids("MY")
        if self.account_id is not None:
            if self.account_id not in eligible:
                raise MoomooError("MOOMOO_ACCOUNT_ID is not an active real normal account for US and MY")
            return self.account_id
        if len(eligible) == 1:
            return eligible.pop()
        if not eligible:
            raise MoomooError("No active real normal Moomoo account is available to both US and MY")
        raise MoomooError("Multiple eligible accounts found; set MOOMOO_ACCOUNT_ID")

    @staticmethod
    def _number(value) -> float | None:
        try:
            return float(value) if value is not None and value != "" else None
        except (TypeError, ValueError):
            return None

    def position_pnl(self) -> list[BrokerPnlPosition]:
        """Return positive positions with Moomoo's current P&L fields."""
        account_id = self.select_account_id()
        output: list[BrokerPnlPosition] = []
        for market in ("US", "MY"):
            with self._trade_context(market) as context:
                data = self._success(
                    context.position_list_query(
                        acc_id=account_id,
                        position_market=getattr(self.sdk.TrdMarket, market),
                        refresh_cache=True,
                    ),
                    "get positions",
                )
            records = data.to_dict("records") if hasattr(data, "to_dict") else data
            for row in records:
                if float(row.get("qty", 0) or 0) > 0 and row.get("code"):
                    output.append(
                        BrokerPnlPosition(
                            security=MoomooSecurity(
                                str(row["code"]), str(row.get("stock_name", row.get("name", "")))
                            ),
                            market=market,
                            quantity=float(row["qty"]),
                            currency=str(row.get("currency", "") or "Unknown"),
                            market_value=self._number(row.get("market_val")),
                            unrealized_pnl=self._number(row.get("unrealized_pl")),
                            realized_pnl=self._number(row.get("realized_pl")),
                            pnl_ratio=self._number(row.get("pl_ratio")),
                        )
                    )
        return output

    def positions(self) -> list[MoomooSecurity]:
        """Compatibility view for the Phase 1 monitored-symbol universe."""
        return [item.security for item in self.position_pnl()]
