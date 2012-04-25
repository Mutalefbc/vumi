# -*- test-case-name: vumi.persist.tests.test_txriak_manager -*-

"""A manager implementation on top of txriak."""

from txriak.riak import RiakClient, RiakObject, RiakMapReduce, RiakLink
from twisted.internet.defer import inlineCallbacks, gatherResults

from vumi.persist.model import Manager


class TxRiakManager(Manager):
    """A persistence manager for txriak."""

    call_decorator = staticmethod(inlineCallbacks)

    @classmethod
    def from_config(cls, config):
        bucket_prefix = config.pop('bucket_prefix')
        client = RiakClient(**config)
        return cls(client, bucket_prefix)

    def riak_object(self, cls, key):
        bucket_name = self.bucket_prefix + cls.bucket
        bucket = self.client.bucket(bucket_name)
        riak_object = RiakObject(self.client, bucket, key)
        riak_object.set_data({})
        riak_object.set_content_type("application/json")
        return riak_object

    def store(self, modelobj):
        d = modelobj._riak_object.store()
        d.addCallback(lambda result: modelobj)
        return d

    def load(self, cls, key):
        riak_object = self.riak_object(cls, key)
        d = riak_object.reload()
        d.addCallback(lambda result: cls(self, key, _riak_object=result)
                      if result.get_data() is not None else None)
        return d

    def load_list(self, cls, keys):
        deferreds = []
        for key in keys:
            deferreds.append(self.load(cls, key))
        return gatherResults(deferreds)

    def riak_map_reduce(self):
        return RiakMapReduce(self.client)

    def run_map_reduce(self, mapreduce, mapper_func):
        mapreduce_done = mapreduce.run()

        def map_results(raw_results):
            deferreds = []
            for row in raw_results:
                link = RiakLink(row[0], row[1])
                deferreds.append(mapper_func(self, link))
            return gatherResults(deferreds)

        mapreduce_done.addCallback(map_results)
        return mapreduce_done

    @inlineCallbacks
    def purge_all(self):
        buckets = yield self.client.list_buckets()
        deferreds = []
        for bucket_name in buckets:
            if bucket_name.startswith(self.bucket_prefix):
                bucket = self.client.bucket(bucket_name)
                deferreds.append(bucket.purge_keys())
        yield gatherResults(deferreds)
