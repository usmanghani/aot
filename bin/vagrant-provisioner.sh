#!/bin/bash

apt-get -q update
apt-get -q -y upgrade
apt-get -y autoremove
apt-get -q -y --no-install-recommends install wget curl build-essential python2.7 python2.7-dev expect git htop \
  libreadline6-dev zlib1g-dev libssl-dev libyaml-dev libsqlite3-dev sqlite3 autoconf libgdbm-dev libncurses5-dev \
  automake libtool bison pkg-config libffi-dev

# easy_install, pip, and virtualenv
if [[ ! -f setuptools-2.2.tar.gz ]]; then
  echo "Setuptools not found. Installing."
  wget https://bitbucket.org/pypa/setuptools/raw/bootstrap/ez_setup.py -O - | python2.7
  easy_install-2.7 pip
  pip2.7 install virtualenv
fi

# sneak in ruby stuff as well
if [[ ! $(which rvm) ]]; then
  echo "RVM not found. Installing."
  curl -sSL https://get.rvm.io | bash -s stable
  source /usr/local/rvm/scripts/rvm
  rvm install ruby
  rvm use ruby
  gem install fpm --no-ri --no-rdoc
fi

if [[ ! -d orchestration ]]; then
  echo "Copying /vagrant/\* files to $(pwd)."
  cp -r /vagrant/* .
  virtualenv venv
  source venv/bin/activate
  pip install -r requirements.txt
fi