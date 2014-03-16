from setuptools import setup, find_packages

with open('requirements.txt') as f:
    requirements = f.read()

setup(name='hadoop-orchestration', version='1.0',
    packages=find_packages(), url='', license='MIT',
    author='dkarapetyan', author_email='dkarapetyan@gmail.com',
    description='Some utility classes wrapping Paramiko and Boto for creating simple clusters in AWS.',
    install_requires=requirements)
