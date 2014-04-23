import json
import logging
import os
import paramiko
from os.path import expanduser
from time import sleep
from paramiko import SSHClient
from boto.ec2.blockdevicemapping import BlockDeviceMapping, BlockDeviceType
from boto.ec2.networkinterface import NetworkInterfaceSpecification, NetworkInterfaceCollection

# Logging boilerplate.
logger = logging.getLogger('definitions')

# Dynamically create the exception classes.
for error_class in ['InstanceStartedError', 'VolumeReadyError', 'NonRunningInstanceEbsAttachError',
    'SSHConnectionError']:
    globals()[error_class] = type(error_class, (Exception,), {})


class AutoIgnorePolicy(paramiko.MissingHostKeyPolicy):
    """
    Ignore all key related issues.
    """

    def missing_host_key(self, client, hostname, key):
        return


class EBS(object):
    """
    Specifies volume information that we want to attach to the instances.
    """

    def __init__(self, size, ebs_type, iops=None, snapshot=None):
        """
        Valid types for ebs_type are standard | io1. size is in GB and iops is what you would specify
        in AWS if you were creating a volume from the console. I/O blocks are 16kB and you specify how many
        of these you want by setting iops. Default iops is 100.
        """
        self.size, self.type = size, ebs_type
        if ebs_type != 'standard':
            self.iops = iops or 100
        else:
            self.iops = None
        self.snapshot = snapshot


def with_retry(retry_message, error_message, retry=10, sleep_time=10):
    """
    Abstracts retry functionality. This will call the function/method and catch any exceptions and call the
    function again until we reach retry_count at which point a fatal error is logged and we bail.
    """

    def decorator(fn):
        def decorated(self, *args, **kwargs):
            retry_counter = 0
            while True:
                try:
                    return fn(self, *args, **kwargs)
                except:
                    logger.error("Exception for instance = {0}.".format(self.name))
                    logging.exception('')
                    retry_counter += 1
                    if retry_counter > retry:
                        logger.fatal(error_message + " Instance = {0}.".format(self.name))
                        break
                    logger.debug(retry_message + " Instance = {0}.".format(self.name))
                    sleep(sleep_time)

        return decorated

    return decorator


class InstanceDefinition(object):
    """
    Instance specific data and methods, e.g. name, size, security groups, etc.
    """

    # Boto does not give us a way to get at the number of ephemeral devices for a given instance type so we need
    # to keep these values ourselves.
    EphemeralDriveCounts = {'m3.medium': 1, 'm3.large': 1, 'm3.xlarge': 2, 'm3.2xlarge': 2, 'cr1.8xlarge': 2,
        'hi1.4xlarge': 2, 'i2.xlarge': 1, 'i2.2xlarge': 2, 'i2.4xlarge': 4, 'i2.8xlarge': 8, 'hs1.8xlarge': 24,
        'c3.large': 2, 'c3.xlarge': 2, 'c3.2xlarge': 2, 'c3.4xlarge': 2, 'c3.8xlarge': 2, 'cc2.8xlarge': 4,
        'm1.small': 1, 't1.micro': 0, 'm1.medium': 1, 'm1.large': 2, 'm1.xlarge': 4}

    # We need drive letters when creating the ephemeral device mappings so we index into this string.
    DriveLetters = 'efghijklmnopqrstuvwxyz'

    def __init__(self, name, owner, ami, user, ec2_size, ssh_key, private_key_file, security_groups, subnet,
        instance_profile_name=None, bootstrap_sequence=None, hostname=None, ebs=None, placement_group=None):
        self.name, self.hostname = name, hostname
        self.owner, self.user = owner, user
        self.ami, self.ec2_size = ami, ec2_size
        self.ssh_key, self.placement_group = ssh_key, placement_group
        # Need to make sure private key file actually exists.
        if not os.path.isfile(expanduser(private_key_file)):
            raise Exception, "SSH key file does not exist: {0}.".format(private_key_file)

        self.private_key_file = private_key_file
        self.bootstrap_sequence = bootstrap_sequence or []
        self.instance_profile_name = instance_profile_name
        self.security_groups, self.subnet = security_groups, subnet
        self.ebs = ebs or []
        self.ephemeral_device_count = self.EphemeralDriveCounts.get(ec2_size, 0)
        if self.ephemeral_device_count == 0:
            error_message = "Unable to determine ephemeral device count for given instance size: {0}, {1}.".format(
                ec2_size, name)
            logger.error(error_message)

        # Variables that will be set when we call various lifecycle methods, e.g. start().
        self.instance, self.ssh_client, self.ebs_optimization = None, None, False
        self.interfaces, self.connection, self.existing_instances = None, None, None

    def auto_assign_ip(self):
        """
        Calling this method modifies network configuration for the instance so that the instance gets a public
        IP address.
        """
        interface = NetworkInterfaceSpecification(subnet_id=self.subnet, groups=self.security_groups,
            associate_public_ip_address=True)
        self.interfaces = NetworkInterfaceCollection(interface)

        return self

    def ebs_optimized(self):
        """
        Call this to make the provisioning of this instance ebs optimized.
        """
        self.ebs_optimization = True

        return self

    def instance_facts(self):
        """
        After all the instances are spun up and ready to go we gather and plop down a fact file in {json,yaml} format
        so that the bootstrap scripts can be aware of the cluster in terms of names, ip addresses, and some other
        basic facts.
        """
        base_facts = dict(name=self.name, subnet=self.subnet, main_user=self.user, owner=self.owner)
        try:
            facts = dict(ip_address=self.instance.private_ip_address, hostname=self.ssh_command('hostname').strip(),
                **base_facts)
        except:
            logger.exception('')
            logger.fatal("Unable to gather complete set of facts for instance: {0}.".format(self.name))
            logger.info("Falling back to base set of facts: {0}.".format(self.name))
            facts = base_facts

        return facts

    def upload_cluster_facts(self, facts):
        """
        Converts facts to {yaml, json} and upload to /etc/cluster_facts.{yaml, json} so that bootstrap script
        can use those facts for configuration.
        """
        json_facts = json.dumps(facts).replace("'", "\\'").replace('"', '\\"')
        self.ssh_command("echo {0} | sudo tee -a /etc/cluster_facts.json".format(json_facts))

        return self

    def _initial_block_device_mapping(self):
        """
        Set up the ephemeral block device mapping.
        """
        block_device_mapping = BlockDeviceMapping()
        for i in range(0, self.ephemeral_device_count):
            block_device = BlockDeviceType(ephemeral_name='ephemeral' + str(i))
            drive_name = '/dev/sd{0}'.format(self.DriveLetters[i])
            block_device_mapping[drive_name] = block_device

        return block_device_mapping

    @with_retry("Failed to add tags. Re-trying after timeout.", "Could not add {Name, Owner} tags to instance.")
    def _add_tags(self):
        self.instance.add_tag('Name', self.name)
        self.instance.add_tag('Owner', self.owner)

    def start(self, connection):
        """
        Fire up an EC2 instance with the given configuration and block device mapping. Any number of things can
        go wrong here. From being unable to tag the instance to not having an instance. Instead of handling all
        those edge cases we let downstream methods deal with the issue. Little to no error recovery is ok as long
        as the rest of the process can continue. The orchestrator uses threads so the main thread doesn't crash
        when the threads doing the provisioning die with an exception.
        """
        if self.instance:
            raise InstanceStartedError, "Can not call start twice on a single instance."

        self.connection, image = connection, connection.get_image(self.ami)
        block_device_mapping = self._initial_block_device_mapping()
        common_options = dict(image_id=image.id, min_count=1, max_count=1, key_name=self.ssh_key,
            instance_type=self.ec2_size, ebs_optimized=self.ebs_optimization, block_device_map=block_device_mapping,
            placement_group=self.placement_group, instance_profile_name=self.instance_profile_name)

        if self.interfaces is None:
            self.instance = connection.run_instances(security_group_ids=self.security_groups, subnet_id=self.subnet,
                **common_options).instances[0]
        else:  # Should only happen if someone called auto_assign_ip()
            self.instance = connection.run_instances(network_interfaces=self.interfaces, **common_options).instances[0]

        self._add_tags()

        return self

    @with_retry("Retrying to tag EBS volume after timeout.", "Unable to add tags to EBS volume.")
    def _tag_volume(self, volume): volume.add_tag('Name', self.name)

    @with_retry("Volume not available.", "Volume did not become available.")
    def _wait_for_volume(self, volume):
        if volume.update() != 'available':
            raise VolumeReadyError, "Volume is not ready: {0}.".format(self.name)

    def attach_ebs_devices(self):
        """
        Once the instance has started attach any configured EBS volumes. Waiting for the instance to
        start happens upstream of this method and an exception is raised if the instance we get is not running
        already.
        """
        if self.state != 'running':
            message = "Can not attach an EBS volume to an instance that is not running: {0}.".format(self.name)
            raise NonRunningInstanceEbsAttachError, message

        for i in range(0, len(self.ebs)):
            ebs_definition = self.ebs[i]
            volume = self.connection.create_volume(size=ebs_definition.size, zone=self.instance.placement,
                snapshot=ebs_definition.snapshot, volume_type=ebs_definition.type, iops=ebs_definition.iops)

            self._wait_for_volume(volume)
            self._tag_volume(volume)
            volume.attach(self.instance.id, '/dev/sd{0}'.format(self.DriveLetters[self.ephemeral_device_count + i]))

    @property
    def state(self):
        """
        Used by various methods to figure out how to proceed. We can't do anything unless the instance is running.
        """
        try:
            return self.instance.update()
        except:
            logger.exception('')
            return 'unknown state'

    def instantiate_ssh_client(self):
        """
        Without SSH access we can't provision the instance.
        """
        self.ssh_client = SSHClient()
        self.ssh_client.set_missing_host_key_policy(AutoIgnorePolicy())
        self.ssh_client.connect(hostname=self.instance.private_ip_address, username=self.user, timeout=10,
            key_filename=self.private_key_file)

        return self

    @with_retry("Retrying SSH connection after timeout.", "SSH connection error.", sleep_time=60)
    def establish_ssh_connection(self):
        """
        See if we can connect and give up after X number of retries.
        """
        private_ip_address, name = self.instance.private_ip_address, self.name
        logger.info("Establishing SSH connection: {0} - {1}.".format(private_ip_address, name))
        self.instantiate_ssh_client()

        return self

    def ssh_command(self, command):
        """
        Hack around not being able to do stuff because of pty issues with sudo. Note that we need to
        wait for the command to return some kind of exit code. If we don't have the exit code and call recv()
        too soon then the process will die prematurely. This was a source of a major headache before I figured it out.
        We set keepalive to 10s on the transport to keep the session open because we need it for some long
        lived sessions.
        """
        transport = self.ssh_client.get_transport()
        transport.set_keepalive(10)
        chan = transport.open_session()
        chan.get_pty(width=800, height=600)
        chan.exec_command(command)
        logger.debug('Executing command: {0}.'.format(command))
        while not chan.exit_status_ready():
            sleep(1)

        result = chan.recv(4000000)
        chan.close()

        return result


    def generate_ssh_keys(self):
        """
        We need to generate ssh keys and distribute them for all sorts of reasons. This needs to happen ahead
        of any bootstrap scripts because the bootstrap scripts themselves might want SSH access to other cluster hosts.
        We need to be careful to not regenerate keys if the image we are using already comes with keys pre-generated.
        """
        key_gen_template = 'echo -e "\n\n\n" | sudo -u {0} -H -i bash -c ' + \
                           '"if [[ ! -f ~/.ssh/id_rsa.pub ]]; then ssh-keygen -t rsa -N \'\' -C \'{0} key\'; fi"'

        for cmd in [key_gen_template.format('root'), key_gen_template.format(self.user)]:
            self.ssh_command(cmd)

        return self

    def user_pub_key(self, user):
        """
        Key distribution is necessary for hadoop clusters. Like root_pub_key() this method will also return
        bogus keys so that the orchestrator can proceed with the rest of the process.
        """
        command = "sudo -u {0} -H -i bash -c 'cat ~/.ssh/id_rsa.pub'".format(user)
        result, name = "ssh-rsa asdf", self.name
        try:
            result = self.ssh_command(command)
        except:
            logger.exception('')
            logger.fatal("Unable to get public SSH key: user = {0}, instance = {1}.".format(user, name))
            logger.info("Falling back to bogus public SSH key: {0}.".format(name))

        return result[result.find("ssh-"):]

    @property
    def root_pub_key(self):
        """
        This property always return a result even if it is bogus. The reason is that key distribution is a global
        issue and is handled by the orchestrator so it is better to return a bogus key and let the orchestrator
        continue rather than kill the process at the key distribution step.
        """
        return self.user_pub_key('root')

    def add_pub_keys(self, keys):
        """
        Once we have all the keys, bogus or otherwise, we distribute them with this method. Keys are handed to us
        from the orchestrator because we don't have access to the other instances from here.
        """
        for key in keys:
            self.ssh_command("sudo -u root -H -i bash -c 'echo {0} >> ~/.ssh/authorized_keys'".format(key))

        return self

    def run_bootstrap_sequence(self):
        """
        When all the keys and everything else has been properly placed on the instances we run the bootstrap
        scripts. Most bootstrap scripts depend on knowing cluster facts and having remote root ssh access to other
        nodes in the cluster. Bootstrap script errors will happen if bogus keys get distributed.
        """
        for i in range(0, len(self.bootstrap_sequence)):
            self.bootstrap_sequence[i].execute(self.ssh_client, i)

        return self


class Pool(object):
    """
    A pool is just a convenient wrapper around a collection of instances that share common settings. The names
    of the instances in the pool are appended with numbers starting at 0.
    """

    class PoolInstancesAccessor(object):
        """
        For maintaining backwards compatibility it is easier to provide some indirection
        to instance definition access so that we can muck around with instance creation.
        """

        def __get__(self, obj, _):
            if obj._instance_definitions:
                return obj._instance_definitions

            obj._instance_definitions = []
            for i in range(0, obj.pool_size):
                instance_definition = InstanceDefinition(name=obj.pool_name + str(i), ami=obj.ami, owner=obj.owner,
                    ec2_size=obj.instance_size, ssh_key=obj.ssh_key, bootstrap_sequence=obj.bootstrap_sequence,
                    security_groups=obj.security_groups, subnet=obj.subnet, ebs=obj.ebs, user=obj.user,
                    private_key_file=obj.private_key_file, placement_group=obj.placement_group,
                    instance_profile_name=obj.instance_profile_name)
                obj._instance_definitions.append(instance_definition)

            return obj._instance_definitions

    instance_definitions = PoolInstancesAccessor()

    def __init__(self, pool_name, owner, ami, user, instance_size, pool_size, ssh_key, private_key_file,
        security_groups, subnet, placement_group=None, bootstrap_sequence=None, ebs=None, instance_profile_name=None):
        self.pool_name, self.owner = pool_name, owner
        self.ami, self.user = ami, user
        self.placement_group, self.instance_profile_name = placement_group, instance_profile_name
        self.instance_size, self.pool_size = instance_size, pool_size or 1
        self.ssh_key, self.private_key_file = ssh_key, private_key_file
        self.bootstrap_sequence = bootstrap_sequence or []
        self.security_groups, self.subnet = security_groups, subnet
        self.ebs = ebs or []
        self._instance_definitions = None

    def __getattr__(self, item):
        """
        For pool instances we want to basically delegate all methods down to the instances belonging to this
        pool.
        """

        def instance_delegator(*args, **kwargs):
            for instance in self._instance_definitions:
                getattr(instance, item)(*args, **kwargs)

            return self

        # Cache the method so that further lookups do not fall through to __getattr__
        setattr(self, item, instance_delegator)

        return instance_delegator
