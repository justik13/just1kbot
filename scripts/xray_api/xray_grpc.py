import logging
import sys
from pathlib import Path
from typing import Dict

import grpc

GENERATED_DIR = Path(__file__).parent / "generated"
if str(GENERATED_DIR) not in sys.path:
    sys.path.insert(0, str(GENERATED_DIR))

try:
    from xray.common.serial import typed_message_pb2
    from xray.common.protocol import user_pb2
    from xray.proxy.vless import account_pb2
    from xray.app.proxyman.command import command_pb2 as proxyman_cmd
    from xray.app.proxyman.command import command_pb2_grpc as proxyman_grpc
    from xray.app.stats.command import command_pb2 as stats_cmd
    from xray.app.stats.command import command_pb2_grpc as stats_grpc

except ImportError:
    proxyman_cmd = None
    proxyman_grpc = None
    stats_cmd = None
    stats_grpc = None
    user_pb2 = None
    typed_message_pb2 = None
    account_pb2 = None

logger = logging.getLogger(__name__)


class XrayGrpcClient:
    """gRPC client to Xray core HandlerService and StatsService."""

    def __init__(self, host: str = "127.0.0.1", port: int = 10085, timeout: float = 5.0):
        self.host = host
        self.port = port
        self.target = f"{host}:{port}"
        self.timeout = timeout
        self._channel = None

    def _get_channel(self) -> grpc.Channel:
        if self._channel is None:
            self._channel = grpc.insecure_channel(self.target)
        return self._channel

    get_channel = _get_channel

    def close(self) -> None:
        if self._channel is not None:
            try:
                self._channel.close()
            except Exception:
                pass
            self._channel = None

    def is_healthy(self) -> bool:
        if stats_grpc is None or stats_cmd is None:
            return False
        try:
            channel = self._get_channel()
            stub = stats_grpc.StatsServiceStub(channel)
            stub.QueryStats(
                stats_cmd.QueryStatsRequest(pattern="", reset=False),
                timeout=min(self.timeout, 2.0),
            )
            return True

        except grpc.RpcError as exc:
            if exc.code() in (grpc.StatusCode.OK, grpc.StatusCode.NOT_FOUND, grpc.StatusCode.INVALID_ARGUMENT):
                return True
            logger.debug("gRPC health check RPC error: %s (%s)", exc.code(), exc.details())
            self.close()
            return False
        except Exception as exc:
            logger.debug("gRPC health check connection failed: %s", exc)
            self.close()
            return False


    def add_user(self, inbound_tag: str, user_id: str, flow: str = "") -> bool:
        if proxyman_cmd is None or proxyman_grpc is None:
            raise RuntimeError("Protobuf modules not loaded")

        account = account_pb2.Account(id=user_id, flow=flow, encryption="none")
        account_typed = typed_message_pb2.TypedMessage(
            type="xray.proxy.vless.Account", value=account.SerializeToString()
        )
        user = user_pb2.User(level=0, email=user_id, account=account_typed)
        add_user_op = proxyman_cmd.AddUserOperation(user=user)
        op_typed = typed_message_pb2.TypedMessage(
            type="xray.app.proxyman.command.AddUserOperation",
            value=add_user_op.SerializeToString(),
        )
        req = proxyman_cmd.AlterInboundRequest(tag=inbound_tag, operation=op_typed)

        channel = self._get_channel()
        stub = proxyman_grpc.HandlerServiceStub(channel)
        try:
            stub.AlterInbound(req, timeout=self.timeout)
            return True
        except grpc.RpcError as exc:
            details = (exc.details() or "").lower()
            if "already exists" in details or "duplicate" in details:
                return True
            logger.error("AlterInbound AddUser failed: %s", exc)
            self.close()
            raise

    def remove_user(self, inbound_tag: str, user_id: str) -> bool:
        if proxyman_cmd is None or proxyman_grpc is None:
            raise RuntimeError("Protobuf modules not loaded")

        remove_user_op = proxyman_cmd.RemoveUserOperation(email=user_id)
        op_typed = typed_message_pb2.TypedMessage(
            type="xray.app.proxyman.command.RemoveUserOperation",
            value=remove_user_op.SerializeToString(),
        )
        req = proxyman_cmd.AlterInboundRequest(tag=inbound_tag, operation=op_typed)

        channel = self._get_channel()
        stub = proxyman_grpc.HandlerServiceStub(channel)
        try:
            stub.AlterInbound(req, timeout=self.timeout)
            return True
        except grpc.RpcError as exc:
            details = (exc.details() or "").lower()
            if "not found" in details or "does not exist" in details:
                return True
            logger.error("AlterInbound RemoveUser failed: %s", exc)
            self.close()
            raise

    @staticmethod
    def _aggregate_query_stats(resp) -> Dict[str, Dict[str, int]]:
        result: Dict[str, Dict[str, int]] = {}
        for stat in resp.stat:
            parts = stat.name.split(">>>")
            if len(parts) != 4 or parts[0] != "user" or parts[2] != "traffic":
                continue
            email, direction = parts[1], parts[3]
            if direction not in ("uplink", "downlink") or not email:
                continue
            result.setdefault(email, {"uplink": 0, "downlink": 0})[direction] += int(stat.value)
        return result

    @staticmethod
    def _aggregate_user_stats(resp) -> Dict[str, Dict[str, int]]:
        result: Dict[str, Dict[str, int]] = {}
        for user in resp.users:
            email = user.email
            if not email:
                continue
            result.setdefault(email, {"uplink": 0, "downlink": 0})
            if user.traffic is not None:
                result[email]["uplink"] += int(user.traffic.uplink)
                result[email]["downlink"] += int(user.traffic.downlink)
        return result

    def get_users_stats(self, reset: bool = False) -> Dict[str, Dict[str, int]]:
        """Return traffic aggregated by email across all Xray inbounds.

        QueryStats is authoritative for traffic accounting because it reads the
        persistent user>>>email>>>traffic>>>direction counters. GetUsersStats
        is retained as a compatibility fallback for cores that do not expose
        those counters through QueryStats.
        """
        if stats_cmd is None or stats_grpc is None:
            raise RuntimeError("Protobuf modules not loaded")

        channel = self._get_channel()
        stub = stats_grpc.StatsServiceStub(channel)
        try:
            resp = stub.QueryStats(

                stats_cmd.QueryStatsRequest(pattern="user>>>", reset=reset),
                timeout=self.timeout,
            )
            result = self._aggregate_query_stats(resp)
            if result:
                return result
            logger.warning("QueryStats returned no user traffic counters; trying GetUsersStats")
        except grpc.RpcError as exc:
            logger.warning("QueryStats(user>>>) failed: %s; trying GetUsersStats", exc)

        try:
            resp = stub.GetUsersStats(
                stats_cmd.GetUsersStatsRequest(include_traffic=True, reset=reset),
                timeout=self.timeout,
            )
            return self._aggregate_user_stats(resp)
        except grpc.RpcError as exc:
            logger.error("Both Xray traffic statistics APIs failed: %s", exc)
            self.close()
            raise

