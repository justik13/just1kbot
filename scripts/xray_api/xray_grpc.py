import logging
import sys
from pathlib import Path
from typing import Dict

import grpc

# Add generated directory to sys.path so 'xray.*' can be imported
GENERATED_DIR = Path(__file__).parent / "generated"
if str(GENERATED_DIR) not in sys.path:
    sys.path.insert(0, str(GENERATED_DIR))

try:
    from xray.app.proxyman.command import command_pb2 as proxyman_cmd
    from xray.app.proxyman.command import command_pb2_grpc as proxyman_grpc
    from xray.app.stats.command import command_pb2 as stats_cmd
    from xray.app.stats.command import command_pb2_grpc as stats_grpc
    from xray.common.protocol import user_pb2
    from xray.common.serial import typed_message_pb2
    from xray.proxy.vless import account_pb2
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
    """
    gRPC client to Xray core (HandlerService and StatsService).
    Defaults to 127.0.0.1:10085.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 10085,
        timeout: float = 5.0,
    ):
        self.host = host
        self.port = port
        self.target = f"{host}:{port}"
        self.timeout = timeout

    def _get_channel(self) -> grpc.Channel:
        return grpc.insecure_channel(self.target)

    def is_healthy(self) -> bool:
        """
        Tests if Xray gRPC API is responsive.
        """
        if stats_grpc is None or stats_cmd is None:
            return False
        try:
            with self._get_channel() as channel:
                stub = stats_grpc.StatsServiceStub(channel)
                req = stats_cmd.QueryStatsRequest(pattern="", reset=False)
                stub.QueryStats(req, timeout=min(self.timeout, 2.0))
                return True
        except grpc.RpcError as e:
            if e.code() in (
                grpc.StatusCode.OK,
                grpc.StatusCode.NOT_FOUND,
                grpc.StatusCode.INVALID_ARGUMENT,
            ):
                return True
            logger.debug("gRPC health check RPC error: %s (%s)", e.code(), e.details())
            return False
        except Exception as e:
            logger.debug("gRPC health check connection failed: %s", e)
            return False

    def add_user(
        self, inbound_tag: str, user_id: str, flow: str = ""
    ) -> bool:
        """
        Adds a VLESS user with UUID to the specified inbound tag.
        Idempotent: if user already exists, returns True.
        """
        if proxyman_cmd is None or proxyman_grpc is None:
            raise RuntimeError("Protobuf modules not loaded")

        account = account_pb2.Account(
            id=user_id,
            flow=flow,
            encryption="none",
        )
        account_typed = typed_message_pb2.TypedMessage(
            type="xray.proxy.vless.Account",
            value=account.SerializeToString(),
        )
        user = user_pb2.User(
            level=0,
            email=user_id,
            account=account_typed,
        )
        add_user_op = proxyman_cmd.AddUserOperation(user=user)
        op_typed = typed_message_pb2.TypedMessage(
            type="xray.app.proxyman.command.AddUserOperation",
            value=add_user_op.SerializeToString(),
        )
        req = proxyman_cmd.AlterInboundRequest(
            tag=inbound_tag,
            operation=op_typed,
        )

        with self._get_channel() as channel:
            stub = proxyman_grpc.HandlerServiceStub(channel)
            try:
                stub.AlterInbound(req, timeout=self.timeout)
                logger.info("Added user %s to inbound %s", user_id, inbound_tag)
                return True
            except grpc.RpcError as e:
                details = (e.details() or "").lower()
                if "already exists" in details or "duplicate" in details:
                    logger.info(
                        "User %s already exists in %s (idempotent)",
                        user_id,
                        inbound_tag,
                    )
                    return True
                logger.error("AlterInbound AddUser failed: %s", e)
                raise

    def remove_user(self, inbound_tag: str, user_id: str) -> bool:
        """
        Removes a user with UUID (email=user_id) from the specified inbound tag.
        Idempotent: if user does not exist, returns True.
        """
        if proxyman_cmd is None or proxyman_grpc is None:
            raise RuntimeError("Protobuf modules not loaded")

        remove_user_op = proxyman_cmd.RemoveUserOperation(email=user_id)
        op_typed = typed_message_pb2.TypedMessage(
            type="xray.app.proxyman.command.RemoveUserOperation",
            value=remove_user_op.SerializeToString(),
        )
        req = proxyman_cmd.AlterInboundRequest(
            tag=inbound_tag,
            operation=op_typed,
        )

        with self._get_channel() as channel:
            stub = proxyman_grpc.HandlerServiceStub(channel)
            try:
                stub.AlterInbound(req, timeout=self.timeout)
                logger.info("Removed user %s from inbound %s", user_id, inbound_tag)
                return True
            except grpc.RpcError as e:
                details = (e.details() or "").lower()
                if "not found" in details or "does not exist" in details:
                    logger.info(
                        "User %s was not found in %s (idempotent)",
                        user_id,
                        inbound_tag,
                    )
                    return True
                logger.error("AlterInbound RemoveUser failed: %s", e)
                raise

    def get_users_stats(self, reset: bool = False) -> Dict[str, Dict[str, int]]:
        """
        Queries and aggregates traffic statistics by email (UUID).
        Returns clean dictionary: { uuid: { "uplink": int, "downlink": int } }.
        """
        if stats_cmd is None or stats_grpc is None:
            raise RuntimeError("Protobuf modules not loaded")

        result: Dict[str, Dict[str, int]] = {}

        with self._get_channel() as channel:
            stub = stats_grpc.StatsServiceStub(channel)

            # Strategy 1: Try GetUsersStats (modern Xray-core)
            try:
                req = stats_cmd.GetUsersStatsRequest(
                    include_traffic=True, reset=reset
                )
                resp = stub.GetUsersStats(req, timeout=self.timeout)
                for u in resp.users:
                    email = u.email
                    if not email:
                        continue
                    if email not in result:
                        result[email] = {"uplink": 0, "downlink": 0}
                    result[email]["uplink"] += int(u.traffic.uplink)
                    result[email]["downlink"] += int(u.traffic.downlink)
                return result
            except (grpc.RpcError, Exception) as e:
                logger.debug(
                    "GetUsersStats failed (%s), falling back to QueryStats",
                    e,
                )

            # Strategy 2: Fallback to QueryStats (pattern 'user>>>')
            try:
                req = stats_cmd.QueryStatsRequest(pattern="user>>>", reset=reset)
                resp = stub.QueryStats(req, timeout=self.timeout)
                for s in resp.stat:
                    # Name format: user>>><email>>>>traffic>>>uplink / downlink
                    parts = s.name.split(">>>")
                    if len(parts) >= 4 and parts[0] == "user" and parts[2] == "traffic":
                        email = parts[1]
                        direction = parts[3]
                        if email not in result:
                            result[email] = {"uplink": 0, "downlink": 0}
                        if direction == "uplink":
                            result[email]["uplink"] += int(s.value)
                        elif direction == "downlink":
                            result[email]["downlink"] += int(s.value)
                return result
            except grpc.RpcError as e:
                logger.error("QueryStats failed: %s", e)
                raise
