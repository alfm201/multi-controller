"""Control-plane 반복 동작과 장애 전환 시나리오 회귀 테스트."""

from coordinator.client import CoordinatorClient
from coordinator.election import pick_coordinator
from coordinator.service import CoordinatorService
from network.dispatcher import FrameDispatcher
from network.peer_registry import PeerRegistry
from routing.router import InputRouter
from routing.sink import InputSink
from runtime.context import NodeInfo, RuntimeContext


class WireConn:
    """두 dispatcher 사이를 동기적으로 이어 주는 테스트용 연결."""

    def __init__(self, sender_id, receiver_id, dispatcher):
        self.sender_id = sender_id
        self.receiver_id = receiver_id
        self.dispatcher = dispatcher
        self.closed = False
        self.frames = []

    def send_frame(self, frame):
        if self.closed:
            return False
        self.frames.append(dict(frame))
        self.dispatcher.dispatch(self.sender_id, dict(frame))
        return True

    def close(self):
        self.closed = True


def _nodes():
    return [
        NodeInfo.from_dict({"name": "A", "ip": "127.0.0.1", "port": 5000}),
        NodeInfo.from_dict({"name": "B", "ip": "127.0.0.1", "port": 5001}),
        NodeInfo.from_dict({"name": "C", "ip": "127.0.0.1", "port": 5002}),
    ]


class ControlPlaneHarness:
    def __init__(self):
        self.contexts = {}
        self.registries = {}
        self.dispatchers = {}
        self.services = {}
        self.clients = {}
        self.connections = {}

        nodes = _nodes()
        for node in nodes:
            self.contexts[node.node_id] = RuntimeContext(self_node=node, nodes=nodes)
            self.registries[node.node_id] = PeerRegistry()
            self.dispatchers[node.node_id] = FrameDispatcher()

        self.router_b = InputRouter(self.contexts["B"], self.registries["B"])
        self.sink_c = InputSink(require_authorization=True)

        for node_id in ("A", "B", "C"):
            self.services[node_id] = CoordinatorService(
                self.contexts[node_id],
                self.registries[node_id],
                self.dispatchers[node_id],
            )

        self.clients["B"] = CoordinatorClient(
            self.contexts["B"],
            self.registries["B"],
            self.dispatchers["B"],
            coordinator_resolver=lambda: pick_coordinator(self.contexts["B"], self.registries["B"]),
            router=self.router_b,
            sink=None,
        )
        self.clients["C"] = CoordinatorClient(
            self.contexts["C"],
            self.registries["C"],
            self.dispatchers["C"],
            coordinator_resolver=lambda: pick_coordinator(self.contexts["C"], self.registries["C"]),
            router=None,
            sink=self.sink_c,
        )

    def connect(self, left_id, right_id):
        left_conn = WireConn(left_id, right_id, self.dispatchers[right_id])
        right_conn = WireConn(right_id, left_id, self.dispatchers[left_id])

        self.connections[(left_id, right_id)] = left_conn
        self.connections[(right_id, left_id)] = right_conn
        assert self.registries[left_id].bind(right_id, left_conn) is True
        assert self.registries[right_id].bind(left_id, right_conn) is True

    def disconnect(self, left_id, right_id):
        for src, dst in ((left_id, right_id), (right_id, left_id)):
            conn = self.connections.pop((src, dst), None)
            if conn is None:
                continue
            conn.close()
            self.registries[src].unbind(dst, conn)

    def connect_all(self):
        self.connect("A", "B")
        self.connect("A", "C")
        self.connect("B", "C")

    def current_coordinator_id(self, node_id):
        coordinator = pick_coordinator(self.contexts[node_id], self.registries[node_id])
        return coordinator.node_id

    def assert_active_control(self):
        assert self.router_b.get_target_state() == "active"
        assert self.router_b.get_selected_target() == "C"
        assert self.sink_c.get_authorized_controller() == "B"


def test_repeated_claim_release_cycles_leave_clean_state():
    harness = ControlPlaneHarness()
    harness.connect_all()

    for _ in range(25):
        harness.clients["B"].request_target("C")

        assert harness.current_coordinator_id("B") == "A"
        harness.assert_active_control()
        assert harness.services["A"]._leases["C"]["controller_id"] == "B"

        harness.clients["B"].clear_target()

        assert harness.router_b.get_target_state() == "inactive"
        assert harness.router_b.get_selected_target() is None
        assert harness.sink_c.get_authorized_controller() is None
        assert "C" not in harness.services["A"]._leases


def test_repeated_failover_reasserts_active_lease_without_stale_authorization():
    harness = ControlPlaneHarness()
    harness.connect_all()

    harness.clients["B"].request_target("C")
    harness.assert_active_control()

    for _ in range(12):
        harness.disconnect("A", "B")
        harness.disconnect("A", "C")

        assert harness.current_coordinator_id("B") == "B"
        assert harness.current_coordinator_id("C") == "B"
        harness.clients["B"]._on_coordinator_changed("B")

        harness.assert_active_control()
        assert harness.services["B"]._leases["C"]["controller_id"] == "B"

        harness.connect("A", "B")
        harness.connect("A", "C")

        assert harness.current_coordinator_id("B") == "A"
        assert harness.current_coordinator_id("C") == "A"
        harness.clients["B"]._on_coordinator_changed("A")

        harness.assert_active_control()
        assert harness.services["A"]._leases["C"]["controller_id"] == "B"
