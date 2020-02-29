# -*- coding: utf-8 -*-

import json
import os
import sys
import time
import yaml
import logging

from .udronerc import DroneHost

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("qa")


def replace_tags(msg: str, data: dict):
    """Replaces values inside parameters based on conf.py

    Args:
      msg (str): Message containing tags
      data (dict): Mapping for tags
    """
    return msg.format(*data)


def cmd_sleep(v, c):
    """
    Sleep for N seconds

    Args:
        v: seconds to sleep
        c: unused
    """
    logger.debug(f"SLEEP {v[1]:d}")
    time.sleep(v[1])


def cmd_comment(args, counter):
    """
    Print comment inside the log

    Args:
        args (dict): must contain msg which will be logger.debuged
        counter (int): current interation in loop
    """
    comment = replace_tags(args["msg"], counter)
    logger.debug(f"COMMENT {comment}")


# this command can be prepended to any other command. it simply inverts the result
def cmd_fail(v, c):
    if cmd_map[v[1]] is not None:
        try:
            cmd_map[v[1]](v[1:], c)
        except:
            logger.debug("INVERT we expected this command to fail which it did")
            return 0
        logger.debug("ERROR command was supposed to fail")
        raise ExceptionClass(1000, "command should have failed", "foo")


# this command executes a shell script. if the script returns 0 we assume all is well
def cmd_raw(v, c):
    path = v[1]
    param = replace_tags(v[2], c)
    logger.debug("SCRIPT " + path + " " + param)
    ret = os.system(path + " " + param)
    if ret:
        raise ExceptionClass(1000, f"script returned {ret:d}", "foo")


cmds_meta = set(["local", "must_fail" "name"])

# this is the map of all complex call helpers
cmds_drone = {
    "checkip": {},
    "checknetmask": {},
    "cloudlogin": {},
    "cloudlogout": {},
    "cloudwispr": {},
    "comment": {},
    "dhcp": {},
    "dns_flood": {},
    "download": {},
    "essid": {},
    "fatserver": {},
    "getifaddrs": {},
    "ping": {},
    "readfile": {},
    "setmask": {},
    "sleep": {},
    "sysinfo": {},
    "system": {},
    "ubus": {},
    "uci_dump": {},
    "uci_get": {},
    "uci_replace": {},
    "uci_set": {},
    "upgrade": {},
    "webui_auth": {},
    "webui_ip": {},
    "webui_rpc": {},
}

cmd_local_map = {"sleep": cmd_sleep, "comment": cmd_comment, "raw": cmd_raw}


def run_test(test):
    """
    Run a actual test

    Args:
        test (test): the test to run
    """
    max = 1
    loop = 0

    # we can iterate  0->max
    try:
        if test["repeat"] is not None:
            max = test["repeat"]
    except:
        max = 1

    # or we iterate 0/first -> last
    try:
        if test["last"] is not None:
            max = test["last"]
        if test["first"] is not None:
            loop = test["first"]
            loop = loop - 1
    except:
        loop = 0

    # do $max iterations of the test set
    fail = 0
    while loop < max:
        loop = loop + 1
        logger.debug('RUN "' + test["desc"] + f'" - iteration {loop:d}')
        try:
            # loop over all commands and call them
            for task in test["tasks"]:
                if task.get("local", False):
                    res = run_task_local(task)
                else:
                    res = run_task_drone(task)
                res = cmd_map[task[0]](task, loop)
        except KeyboardInterrupt as e:
            # ctrl-C was hit
            exit(-1)
        except:
            # increment fail counter
            fail = fail + 1
            logger.debug(f"FAIL iterate {loop:d}")
            logger.debug(sys.exc_info())
    if fail > 0:
        raise ExceptionClass(1000, f"{fail:d} iterations failed", "foo")


def validate_task(task, cmds_available):
    task = task.copy()
    for meta_cmd in meta_cmds:
        task.pop(meta_cmd, None)

    if len(task_data.keys()) > 1:
        raise ExceptionClass(1000, "More than one task identifier provided!", "foo")
    elif len(task.keys()) == 0:
        raise ExceptionClass(1000, "No task identifier provided!", "foo")

    if task.keys()[0] not in cmds_available:
        raise ExceptionClass(1000, "Unknown task identifier provided!", "foo")

    task_id = task_data.keys()[0]

    return task_id, task[task_id]


def run_task_local(task):
    """
    Run a task locally on the udrone host

    Args:
        task (dict): Task to run locally
    """
    task_id, task_data = validate_task(task, cmd_local_map.keys())

    return cmd_local_map[task_id](task_data)


def run_task_drone(task):
    """
    Run a task remotely on udrone

    Args:
        task (dict): Task to run on drone
    """
    task_id, task_data = validate_task(task, cmds_drone)

    # TODO validate task args

    # per default we wait 10s for a reply
    timeout = 10
    # if a timeout value was passed, use it instead of the default
    if len(v) == 5:
        timeout = v[4]
    # check if the call has a payload
    if len(v) < 4:
        # no payload so do a flat call
        logger.debug('DRONE calling "' + v[2] + '"')
        return drone[v[1] - 1].call(v[2])
    else:
        # there is a payload, substitue global vars and iteration coutners
        payload = replace_tags(v[3], c)
        # issue the actual command
        logger.debug('DRONE calling "' + v[2] + '":' + json.dumps(payload))
        return drone[v[1] - 1].call(v[2], payload, task.get("timeout", 10))

    return cmd_drone


def online_drones():
    """Prints list of currently online drones"""
    online = host.whois("!all-default")
    logger.debug((online))
    if not online:
        logger.debug("No drones found!")
    else:
        logger.debug(f"Online drones: {len(online)}")
        for drone in online.values():
            logger.debug(drone)
            data = drone["data"]
            logger.debug(f"Drone {drone['from']} ({data['board']}) is online ")


if __name__ == "__main__":
    # load configuration
    with open("config.yml") as c:
        conf = yaml.safe_load(c.read())

    host = DroneHost(conf["ifname"])
    drone = []
# online_drones()


# if len(sys.argv) < 2:
#    logger.debug("Please specify action via args")
#    exit(-1)
#
# try:
#    f = open(sys.argv[1], "r")
#    buf = f.read()
#    f.close()
#    test = json.loads(buf)
#    logger.debug('START "' + test["id"] + '" - "' + test["desc"] + '"')
#    if test["drones"] is None:
#        logger.debug("ERROR no drones defined")
#        exit(-1)
# except:
#    logger.debug(sys.exc_info())
#    logger.debug("ERROR bad json")
#    exit(-1)
#
# drone_count = 0
# try:
#    for l in test["drones"]:
#        count = l
#        board = None
#        if type(l) == list:
#            count = l[0]
#            board = l[1]
#        d = 0
#        while d < count:
#            logger.debug(f"DRONE init unit {drone_count:d}")
#            drone.append(host.Group(f"Drone{drone_count:d}"))
#            drone[drone_count].assign(1, board=board)
#            d = d + 1
#            drone_count = drone_count + 1
#
# except:
#    logger.debug("ERROR failed to grab drones")
#    logger.debug(sys.exc_info())
#    exit(-1)
#
## iterate over the tests
# success = 0
# count = 0
# for t in test["test"]:
#    count = count + 1
#    try:
#        run_test(t)
#        logger.debug(f"PASS {count:d} " + test["id"])
#        success = success + 1
#        if t["sleep"]:
#            logger.debug(f"SLEEP {t['sleep']:d}")
#            time.sleep(t["sleep"])
#    except:
#        logger.debug(f"FAIL exception running {count:d} " + test["id"])
#        logger.debug(sys.exc_info())
#        time.sleep(5)
# d = 0
# while d < drone_count:
#    logger.debug(f"DRONE reset unit {d:d}")
#    cmd_drone(["DRONE", d, "!reset"], 1)
#    d = d + 1
#
# result = "FAIL"
# if success == len(test["test"]):
#    result = "PASS"
#
# logger.debug("RESULT " + result + f" {success:d}/{len(test['test']):d}")
