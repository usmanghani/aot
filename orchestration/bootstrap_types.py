import logging
import os
from time import sleep
from paramiko import SFTPClient

# Logging boilerplate.
logger = logging.getLogger('bootstrap_types')

class BootstrapTarError(Exception): pass


class BootstrapType(object):

    @staticmethod
    def execute_command(command, client):
        logger.info('Executing command: {0}.'.format(command))
        transport = client.get_transport()
        transport.set_keepalive(10)
        chan = transport.open_session()
        chan.get_pty(width=800, height=600)
        chan.exec_command(command)
        while not chan.exit_status_ready():
            sleep(1)

        exit_status = chan.exit_status
        result = chan.recv(1000000)
        chan.close()
        return [result, exit_status]


class Tar(BootstrapType):
    """
    The tar bootstrap type is a tar file that will be copied to the remote machine and then
    unpacked. The tarball must contain bootstrap.sh because that's what will be executed. The bootstrap
    script instance can also take a list of arguments that will be passed to bootstrap.sh as command line parameters.
    """

    def __init__(self, tarfile, args):
        self.tarfile, self.args = tarfile, args
        if not os.path.exists(tarfile):
            raise BootstrapTarError, "Can not find the given file: {0}.".format(tarfile)

    def execute(self, client, stage_number):
        """
        Move the tar file into place, unpack and run bootstrap.sh passing any given arguments.
        """
        sftp = SFTPClient.from_transport(client.get_transport())
        stage_directory = 'stage-' + str(stage_number)
        try:
            sftp.mkdir(stage_directory)
        except:
            logger.exception('')
        sftp.chdir(stage_directory)

        # Stage complete so nothing to do.
        if 'stage-complete' in sftp.listdir():
            return

        # upload, untar, execute bootstrap.sh
        sftp.put(self.tarfile, '{0}/{1}.tar'.format(sftp.getcwd(), stage_directory))
        untar = 'cd {0} && tar xf {1}.tar'.format(stage_directory, stage_directory)
        untar_result = self.execute_command(untar, client)
        logger.debug('Command results: {0}.'.format(untar_result))

        logger.info('Executing contents of tar file: {0}.'.format(self.tarfile))
        bootstrap_arguments = ' '.join(self.args)
        run_bootstrap = 'cd {0} '.format(
            stage_directory) + '&& sudo -u root -H bash -l -c "bash bootstrap.sh {0}" > output '.format(
            bootstrap_arguments) + '&& touch stage-complete'
        bootstrap_result = self.execute_command(run_bootstrap, client)
        if bootstrap_result[1] != 0:
            logger.error("Stage did not complete: {0}.".format(stage_number))

        logger.debug('Command results: {0}.'.format(bootstrap_result[0]))