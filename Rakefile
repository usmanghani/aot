desc "Create and package the virtual environment as a tar file."
task :default => ['venv', 'install'] do |t|
  sh "tar cf orchestration.tar venv"
end

desc "Install the current module into the virtual environment."
task :install => ['venv'] do |t|
  sh "source venv/bin/activate; python setup.py install"
end

desc "Create the virtualenv and install all the requirements."
directory "venv" => ['clean'] do |t|
  sh "virtualenv venv; source venv/bin/activate; pip install -r requirements.txt"
end

desc "Do git clean before creating the venv."
task :clean do |t|
  sh "git clean -xfd"
end
