# -*- test-case-name: vumi.blinkenlights.heartbeat.tests.test_monitor -*-

import time

from twisted.internet.defer import inlineCallbacks
from twisted.internet.task import LoopingCall

from vumi.service import Worker
from vumi.blinkenlights.heartbeat.publisher import HeartBeatMessage
from vumi.blinkenlights.heartbeat.storage import Storage
from vumi.persist.txredis_manager import TxRedisManager
from vumi.utils import generate_worker_id
from vumi.errors import ConfigError
from vumi.log import log


# helper to check whether a config key is defined
def assert_field(cfg, key):
    if key not in cfg:
        raise ConfigError("Expected '%s' field in config" % key)


class WorkerInstance(object):
    """
    A hostname, port pair which uniquely identifies a worker instance.
    Made this into an object since we need to be able to perform identity
    operations on the pair. hash(), eq() for example.
    """

    def __init__(self, hostname, pid):
        self.hostname = hostname
        self.pid = pid

    def __eq__(self, obj):
        if not isinstance(obj, WorkerInstance):
            return NotImplemented
        return (self.hostname == obj.hostname and
                self.pid == obj.pid)

    def __hash__(self):
        return hash((self.hostname, self.pid))


class HeartBeatMonitor(Worker):

    DEFAULT_DEADLINE = 30

    # Instance vars:
    #
    # _storage: Thin wrapper around TxRedisManager. Used to persist
    #           worker data to redis
    #
    # _systems: Describes which workers belong to which system
    # _workers: Worker metadata which we need to keep around in memory
    #
    # _instance_sets: A dict which maps from a worker_id to a set. Tracks
    #                 which worker instances have checked-in the last 30s.

    @inlineCallbacks
    def startWorker(self):
        log.msg("Heartbeat monitor initializing")

        self.deadline = self.config.get("deadline", self.DEFAULT_DEADLINE)

        redis_cfg = self.config['redis_manager']
        self._redis = yield TxRedisManager.from_config(redis_cfg)
        self._storage = Storage(self._redis)
        self._systems, self._workers = self.parse_config(self.config)

        # synchronize with redis
        self._sync_to_redis()

        # Start consuming heartbeats
        yield self.consume("heartbeat.inbound", self.consume_message,
                           exchange_name='vumi.health',
                           message_class=HeartBeatMessage)

        self._start_verification_task()

    def stopWorker(self):
        log.msg("HeartBeat: stopping worker")
        if self._task:
            self._task.stop()
            self._task = None

    def parse_config(self, config):
        """
        Parse configuration and populate in-memory state
        """
        assert_field(config, 'monitored_systems')
        systems = {}
        workers = {}
        # loop over each defined system
        for sys in config['monitored_systems'].values():
            assert_field(sys, 'workers')
            assert_field(sys, 'system_id')
            system_id = sys['system_id']
            systems[system_id] = []
            # loop over each defined worker in the system
            for wkr_entry in sys['workers'].values():
                assert_field(wkr_entry, 'name')
                assert_field(wkr_entry, 'min_procs')
                worker_name = wkr_entry['name']
                min_procs = wkr_entry['min_procs']
                worker_id = generate_worker_id(system_id, worker_name)
                wkr = workers[worker_id] = {}
                wkr['system_id'] = system_id
                wkr['worker_id'] = worker_id
                wkr['worker_name'] = worker_name
                wkr['min_procs'] = min_procs
                systems[system_id].append(worker_id)
        return systems, workers

    def update(self, msg):
        """
        Process a heartbeat message.
        """
        worker_id = msg.get('worker_id', None)
        timestamp = msg.get('timestamp')
        hostname = msg.get('hostname', None)
        pid = msg.get('pid', None)

        # A bunch of discard rules:
        # 1. missing fields (mostly for older message versions)
        # 2. Unknown worker (Monitored workers need to be in the config)
        # 3. Message which are too old.

        if not (worker_id and timestamp and hostname and pid):
            log.msg("Discarding message. mandatory fields not set")
            return
        wkr = self._workers.get(worker_id, None)
        if wkr is None:
            log.msg("Discarding message. worker '%s' is unknown" % worker_id)
            return
        if timestamp < (time.time() - self.deadline):
            return

        # Add worker instance to the instance set for the worker species
        ins = WorkerInstance(hostname, pid)
        instances = self._instance_sets[worker_id]
        instances.add(ins)

    def _setup_instance_sets(self):
        """ Create a set for each worker species """
        self._instance_sets = {}
        for wkr_id in self._workers.keys():
            self._instance_sets[wkr_id] = set()

    def _verify_workers(self):
        """
        For now, verify that each worker species has more than min_procs
        running
        """
        lst_fail = []
        lst_pass = []
        for wkr_id, instances in self._instance_sets.iteritems():
            min_procs = self._workers[wkr_id]['min_procs']
            # check whether enough workers checked in
            proc_count = len(instances)
            if proc_count < min_procs:
                lst_fail.append((wkr_id, proc_count))
            else:
                lst_pass.append((wkr_id,))
            # update host:instance_count info for each worker
            # if no instances checked-in, well then we just have to delete
            # the existing hostinfo
            hostinfo = self._compute_hostinfo(instances)
            if len(hostinfo) > 0:
                self._storage.set_worker_hostinfo(wkr_id, hostinfo)
            else:
                self._storage.delete_worker_hostinfo(wkr_id)
        return lst_fail, lst_pass

    def _compute_hostinfo(self, instances):
        """ Compute the number of worker instances running on each host. """
        info = {}
        # initialize per-host counters
        for ins in instances:
            info[ins.hostname] = 0
        # update counters for each instance
        for ins in instances:
            info[ins.hostname] = info[ins.hostname] + 1
        return info

    def _sync_to_redis(self):
        """
        Persist the loaded configuration to redis, possibly overwriting
        any existing data.
        """
        system_ids = self._systems.keys()
        self._storage.set_systems(system_ids)
        for system_id, wkrs in self._systems.iteritems():
            self._storage.set_system_workers(system_id, wkrs)
        for wkr_id, attrs in self._workers.iteritems():
            self._storage.set_worker_attrs(wkr_id, attrs)

    def _process_failures(self, wkrs):
        """
        For now, we only handle failures of the min_procs constraint.

        For each failing worker, we record the start time of the failure
        (if it has not already been set), and the current number of procs.

        """
        for wkr in wkrs:
            log.msg("Worker %s failed min-procs verification. "
                    "%i procs checked in" % wkr)
            issue = {
                "issue_type": "min-procs-fail",
                "start_time": time.time(),
                "procs_count": wkr[1],
            }
            self._storage.open_or_update_issue(wkr[0], issue)

    def _process_passes(self, wkrs):
        """ Just delete any open issues """
        for wkr in wkrs:
            self._storage.delete_worker_issue(wkr[0])

    def _run_verification(self):
        """
        Iterate over worker instance sets and check to see whether any have not
        checked-in on time.
        """
        wkrs_fail, wkrs_pass = self._verify_workers()
        self._process_failures(wkrs_fail)
        self._process_passes(wkrs_pass)
        # setup instance sets for next verification pass
        self._setup_instance_sets()

    def _start_verification_task(self):
        """ Create a timer task to check for missing worker """
        self._task = LoopingCall(self._run_verification)
        done = self._task.start(self.deadline, now=False)
        errfn = lambda failure: log.err(failure,
                                        "Heartbeat verify: timer task died")
        done.addErrback(errfn)
        self._setup_instance_sets()

    def consume_message(self, msg):
        log.msg("Received message: %s" % msg)
        self.update(msg.payload)
