from orchestration.definitions import *
from orchestration.bootstrap_types import *
from orchestration.orchestrator import Orchestrator

logging.basicConfig(level=logging.INFO)

private_key = expanduser('~/.ssh/orchestration.pem')
security_groups, public_subnet, private_subnet = ['sg-45759b20'], 'subnet-1bc5c46f', 'subnet-d59aba93'

# These options are shared across regular instances and pool instances in this example but they don't have to be.
generic_options = dict(ami='ami-ace67f9c', ssh_key='orchestration',
    security_groups=security_groups, owner='dkarapetyan', user='ubuntu', instance_profile_name='Get-Parcels',
    private_key_file=private_key)

scripts_root = '../../bootstrap-scripts'
sequence = [Tar(scripts_root + '/python27-virtualenv/python27-virtualenv.tar', [])]

# Now define all the instances and pools in the context of an orchestrator
with Orchestrator(aws_region='us-west-2') as orchestrator:
    # public micro
    orchestrator.add_instance(name='test-public', ec2_size='t1.micro',
        subnet=public_subnet, bootstrap_sequence=sequence, **generic_options).auto_assign_ip()
    # private micro
    orchestrator.add_instance(name='test-private', ec2_size='t1.micro',
        subnet=private_subnet, bootstrap_sequence=sequence, **generic_options)
