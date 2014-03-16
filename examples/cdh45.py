from orchestration.definitions import *
from orchestration.bootstrap_types import *
from orchestration.orchestrator import Orchestrator

logging.basicConfig(level=logging.INFO)

ssh_key_file = expanduser('~/.ssh/orchestration.pem')
security_groups, public_subnet, private_subnet = ['sg-45759b20'], 'subnet-1bc5c46f', 'subnet-d59aba93'

# These options are shared across regular instances and pool instances in this example but they don't have to be
# in the general case.
generic_options = dict(ami='ami-6cad335c', ssh_key='orchestration',
    security_groups=security_groups, owner='dkarapetyan', user='ubuntu', instance_profile_name='Get-Parcels',
    private_key_file=ssh_key_file)

scripts_root = '../../bootstrap-scripts'
# This is a common sequence used by all instances in this example.
common_sequence = [Tar(scripts_root + '/raid-ephemeral-disks/raid-ephemeral-disks.tar', []),
    Tar(scripts_root + '/hostname-fixer/hostname-fixer.tar', []),
    Tar(scripts_root + '/p-prerequisites/p-prereqs.tar', [])]

# The actual different sequences we need.
p_worker_sequence = common_sequence
manager_sequence = common_sequence + [Tar(scripts_root + '/cloudera-manager/cloudera-manager.tar', []),
    Tar(scripts_root + '/cloudera-node-prerequisites/cloudera-node-prereqs.tar', [])]
p_master_sequence = common_sequence + [Tar(scripts_root + '/p-master/p-master-setup.tar', [])]
cloudera_node_sequence = common_sequence + \
                         [Tar(scripts_root + '/cloudera-node-prerequisites/cloudera-node-prereqs.tar', [])]

# Now define all the instances and pools in the context of an orchestrator
with Orchestrator(aws_region='us-west-2') as orchestrator:
    # Cloudera manager node. Needs to be in public subnet.
    orchestrator.add_instance(name='cloudera-manager', ec2_size='m3.2xlarge',
        subnet=public_subnet, bootstrap_sequence=manager_sequence, **generic_options).auto_assign_ip()
    # P master node. Needs to be in public subnet.
    orchestrator.add_instance(name='p-master', ec2_size='m3.2xlarge',
        subnet=public_subnet, bootstrap_sequence=p_master_sequence, **generic_options).auto_assign_ip()
    # Hadoop nodes. Private subnet with NAT.
    orchestrator.add_pool(pool_name='hadoop', pool_size=2, instance_size='m3.2xlarge',
        subnet=private_subnet, bootstrap_sequence=cloudera_node_sequence, **generic_options)
    # P worker nodes. Same as hadoop nodes.
    orchestrator.add_pool(pool_name='p-worker', pool_size=2, instance_size='m3.2xlarge',
        subnet=private_subnet, bootstrap_sequence=p_worker_sequence, **generic_options)
