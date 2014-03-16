# Overview
There are four stages to the orchestration process:

  * Instance/Pool creation and Block device mapping configuration
  * SSH key generation and cluster fact collection
  * SSH key and cluster facts distribution
  * Bootstrap sequence execution for each instance/pool

Each step is meant to be idempotent to aid error recovery but for the time being if the orchestration
process is halted then the processes is started anew and the old instances and block devices are
left intact. The only thing that carries over are the instance and block device names so that
they can be easily identified in the AWS console. 
follows.

# Example Cluster Configuration
Look in `examples`. Pretty straightforward.

## Dependencies

  * python2.7
  * pip
  * virtualenv

## Installation

  * `virtualenv venv`
  * `source venv/bin/activate`
  * `pip install -r requirements.txt`

## Common Configuration Options and Instance/Pool Definitions
Each instance requires a set of parameters. It is the same exact set of parameters that you
would need to provide if you were going to spin up the instances from the AWS interactive console.
The parameters are: 

  * AMI = `ami`
  * SSH key pair = `ssh_key`
  * Network security groups = `security_groups`
  * VPC subnet = `subnet`
  * User the instance belongs to = `owner`
  * SSH user that will be executing bootstrapping commands = `user`
  * Extra EBS volumes = `ebs`

Each of the above options can be customized per instance and per pool but you more than likely
want to stick to a generic set because then you don't have to worry about cross subnet networking
and SSH key pair issues.

The other options are more instance and pool specific:

  * Name of instance, instances in a pool get appended with 1, 2, 3, etc.
  * EC2 instance size = `ec2_size`
  * Private SSH key file = `private_key_file`
  * Bootstrap sequence (will be explained below) = `bootstrap_sequence`

# Bootstrap Sequence
Right now the only supported type of bootstrap definition is the `Tar` class and it points to
a local tar file. The tar file when expanded on the remote instance has to satisfy a very simple 
contract. It must include a file called `bootstrap.sh` that is executable with `bash`, i.e. `
bash bootstrap.sh`. The tar files are expanded into directories in the order that they are 
provided called `stage-0`, `stage-1`, etc. Once expanded the bootstrap scripts are executed 
with root privileges and can do anything that's necessary to configure the instance. During
the SSH key generation and distribution step the root keys for each host are distributed to 
every other host and the facts for the entire cluster are written to `/etc/cluster_facts.json`. 
The cluster facts contain a mapping from instance names to a set of facts for that instance 
which includes the name of the instance along with IP address and few other things. This means 
that a bootstrapping script can use the root user to access all the other hosts remotely 
because the SSH keys have been distributed and the IP addresses provided in 
`/etc/cluster_facts.json`.

The above mechanism is general and quite flexible because it does not constrain you in any way. 
If from the bootstrap script you want to use ansible/chef/puppet or some other configuration 
management tool then you can simply execute the required recipes and definitions from the 
bootstrap script as long as you bundle your recipes in the tar file.

# Running Orchestration Scripts
Once you have your instances and pools defined in a python file then spinning up your cluster
is as simple as `python config.py`.
