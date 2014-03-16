import json
import os
import logging
from os.path import expanduser
from threading import Thread
from time import sleep
from boto.ec2 import connect_to_region
from orchestration.definitions import Pool, InstanceDefinition

# Logging boilerplate.
logger = logging.getLogger('orchestrator')

# Dynamically create the exception classes.
for error_class in ['SecretsFileError', 'PreflightError', 'UnknownInstanceDefinitionMethod',
    'PoolTypeError', 'InstanceTypeError']:
    globals()[error_class] = type(error_class, (Exception,), {})

class Orchestrator(object):
    """
    Contains instance and pool definitions and methods for starting those instances and running the bootstrap
    sequences.
    """

    class AllInstanceAccessor(object):
        """
        Might want to do extra things in the future so provide an accessor for indirection.
        An example of something that could be useful is filtering out all instances that were not
        properly initialized.
        """

        def __get__(self, instance, _):
            if instance._all_instances:
                return instance._all_instances

            # Gather all the pool instances and regular instances into one list.
            instance._all_instances = []
            for pool in instance._pools:
                instance._all_instances += pool.instance_definitions
            instance._all_instances += instance._instances

            return instance._all_instances

    all_instances = AllInstanceAccessor()

    def __init__(self, aws_region):
        self._aws_region = aws_region
        # Read the secrets from ~/.orchestrator: line 1 = access key, line 2 = secret access key
        orchestrator_file = expanduser('~/.orchestrator')
        if os.path.isfile(orchestrator_file):
            with open(orchestrator_file) as secrets:
                self._access_key = secrets.readline().strip()
                self._secret_key = secrets.readline().strip()
        else:
            raise SecretsFileError, "Could not open {0} for reading access/secret key.".format(orchestrator_file)

        self._instances, self._pools = [], []
        self._cluster_facts, self._all_instances = None, None
        self._connection = connect_to_region(aws_region, aws_access_key_id=self._access_key,
            aws_secret_access_key=self._secret_key)

    def add_pool(self, *args, **kwargs):
        """
        Same reasoning as for add_instance().
        """
        pool = Pool(*args, **kwargs)
        self._pools.append(pool)
        return pool

    def add_instance(self, *args, **kwargs):
        """
        Instead of passing in the definitions during initialization we now force instances to be
        added with this method.
        """
        definition = InstanceDefinition(*args, **kwargs)
        self._instances.append(definition)
        return definition

    def _wait_for_ready(self, timeout, retries):
        """
        Wait until all instances are in 'running' state. Give up after a set number of retries.
        """
        retry_count = 0
        while not all([instance.state == "running" for instance in self.all_instances]):
            retry_count += 1
            logger.info("Not all instances are ready. Retrying after {0}s.".format(timeout))
            sleep(timeout)
            if retry_count > retries:
                logger.fatal("Some instances did not transition to 'running' state.")
                break

        not_running = [instance for instance in self.all_instances if not instance.state == 'running']
        for instance in not_running:
            logger.fatal("Instance did not transition to running state: instance name = {0}.", instance.name)

    @staticmethod
    def _start_threads_and_wait(threads):
        for t in threads: t.start()
        for t in threads: t.join()

    def _instance_init(self):
        """
        Instance accessor is lazy so force it to initialize the instances.
        """
        for x in self.all_instances:
            logger.info('Found instance definition: {0}.'.format(x.name))

    def __getattr__(self, item):
        """
        Some methods go directly to the underlying instances so just factor out the functionality with
        __getattr__(). What gets left is the stuff that the orchestrator should truly worry about, i.e.
        cluster level orchestration.
        """
        if item not in ['_attach_ebs_devices', '_establish_ssh_connection', '_run_bootstrap_sequence',
            '_generate_ssh_keys']:
            raise UnknownInstanceDefinitionMethod, \
                "Can not use the given method on all instance definitions: {0}.".format(item)

        def delegator():
            self._start_threads_and_wait(
                [Thread(target=getattr(instance, item[1:])) for instance in self.all_instances])

        # Cache the method
        setattr(self, item, delegator)

        return delegator

    def _start(self):
        """
        Spin up the instances.
        """
        spin_up_threads = [Thread(target=instance.start, args=(self._connection,)) for
            instance in self.all_instances]
        self._start_threads_and_wait(spin_up_threads)

    def _distribute_ssh_keys(self):
        """
        Take all the root and user keys and append to root authorized_keys.
        """
        all_root_pub_keys = [instance.root_pub_key for instance in self.all_instances]
        all_user_pub_keys = [instance.user_pub_key(instance.user) for instance in self.all_instances]
        all_keys = all_root_pub_keys + all_user_pub_keys
        distribute_ssh_key_threads = [Thread(target=instance.add_pub_keys, args=(all_keys,)) for
            instance in self.all_instances]
        self._start_threads_and_wait(distribute_ssh_key_threads)

    def _upload_cluster_facts(self):
        """
        Collect information from each instance and upload the aggregate set to all the nodes.
        """
        facts = {}
        for instance in self.all_instances:
            facts[instance.name] = instance.instance_facts()

        self._cluster_facts = facts

        fact_upload_threads = [Thread(target=instance.upload_cluster_facts, args=(facts,)) for
            instance in self.all_instances]
        self._start_threads_and_wait(fact_upload_threads)

    def _write_cluster_facts(self):
        """
        Dump the cluster facts to a file.
        """
        with open(expanduser('~/.cluster_facts.json'), 'w') as output:
            json.dump(self._cluster_facts, output)

    # Context manager methods.
    def __enter__(self):
        return self

    def __exit__(self, exc_type, *args, **kwargs):
        if exc_type is not None:
            return False

        self._go()

        return True

    def _preflight_checks(self):
        """
        Verify that everything is peachy.
        """
        pass

    def _go(self):
        """
        Run everything in the correct order to provision and bootstrap instances and pools.
        """
        self._instance_init()
        self._preflight_checks()
        logger.info("Spinning up instances.")
        self._start()
        logger.info("Waiting for instances to be ready.")
        self._wait_for_ready(30, 10)
        logger.info("Attaching any block devices.")
        self._attach_ebs_devices()
        logger.info("Waiting for SSH access.")
        self._establish_ssh_connection()
        logger.info("Generating SSH keys.")
        self._generate_ssh_keys()
        logger.info("Distributing SSH keys.")
        self._distribute_ssh_keys()
        logger.info("Uploading cluster facts.")
        self._upload_cluster_facts()
        logger.info("Writing cluster facts to local host as well: ~/.cluster_facts.json.")
        self._write_cluster_facts()
        logger.info("Running bootstrap sequence.")
        self._run_bootstrap_sequence()
