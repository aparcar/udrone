# -*- coding: utf-8 -*-
from atexit import register
from errno import ENOENT
from errno import ETIMEDOUT, EOPNOTSUPP, EPROTO
from errno import EWOULDBLOCK
from functools import partial
from getopt import gnu_getopt
from os import environ
from sys import argv
import binascii
import json
import logging
import os
import select
import socket
import struct
import threading
import time
import fcntl


UDRONE_ADDR = ("239.6.6.6", 21337)
UDRONE_GROUP_DEFAULT = "!all-default"
UDRONE_MAX_DGRAM = 32 * 1024
UDRONE_RESENT_STRATEGY = [0.5, 1, 1]
UDRONE_IDLE_INTVAL = 19


class DroneNotReachableError(EnvironmentError):
    pass


class DroneNotFoundError(EnvironmentError):
    pass


class DroneRuntimeError(EnvironmentError):
    pass


class DroneConflict(EnvironmentError):
    pass


logger = logging.getLogger("udrone")
logger.setLevel(logging.DEBUG)


class DroneGroup(object):
    def __init__(self, host, groupid):
        self.host = host
        self.groupid = groupid
        self.idle_intval = UDRONE_IDLE_INTVAL
        self.timer = None
        self._timer_setup()
        self.seq = self.host.genseq()
        self.members = set()
        logger.debug("Group %s created.", self.groupid)

    def _timer_action(self):
        logger.debug("Group %s keep-alive timer triggered", self.groupid)
        if len(self.members) > 0:
            self.host.whois(self.groupid, need=0, seq=0)
        self._timer_setup()

    def _timer_setup(self):
        if self.timer:
            self.timer.cancel()
        self.timer = threading.Timer(self.idle_intval, self._timer_action)
        self.timer.setDaemon(True)
        self.timer.start()

    def assign(self, max_nodes: int, min_nodes: int = None, board: str = None) -> list:
        """
        Assign nodes to group

        Args:
            max_nodes (int): maximal number of nodes required
            min_nodes (int): mimimal number of nodes required
            board (str): limit assignment to specific board

        Returns:
            list: new member os group
        """

        if not min_nodes:
            min_nodes = max_nodes if max_nodes else 1
        available = self.host.whois(
            UDRONE_GROUP_DEFAULT, max_nodes, board=board
        ).keys()[:max_nodes]

        if len(available) < min_nodes:
            raise DroneNotFoundError((ENOENT, "You must construct additional drones"))
        new_members = self.engage(available)

        if len(new_members) < min_nodes:
            max_nodes -= len(new_members)
            available = self.host.whois(UDRONE_GROUP_DEFAULT, max_nodes).keys()[
                :max_nodes
            ]
            new_members += self.engage(available)

        if len(new_members) < min_nodes:
            if len(new_members) > 0:  # Rollback
                self.host.call_multi(new_members, None, "!reset", None, "status")
            raise DroneNotFoundError((ENOENT, "You must construct additional drones"))

        return new_members

    def engage(self, nodes):
        data = {"group": self.groupid, "seq": self.seq}
        ans = self.host.call_multi(nodes, None, "!assign", data, "status")
        members = []
        for member, answer in ans.iteritems():
            try:
                if answer["data"]["code"] == 0:
                    members.append(member)
            except Exception:
                pass
        self.members |= set(members)
        return members

    def reset(self, reset=None):

        if len(self.members) < 1:
            return
        expect = self.members.copy()
        self.host.reset(self.groupid, reset, expect)
        self.members = expect
        if len(expect) > 0:
            raise DroneNotReachableError((ETIMEDOUT, "Request Timeout", expect))

    def request(self, msg_type, data=None, timeout=60):

        if len(self.members) < 1:
            raise DroneNotFoundError((ENOENT, "Drone group is empty"))
        if msg_type[0] != "!":
            self.seq += 1
            seq = self.seq
        else:
            seq = self.host.genseq()

        pending = self.members.copy()
        i = 0
        answers = {}
        start = time.time()
        now = start
        self._timer_setup()

        while len(pending) > 0 and (now - start) >= 0 and (now - start) < timeout:
            expect = pending.copy()
            i += 1
            if i % 2 == 1:
                answers.update(
                    self.host.call(self.groupid, seq, msg_type, data, expect=expect)
                )
            else:
                self.host.recv_until(
                    answers,
                    seq,
                    expect=expect,
                    timeout=min(10, timeout - (now - start)),
                )

            for drone in expect:  # Timed out
                answers[drone] = None
            for drone, ans in answers.iteritems():
                if ans and ans["msg_type"] == "accept":
                    answers[drone] = None  # In Progress
                elif drone in pending and ans is not None:
                    pending.remove(drone)
            now = time.time()
            self._timer_setup()
        return answers

    def call(self, msg_type, data=None, timeout=60, update=None):

        res = self.request(msg_type, data, timeout)
        if update:
            update.update(res)
        for drone, answer in res.iteritems():
            if not answer:  # Some drone didn't answer
                raise DroneNotReachableError((ETIMEDOUT, "Request Timeout", [drone]))
            if drone not in self.members:  # Some unknown drone answered
                raise DroneConflict([drone])
            if answer["msg_type"] == "unsupported":
                raise DroneRuntimeError((EOPNOTSUPP, "Unknown Command", drone))
            try:
                if answer["msg_type"] == "status" and answer["data"]["code"] > 0:
                    errstr = None
                    if "errstr" in answer["data"]:
                        errstr = answer["data"]["errstr"]
                    raise DroneRuntimeError((answer["data"]["code"], errstr, drone))
            except Exception as e:
                if isinstance(e, DroneRuntimeError):
                    raise e
                else:
                    raise DroneRuntimeError((EPROTO, "Invalid Status Reply", drone))
        return update if update else res


class DroneHost(object):
    def __init__(self, interface=None, args=[]):
        self.args = args
        self.hostid = binascii.hexlify(os.urandom(3)).decode()
        self.uniqueid = f"Host {self.hostid}"
        self.addr = UDRONE_ADDR
        self.resent_strategy = UDRONE_RESENT_STRATEGY
        self.maxsize = UDRONE_MAX_DGRAM

        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind(("", 0))

        local_ip = self.get_ip_address(interface)

        self.socket.setsockopt(
            socket.SOL_IP, socket.IP_MULTICAST_IF, socket.inet_aton(local_ip)
        )

        self.socket.setblocking(0)

        self.poll = select.poll()
        self.poll.register(self.socket, select.POLLIN)

        self.groups = []
        logger.info(f"Initialized host on {interface} with ID {self.uniqueid}")

    def get_ip_address(self, interface: str) -> str:
        """
        Get IP of a local interface

        Args:
            interface (str): name of local interface

        Returns:
            str: IP address of interface
        """
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        return socket.inet_ntoa(
            fcntl.ioctl(
                s.fileno(),
                0x8915,
                struct.pack("256s", interface[:15].encode("utf-8")),  # SIOCGIFADDR
            )[20:24]
        )

    def genseq(self) -> int:
        """
        Generate random sequence number

        Returns:
            int: generated sequence
        """
        return struct.unpack("=I", os.urandom(4))[0] % 2000000000

    def send(self, to: str, seq: int, msg_type: str, data: dict = None):
        """
        Send message to drone

        Args:
            to (str): receiving group
            seq (int): sequence number
            msg_type (str): type of message to receive
            data (dict): data to send to node
        """
        msg = {"from": self.uniqueid, "to": to, "type": msg_type, "seq": seq}
        if data is not None:
            msg["data"] = data
        packet = json.dumps(msg, separators=(",", ":"))
        logger.debug(f"Sending: {packet}")
        self.socket.sendto(packet.encode("utf-8"), self.addr)

    def recv(self, seq: int, msg_type: str = None) -> dict:
        """
        Recevie messages from drones

        Args:
            seq (int): sequence number
            msg_type (str): type of message to receive

        Returns:
            dict: received message from drone
        """
        while True:
            try:
                msg = json.loads(self.socket.recv(self.maxsize))
                if (
                    msg["from"]
                    and msg["type"]
                    and msg["to"] == self.uniqueid
                    and (not msg_type or msg["type"] == msg_type)
                    and (not seq or msg["seq"] == seq)
                ):
                    logger.debug("Received: %s", str(msg))
                    return msg
            except Exception as e:
                if isinstance(e, socket.error) and e.errno == EWOULDBLOCK:
                    return None

    def recv_until(
        self,
        answers: dict,
        seq: int,
        msg_type: str = None,
        timeout: int = 1,
        expect: list = None,
    ):
        """
        Recevie messages from drones until requirement is fulfilled

        Args:
            answers (dict): Empty dict to be filled with received answers
            seq (int): sequence number
            msg_type (str): type of message to receive
            timeout (int): number of seconds before receiving timeouts
            expect (list): list of drones expected to anser
        """

        logger.debug(
            "Receiving replies for seq %i for %.1f secs expecting %s",
            seq,
            timeout,
            expect,
        )
        start = time.time()
        now = start
        while (
            (now - start) >= 0
            and (now - start) < timeout
            and (expect is None or len(expect) > 0)
        ):
            self.poll.poll((timeout - (now - start)) * 1000)
            while True:
                msg = self.recv(seq, msg_type)
                if msg:
                    answers[msg["from"]] = msg
                    if expect is not None and msg["from"] in expect:
                        expect.remove(msg["from"])
                elif not msg:
                    break
            now = time.time()

    def call(
        self,
        to: str,
        seq: int,
        msg_type: str,
        data: dict = None,
        resp_type: str = None,
        expect: list = None,
    ) -> dict:
        """
        Send data to drone and receive response

        Args:
            to (str): selected group
            seq (int): sequence number
            msg_type (str): send message of type
            data (dict): data to send to group
            resp_type (str): receive message of type
            expect (list): list of drones expected to anser

        Returns:
            dict: received message from drones
        """

        if not seq:
            seq = self.genseq()

        answers = {}

        for timeout in self.resent_strategy:
            self.send(to, seq, msg_type, data)
            self.recv_until(answers, seq, resp_type, timeout, expect)
            if expect is not None and len(expect) == 0:
                break
        return answers

    def call_multi(
        self,
        nodes: list,
        seq: int,
        msg_type: str,
        data: dict = None,
        resp_type: str = None,
    ) -> dict:
        """
        Send data to multiple drones and receive responses

        Args:
            nodes (list): selected drones
            seq (int): sequence number
            msg_type (str): send message of type
            data (dict): data to send to group
            resp_type (str): receive message of type

        Returns:
            dict: received message from drones
        """

        if not seq:
            seq = self.genseq()

        answers = {}

        for timeout in self.resent_strategy:
            for node in nodes:
                self.send(node, seq, msg_type, data)
            self.recv_until(answers, seq, resp_type, timeout, nodes)
            if len(nodes) == 0:
                break
        return answers

    def whois(
        self, group: str, need: int = None, seq: int = None, board: str = None
    ) -> dict:
        """
        Return online drones

        Args:
            group (str): limit request to specific group
            need (int): minimum number of ansers
            seq (int): sequence number
            board (str): limit request to specific board

        Returns:
            dict: received answers of boards
        """
        answers = {}
        if seq is None:
            seq = self.genseq()
        for timeout in self.resent_strategy:
            data = {}
            if board:
                data["board"] = board

            self.send(group, seq, "!whois", data)
            if need == 0:
                break
            self.recv_until(answers, seq, "status", timeout)
            if need and len(answers) >= need:
                break

        return answers

    def reset(self, whom, how=None, expect=None):
        data = {"how": how} if how else None
        return self.call(whom, None, "!reset", data, "status", expect)

    def Group(self, groupid, absolute=False):
        if not absolute:
            groupid += self.hostid
        if len(groupid) > 16:
            raise IndexError()
        group = DroneGroup(self, groupid)
        self.groups.append(group)
        return group

    def disband(self, reset=None):
        for group in self.groups:
            group.reset(reset)
        self.groups = []


# if __name__ == "__main__":
#    interface = None
#    debug = False
#
#    optlist, args = gnu_getopt(argv[1:], "i:d")
#    for key, val in optlist:
#        if key == "-i":
#            interface = val
#        elif key == "-d":
#            debug = True
#
#    logcons = logging.StreamHandler()
#
#    if not debug:
#        logcons.setLevel(logging.WARNING)
#    logging.getLogger("udrone").addHandler(logcons)
#
#    self = DroneHost(interface, args=args[1:])
#
#    def teardown(host):
#        try:
#            host.disband()
#        except Exception:
#            pass
#
#    register(partial(teardown, self))
#
#    environ["PYTHONINSPECT"] = "1"
#
#    print("Welcome to the udrone interactive Python shell!")
#    print("\nudrone Commands:")
#    print("self.whois(target)		# Send an echo-request")
#    print("self.reset(target, <'system'>)	# Reset nodes ('system' requests reboot)")
#    print("group = self.Group(prefix)	# Create new group (prefix length <= 10)")
#    print("self.execfile(path)		# Execute a script")
#    print("\nudrone Group Commands:")
#    print("group.assign(max, min = max)	# Assign a number of idle nodes")
#    print("group.engage([node, node, ...])	# Invite nodes by ID")
#    print("group.call(command, <data>)	# Send group-request and return replies")
#    print("group.reset(<'system'>)		# Disband group by resetting nodes")
#    print("\nScanning for idle drones...")
#    idle = self.whois("!all-default").keys()
#    if len(idle) == 0:
#        print("No drones found!")
#    else:
#        print("Found:", ", ".join(idle))
#    print("\nNow have fun...")
